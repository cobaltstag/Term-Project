"""Run the authoritative two-player CS 457 Russian Roulette server."""

import argparse
from dataclasses import dataclass
import math
import secrets
import selectors
import socket
import sys
import time

from protocol import (
    CLIENT_TYPES, GRACE_SECONDS, IO_BUDGET, PHASE_ACTIONS,
    PLAYER_IDS, PLAYING, Peer, ProtocolError, allowed_actions, error_message,
    message, validate,)


REWARDS = (1, 2, 4, 5)
LOBBY = "WAITING_FOR_PLAYERS"
# Count unbound and closing sockets so neither can exhaust the selector.
CONNECTION_LIMIT = 32


def opponent(player):
    return PLAYER_IDS[1] if player == PLAYER_IDS[0] else PLAYER_IDS[0]


@dataclass
class Seat:
    """Retain a player's private state across connection generations."""

    player: str
    token: str
    peer: object
    status: str = "ONLINE"
    grace: float = None
    inventory: int = 0
    reward_index: int = 0


class Match:
    """Own the authoritative state of one two-player match."""

    def __init__(self, rng):
        self.rng = rng
        self.id = secrets.token_hex(16)
        self.seats = {}
        self.phase = LOBBY
        self.revision = 0
        self.turn = None
        self.active = None
        self.saved_phase = None
        self.dare_initiator = None
        self.load_context = None
        self.cylinder = [False] * 6
        self.result = None

    def start(self):
        self.cylinder[self.rng.randrange(6)] = True
        self.active = self.rng.choice(PLAYER_IDS)
        self.turn = 1
        self.phase = "TURN_CHOICE"
        self.revision += 1

    def snapshot(self, seat, event, now):
        """Include only the recipient's private inventory."""
        grace = min(
            (player.grace for player in self.seats.values()
             if player.grace is not None),
            default=None,
        )
        remaining = (
            None if grace is None else max(0, math.ceil((grace - now) * 1000))
        )
        actions = allowed_actions(
            self.phase, self.active, seat.player, seat.inventory
        )
        return message(
            "STATE_UPDATE",
            match_id=self.id,
            state_revision=self.revision,
            phase=self.phase,
            resume_phase=self.saved_phase,
            turn_id=self.turn,
            active_player_id=self.active,
            allowed_actions=list(actions),
            self={"player_id": seat.player, "inventory": seat.inventory},
            players=[
                {"player_id": player, "connection": self.seats[player].status}
                for player in PLAYER_IDS if player in self.seats
            ],
            event=event,
            reconnect_remaining_ms=remaining,
        )

    def finish(self, reason, winner=None, loser=None):
        self.phase = "GAME_OVER"
        self.turn = self.active = self.saved_phase = None
        self.load_context = self.dare_initiator = None
        self.revision += 1
        for seat in self.seats.values():
            seat.token = None
            seat.grace = None
        self.result = message(
            "GAME_OVER", match_id=self.id, state_revision=self.revision,
            winner_id=winner, loser_id=loser, reason=reason)

    def check_move(self, seat, frame):
        """Check turn ownership, revision, and action before play."""
        if self.phase == "PAUSED":
            raise ProtocolError(
                "MATCH_PAUSED",
                "The match is paused while a player reconnects.",
                "MOVE")
        if self.phase not in PLAYING:
            raise ProtocolError(
                "MATCH_NOT_ACTIVE",
                "The match has not started.",
                "MOVE")
        if frame["state_revision"] != self.revision:
            raise ProtocolError(
                "STALE_STATE",
                "The game state changed. Use the current choices.",
                "MOVE")
        if frame["turn_id"] != self.turn:
            raise ProtocolError(
                "STALE_TURN",
                "The turn changed. Use the current choices.",
                "MOVE")
        if seat.player != self.active:
            raise ProtocolError("NOT_YOUR_TURN", "Wait for your turn.", "MOVE")
        payload = frame["payload"]
        if payload["action"] not in PHASE_ACTIONS[self.phase]:
            raise ProtocolError(
                "ACTION_NOT_ALLOWED",
                "Choose an action offered for this turn.",
                "MOVE")
        if payload["action"] == "LOAD" and payload["count"] > min(
                seat.inventory, self.cylinder.count(False)):
            raise ProtocolError(
                "LOAD_LIMIT",
                "That many bullets cannot be loaded.",
                "MOVE")

    def apply_move(self, seat, payload):
        """Commit a validated move and return its public event."""
        action = payload["action"]
        if action == "TRIGGER":
            chamber = self.rng.randrange(6)
            if self.cylinder[chamber]:
                self.cylinder[chamber] = False
                self.finish("SHOT", opponent(seat.player), seat.player)
                return "MATCH_ENDED"
            seat.inventory += REWARDS[seat.reward_index]
            seat.reward_index = (seat.reward_index + 1) % len(REWARDS)
            self.load_context, self.phase = self.phase, "LOAD"
            event = "SURVIVED"
        elif action == "PASS":
            self.dare_initiator = seat.player
            self.handoff(opponent(seat.player), "FORCED_REPLY")
            event = "PASSED"
        else:
            if action == "LOAD":
                empty = [
                    index for index, loaded in enumerate(
                        self.cylinder) if not loaded]
                for index in self.rng.sample(empty, payload["count"]):
                    self.cylinder[index] = True
                seat.inventory -= payload["count"]
            if self.load_context == "FORCED_REPLY":
                self.handoff(self.dare_initiator, "FORCED_RETURN")
            else:
                self.handoff(opponent(seat.player), "TURN_CHOICE")
                self.dare_initiator = None
            self.load_context = None
            event = "TURN_READY"
        self.revision += 1
        return event

    def handoff(self, player, phase):
        self.active, self.phase = player, phase
        self.turn += 1


