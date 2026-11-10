"""CS 457 version-one wire messages and nonblocking TCP transport."""

from collections import deque
from dataclasses import dataclass
import json
import re
import selectors
import socket
import sys
import time


DEFAULT_HOST = "server.barton.edu"
DEFAULT_PORT = 45700
FRAME_LIMIT = 4096
IO_BUDGET = 5
GRACE_SECONDS = 30
PROBE_INTERVAL = 5
PROBE_BUDGET = 10
PLAYER_IDS = ("P1", "P2")
PHASE_ACTIONS = {
    "TURN_CHOICE": ("TRIGGER", "PASS"),
    "FORCED_REPLY": ("TRIGGER",),
    "FORCED_RETURN": ("TRIGGER",),
    "LOAD": ("LOAD", "END_TURN"),
}
PLAYING = tuple(PHASE_ACTIONS)
PHASES = (*PLAYING, "WAITING_FOR_PLAYERS", "PAUSED", "GAME_OVER")
GAMEPLAY_EVENTS = ("PASSED", "SURVIVED", "TURN_READY", "MATCH_ENDED")
EVENTS = (*GAMEPLAY_EVENTS, "SNAPSHOT", "MATCH_STARTED", "CONNECTION_LOST", "RESUMED")
RESULTS = ("SHOT", "FORFEIT", "RECONNECT_TIMEOUT", "MATCH_ABORTED")
FATAL_ERRORS = {
    "FRAME_TOO_LARGE", "UNSUPPORTED_VERSION", "HANDSHAKE_REQUIRED", "ROOM_FULL", "RESUME_DENIED",
}
ERROR_CODES = FATAL_ERRORS | {
    "MALFORMED_FRAME", "INVALID_SCHEMA", "MATCH_NOT_ACTIVE", "MATCH_PAUSED", "STALE_STATE",
    "STALE_TURN", "NOT_YOUR_TURN", "ACTION_NOT_ALLOWED", "LOAD_LIMIT",
}