class ServerPeer(Peer):
    """Track the handshake deadline and seat of one accepted socket."""

    def __init__(self, sock, clock):
        super().__init__(sock, clock)
        self.handshake_deadline = clock() + IO_BUDGET
        self.seat = None


class Server:
    """Serialize socket events and mutations in one selector loop."""

    def __init__(self, selector, clock=time.monotonic, rng=None):
        self.selector = selector
        self.clock = clock
        self.rng = secrets.SystemRandom() if rng is None else rng
        self.match = Match(self.rng)
        self.peers = {}
        self.terminal_peers = set()

    def accept(self, listener):
        try:
            sock, _ = listener.accept()
        except (BlockingIOError, InterruptedError, ConnectionAbortedError):
            return
        if len(self.peers) >= CONNECTION_LIMIT:
            sock.close()
            return
        try:
            sock.setblocking(False)
            peer = ServerPeer(sock, self.clock)
            self.selector.register(sock, selectors.EVENT_READ, peer)
            self.peers[peer] = selectors.EVENT_READ
        except BaseException:
            sock.close()
            raise

    def close(self, peer):
        del self.peers[peer]
        self.selector.unregister(peer.sock)
        self.terminal_peers.discard(peer)
        peer.close()

    def attached(self, peer):
        return peer.seat is not None and peer.seat.peer is peer

    def connected(self):
        return [seat for seat in self.match.seats.values(
        ) if seat.peer is not None and seat.status == "ONLINE"]

    def publish(self, event, now):
        for seat in self.connected():
            seat.peer.queue(self.match.snapshot(seat, event, now))

    def publish_lobby(self, now):
        online = self.connected()
        if len(online) == 2:
            self.match.start()
            for seat in online:
                seat.peer.queue(
                    message(
                        "GAME_START",
                        match_id=self.match.id,
                        first_player_id=self.match.active))
            self.publish("MATCH_STARTED", now)
        else:
            for seat in online:
                seat.peer.queue(
                    message(
                        "LOBBY_WAIT", match_id=self.match.id,
                        connected_players=len(online),
                        required_players=2))
            self.publish("SNAPSHOT", now)

    def bind(self, peer, seat, resumed, now):
        seat.peer = peer
        seat.grace = None
        seat.status = "ONLINE"
        peer.seat = seat
        peer.handshake_deadline = None
        self.match.revision += 1
        peer.queue(
            message(
                "WELCOME", match_id=self.match.id, player_id=seat.player,
                session_token=seat.token, resumed=resumed))
        peer.start_heartbeats(now)

    def join(self, peer, now):
        free = next(
            (player for player in PLAYER_IDS
             if player not in self.match.seats),
            None)
        if self.match.phase != LOBBY or free is None:
            raise ProtocolError("ROOM_FULL",
                                "There is no open seat in the lobby.")
        seat = Seat(free, secrets.token_hex(32), peer)
        self.match.seats[free] = seat
        self.bind(peer, seat, False, now)
        self.publish_lobby(now)

    def resume(self, peer, payload, now):
        """Verify the token and fence the old socket before a rebind."""
        if (
            payload["match_id"] != self.match.id
            or self.match.phase == "GAME_OVER"
        ):
            raise ProtocolError(
                "RESUME_DENIED",
                "This session can no longer reconnect.")
        seat = next(
            (seat for seat in self.match.seats.values()
             if seat.token is not None and secrets.compare_digest(
                 seat.token, payload["session_token"])),
            None)
        if seat is None or (seat.status != "ONLINE" and (
                seat.grace is None or now >= seat.grace)):
            raise ProtocolError(
                "RESUME_DENIED",
                "This session can no longer reconnect.")
        was_online = seat.status == "ONLINE"
        if was_online:
            self.close(seat.peer)
        self.bind(peer, seat, True, now)
        if self.match.phase == LOBBY:
            self.publish_lobby(now)
        elif (
            self.match.phase == "PAUSED"
            and not was_online
            and len(self.connected()) == 2
        ):
            self.match.phase = self.match.saved_phase
            self.match.saved_phase = None
            self.publish("RESUMED", now)
        else:
            self.publish("SNAPSHOT", now)

    def publish_result(self, now, departing=None):
        seats = self.connected()
        if departing is not None:
            seats.append(departing)
        for seat in seats:
            peer = seat.peer
            peer.begin_close(now)
            self.terminal_peers.add(peer)
            peer.queue(self.match.snapshot(seat, "MATCH_ENDED", now))
            peer.queue(self.match.result)
            reason = "CLIENT_REQUEST" if seat is departing else "MATCH_CLOSED"
            peer.queue(message("DISCONNECT", reason=reason))

    def quit(self, peer, now):
        seat = peer.seat
        if self.match.phase == LOBBY:
            del self.match.seats[seat.player]
            self.match.revision += 1
            peer.begin_close(now)
            peer.queue(message("DISCONNECT", reason="CLIENT_REQUEST"))
            self.publish_lobby(now)
        else:
            seat.status = "LEFT"
            self.match.finish("FORFEIT", opponent(seat.player), seat.player)
            self.publish_result(now, seat)

    def lose(self, peer, now, diagnostic=None):
        """Retire a transport and reserve its seat during reconnect."""
        seat = peer.seat if not peer.closing and self.attached(peer) else None
        if diagnostic is None:
            self.close(peer)
        else:
            peer.begin_close(now)
            peer.discard_unsent()
            peer.input.buffer.clear()
            peer.queue(error_message(diagnostic))
        if seat is None:
            return
        seat.peer = None
        seat.status = "DISCONNECTED"
        seat.grace = now + GRACE_SECONDS
        self.match.revision += 1
        if self.match.phase in PLAYING:
            self.match.saved_phase = self.match.phase
            self.match.phase = "PAUSED"
        if self.match.phase == LOBBY:
            self.publish_lobby(now)
        else:
            self.publish("CONNECTION_LOST", now)

    def handle(self, peer, frame, now):
        kind = validate(frame, "client")
        payload = frame["payload"]
        if not self.attached(peer):
            if kind == "CONNECT":
                self.join(peer, now)
            elif kind == "RECONNECT":
                self.resume(peer, payload, now)
            else:
                raise ProtocolError(
                    "HANDSHAKE_REQUIRED",
                    "Send CONNECT or RECONNECT first.")
        elif kind not in CLIENT_TYPES or kind in ("CONNECT", "RECONNECT"):
            raise ProtocolError(
                "ACTION_NOT_ALLOWED",
                "That message is not allowed on a bound connection.")
        elif kind == "PING":
            peer.queue(message("PONG", probe_id=payload["probe_id"]))
        elif kind == "PONG":
            peer.pong(payload["probe_id"])
        elif kind == "DISCONNECT":
            self.quit(peer, now)
        elif kind == "MOVE":
            self.match.check_move(peer.seat, frame)
            peer.complete_probe()
            event = self.match.apply_move(peer.seat, payload)
            if event == "MATCH_ENDED":
                self.publish_result(now)
            else:
                self.publish(event, now)

    def receive(self, peer):
        """Check deadlines before dispatching each complete frame."""
        try:
            data = peer.read()
        except OSError as error:
            self.io_failure(peer, error, "read")
            return
        if data is None or peer.closing:
            return
        if not data:
            self.lose(peer, self.clock())
            return
        peer.input.append(data)
        while peer in self.peers and not peer.closing:
            now = self.clock()
            self.maintain(now)
            if peer not in self.peers or peer.closing:
                return
            try:
                frame = peer.input.pop()
                if frame is None:
                    return
                self.handle(peer, frame, now)
            except ProtocolError as error:
                if error.fatal:
                    self.lose(peer, now, diagnostic=error)
                else:
                    peer.queue(error_message(error))
                    if error.message_type == "MOVE" and self.attached(peer):
                        peer.queue(
                            self.match.snapshot(
                                peer.seat, "SNAPSHOT", now))

    def io_failure(self, peer, error, operation):
        detail = f"Connection {operation} failed ({error.errno})."
        print(detail, file=sys.stderr)
        self.lose(peer, self.clock())

    def maintain(self, now):
        """Resolve transport failures before reconnect grace expires."""
        for peer in list(self.peers):
            deadlines = [peer.output_deadline(), peer.probe_deadline()]
            if peer.closing:
                deadlines.append(peer.close_deadline)
            else:
                deadlines.append(peer.handshake_deadline)
            # Closing peers finish flushing their queued frames.
            flooded = not peer.closing and peer.output_overloaded
            if flooded or any(
                deadline is not None and now >= deadline
                for deadline in deadlines
            ):
                self.lose(peer, now)

        expired = sorted((seat.grace, seat.player)
                         for seat in self.match.seats.values()
                         if seat.grace is not None and now >= seat.grace)
        for _, player in expired:
            if self.match.phase == LOBBY:
                del self.match.seats[player]
                self.match.revision += 1
                self.publish_lobby(now)
            else:
                winner = opponent(player)
                if self.match.seats[winner].status == "ONLINE":
                    self.match.finish("RECONNECT_TIMEOUT", winner, player)
                else:
                    self.match.finish("MATCH_ABORTED")
                self.publish_result(now)
                break

        for peer in list(self.peers):
            peer.tick(now)
            try:
                peer.shutdown_writes()
            except OSError as error:
                self.io_failure(peer, error, "shutdown")
                continue
            if peer.read_eof and peer.write_eof:
                self.close(peer)
        if self.match.phase == "GAME_OVER" and not self.terminal_peers:
            self.match = Match(self.rng)

    def run(self, listener):
        self.selector.register(listener, selectors.EVENT_READ)
        try:
            while True:
                self.maintain(self.clock())
                deadlines = [seat.grace for seat in self.match.seats.values()]
                for peer, current in self.peers.items():
                    interest = peer.interests()
                    if interest != current:
                        self.selector.modify(peer.sock, interest, peer)
                        self.peers[peer] = interest
                    deadlines.extend(
                        (*peer.deadlines(),
                         None if peer.closing else peer.handshake_deadline))
                deadline = min(
                    (value for value in deadlines if value is not None),
                    default=None)
                delay = None if deadline is None else max(
                    0, deadline - self.clock())
                for key, events in self.selector.select(delay):
                    self.maintain(self.clock())
                    peer = key.data
                    if peer is None:
                        self.accept(listener)
                        continue
                    if peer in self.peers and events & selectors.EVENT_READ:
                        self.receive(peer)
                    if peer in self.peers and events & selectors.EVENT_WRITE:
                        self.maintain(self.clock())
                        if peer in self.peers:
                            try:
                                peer.flush_one()
                            except OSError as error:
                                self.io_failure(peer, error, "write")
        finally:
            for peer in list(self.peers):
                self.close(peer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-p", "--port", required=True, type=int, metavar="PORT",
        help="TCP listening port (1024-65535)",
    )
    parser.add_argument(
        "--bind", default="0.0.0.0",
        help="IPv4 listening address (default: %(default)s)",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")

    address = (args.bind, args.port)
    try:
        with socket.create_server(address, family=socket.AF_INET) as listener:
            with selectors.DefaultSelector() as selector:
                listener.setblocking(False)
                print(f"Listening on {address[0]}:{address[1]}.", flush=True)
                Server(selector).run(listener)
    except KeyboardInterrupt:
        print("Server stopped.")
        return 0
    except OSError as error:
        print(f"Server error on port {args.port}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