class ProtocolError(Exception):
    def __init__(self, code, detail, message_type=None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.message_type = message_type
        self.fatal = code in FATAL_ERRORS


def message(msg_type, **payload):
    return {"version": 1, "msg_type": msg_type, "payload": payload}


def error_message(error):
    return message("ERROR", code=error.code, detail=error.detail, fatal=error.fatal)


def move(action, turn_id, state_revision, count=None):
    frame = message("MOVE", action=action)
    frame.update(turn_id=turn_id, state_revision=state_revision)
    if count is not None:
        frame["payload"]["count"] = count
    return frame


def encode(frame):
    data = json.dumps(frame, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > FRAME_LIMIT:
        raise ValueError("Generated message exceeds the frame limit")
    return data + b"\n"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant {value}")


NEGATIVE_ZERO = object()


def integer_literal(value):
    return NEGATIVE_ZERO if value == "-0" else int(value)


class Framer:
    def __init__(self):
        self.buffer = bytearray()

    def append(self, data):
        self.buffer.extend(data)

    def pop(self):
        end = self.buffer.find(b"\n")
        if (len(self.buffer) if end < 0 else end) > FRAME_LIMIT:
            raise ProtocolError("FRAME_TOO_LARGE", "A message exceeds 4096 bytes.")
        if end < 0:
            return None
        line = bytes(self.buffer[:end])
        del self.buffer[:end + 1]
        try:
            if not line or b"\r" in line or line.endswith((b" ", b"\t")):
                raise ValueError("Invalid line framing")
            frame = json.loads(line.decode("utf-8"), object_pairs_hook=unique_object,
                               parse_constant=reject_constant, parse_int=integer_literal)
            if not isinstance(frame, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, RecursionError) as error:
            raise ProtocolError("MALFORMED_FRAME", "Expected one UTF-8 JSON object per line.") from error
        return frame


def fields(value, *names):
    return isinstance(value, dict) and value.keys() == set(names)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def identifier(value, length):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]+", value) is not None and len(value) == length


def allowed_actions(phase, active, player, inventory):
    if active != player:
        return ()
    if phase == "LOAD" and not inventory:
        return ("END_TURN",)
    return PHASE_ACTIONS.get(phase, ())


def snapshot_valid(payload):
    if not fields(payload, "match_id", "state_revision", "phase", "resume_phase", "turn_id",
                  "active_player_id", "allowed_actions", "self", "players", "event", "reconnect_remaining_ms"):
        return False
    phase, resume, turn, active = (payload[key] for key in ("phase", "resume_phase", "turn_id", "active_player_id"))
    own, players = payload["self"], payload["players"]
    remaining = payload["reconnect_remaining_ms"]
    if (not identifier(payload["match_id"], 32) or not integer(payload["state_revision"])
            or phase not in PHASES or payload["event"] not in EVENTS
            or not fields(own, "player_id", "inventory") or own["player_id"] not in PLAYER_IDS
            or not integer(own["inventory"]) or not isinstance(players, list) or not 1 <= len(players) <= 2
            or (remaining is not None and not integer(remaining))):
        return False
    if not all(fields(player, "player_id", "connection") and player["player_id"] in PLAYER_IDS
               and player["connection"] in ("ONLINE", "DISCONNECTED", "LEFT") for player in players):
        return False
    seats = [player["player_id"] for player in players]
    if seats != sorted(set(seats)) or own["player_id"] not in seats:
        return False
    if phase == "PAUSED":
        if resume not in PLAYING:
            return False
    elif resume is not None:
        return False
    if phase in (*PLAYING, "PAUSED"):
        if not integer(turn, 1) or active not in PLAYER_IDS or len(seats) != 2:
            return False
    elif turn is not None or active is not None:
        return False
    if phase == "GAME_OVER" and (remaining is not None or len(seats) != 2):
        return False
    return payload["allowed_actions"] == list(allowed_actions(phase, active, own["player_id"], own["inventory"]))


def result_valid(payload):
    if (not fields(payload, "match_id", "state_revision", "winner_id", "loser_id", "reason")
            or not identifier(payload["match_id"], 32) or not integer(payload["state_revision"])
            or payload["reason"] not in RESULTS):
        return False
    winner, loser = payload["winner_id"], payload["loser_id"]
    if payload["reason"] == "MATCH_ABORTED":
        return winner is None and loser is None
    return winner in PLAYER_IDS and loser in PLAYER_IDS and winner != loser


def error_valid(payload):
    if not fields(payload, "code", "detail", "fatal"):
        return False
    code, detail, fatal = payload["code"], payload["detail"], payload["fatal"]
    return (isinstance(code, str) and code in ERROR_CODES and isinstance(detail, str)
            and 1 <= len(detail) <= 160 and fatal is (code in FATAL_ERRORS))


def move_valid(frame, payload):
    if not integer(frame["turn_id"], 1) or not integer(frame["state_revision"]):
        return False
    action = payload.get("action")
    if action == "LOAD":
        return fields(payload, "action", "count") and integer(payload["count"], 1)
    return fields(payload, "action") and action in ("TRIGGER", "PASS", "END_TURN")


PAYLOAD_RULES = {
    "CONNECT": lambda p: fields(p),
    "RECONNECT": lambda p: fields(p, "match_id", "session_token") and identifier(p["match_id"], 32)
    and identifier(p["session_token"], 64),
    "PING": lambda p: fields(p, "probe_id") and integer(p["probe_id"], 1),
    "PONG": lambda p: fields(p, "probe_id") and integer(p["probe_id"], 1),
    "WELCOME": lambda p: fields(p, "match_id", "player_id", "session_token", "resumed")
    and identifier(p["match_id"], 32) and p["player_id"] in PLAYER_IDS
    and identifier(p["session_token"], 64) and type(p["resumed"]) is bool,
    "LOBBY_WAIT": lambda p: fields(p, "match_id", "connected_players", "required_players")
    and identifier(p["match_id"], 32) and integer(p["connected_players"], 1)
    and p["connected_players"] <= 2 and integer(p["required_players"], 2) and p["required_players"] == 2,
    "GAME_START": lambda p: fields(p, "match_id", "first_player_id") and identifier(p["match_id"], 32)
    and p["first_player_id"] in PLAYER_IDS,
    "STATE_UPDATE": snapshot_valid,
    "GAME_OVER": result_valid,
    "ERROR": error_valid,
}
CLIENT_TYPES = {"CONNECT", "RECONNECT", "MOVE", "DISCONNECT", "PING", "PONG"}
SERVER_TYPES = (set(PAYLOAD_RULES) - {"CONNECT", "RECONNECT"}) | {"DISCONNECT"}


def validate(frame, sender):
    kind = frame.get("msg_type")
    if not isinstance(kind, str):
        raise ProtocolError("INVALID_SCHEMA", "Missing or invalid message type.")
    version = frame.get("version")
    if type(version) is int and version != 1:
        raise ProtocolError("UNSUPPORTED_VERSION", "Only protocol version 1 is supported.", kind)
    payload = frame.get("payload")
    keys = ("version", "msg_type", "payload", "turn_id", "state_revision") if kind == "MOVE" else (
        "version", "msg_type", "payload",
    )
    if type(version) is int and fields(frame, *keys) and isinstance(payload, dict):
        if kind == "MOVE":
            valid = move_valid(frame, payload)
        elif kind == "DISCONNECT":
            valid = fields(payload) if sender == "client" else (
                fields(payload, "reason") and payload["reason"] in ("CLIENT_REQUEST", "MATCH_CLOSED")
            )
        else:
            rule = PAYLOAD_RULES.get(kind)
            valid = rule is not None and rule(payload)
        if valid:
            return kind
    raise ProtocolError("INVALID_SCHEMA", "The message has missing or invalid fields.", kind)


@dataclass(eq=False)
class Outbound:
    data: bytes
    kind: str
    deadline: float
    offset: int = 0


@dataclass
class Probe:
    identifier: int
    frame: Outbound
    revision: int
    deadline: float = None
    completed: bool = False


class Peer:
    """One transport generation. Its owner serializes I/O and timer checks."""

    def __init__(self, sock, clock=time.monotonic):
        self.sock = sock
        self.clock = clock
        self.input = Framer()
        self.output = deque()
        self.next_tick = None
        self.next_probe_id = 1
        self.probe = None
        self.close_deadline = None
        self.read_eof = False
        self.write_eof = False

    @property
    def closing(self):
        return self.close_deadline is not None

    def queue(self, frame):
        pending = Outbound(encode(frame), frame["msg_type"], self.clock() + IO_BUDGET)
        self.output.append(pending)
        return pending

    def start_heartbeats(self, now):
        self.next_tick = now + PROBE_INTERVAL

    def stop_heartbeats(self):
        self.next_tick = None
        if self.probe is not None and self.probe.frame.offset == 0:
            self.output.remove(self.probe.frame)
        self.probe = None

    def tick(self, now, revision=-1):
        if self.next_tick is None or now < self.next_tick:
            return
        intervals = (now - self.next_tick) // PROBE_INTERVAL + 1
        self.next_tick += intervals * PROBE_INTERVAL
        if self.probe is None:
            identifier = self.next_probe_id
            self.next_probe_id += 1
            frame = self.queue(message("PING", probe_id=identifier))
            self.probe = Probe(identifier, frame, revision)

    def complete_probe(self):
        probe = self.probe
        if probe is None:
            return
        if probe.frame.offset == 0:
            self.output.remove(probe.frame)
        elif probe.frame.offset < len(probe.frame.data):
            probe.completed = True
            probe.deadline = None
            return
        self.probe = None

    def pong(self, identifier):
        if self.probe is not None and self.probe.identifier == identifier and self.probe.frame.offset:
            self.complete_probe()

    def flush_one(self):
        if not self.output:
            return
        frame = self.output[0]
        try:
            sent = self.sock.send(frame.data[frame.offset:])
        except (BlockingIOError, InterruptedError):
            return
        if sent == 0:
            raise ConnectionError("Socket write made no progress")
        frame.offset += sent
        if frame.offset == len(frame.data):
            self.output.popleft()
            if self.probe is not None and self.probe.frame is frame:
                if self.probe.completed:
                    self.probe = None
                else:
                    self.probe.deadline = self.clock() + PROBE_BUDGET

    def discard_unsent(self, kinds=None):
        self.output = deque(frame for frame in self.output if frame.offset or (
            kinds is not None and frame.kind not in kinds
        ))

    def begin_close(self, now):
        self.stop_heartbeats()
        if not self.closing:
            self.close_deadline = now + IO_BUDGET

    def shutdown_writes(self):
        if self.closing and not self.write_eof and not self.output:
            self.sock.shutdown(socket.SHUT_WR)
            self.write_eof = True

    def close(self):
        self.input.buffer.clear()
        self.output.clear()
        self.probe = self.next_tick = None
        if not self.write_eof:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError as error:
                print(f"Socket shutdown during cleanup failed ({error.errno}).", file=sys.stderr)
        try:
            self.sock.close()
        except OSError as error:
            print(f"Socket close failed ({error.errno}).", file=sys.stderr)

    def read(self):
        try:
            data = self.sock.recv(FRAME_LIMIT)
        except (BlockingIOError, InterruptedError):
            return None
        if not data:
            self.read_eof = True
        return data

    def interests(self):
        events = 0
        if not self.read_eof and (self.closing or len(self.output) < 64):
            events |= selectors.EVENT_READ
        if self.output:
            events |= selectors.EVENT_WRITE
        return events

    def output_deadline(self):
        return self.output[0].deadline if self.output else None

    def probe_deadline(self):
        return self.probe.deadline if self.probe is not None else None

    def deadlines(self):
        return (self.output_deadline(), self.probe_deadline(), self.next_tick, self.close_deadline)
