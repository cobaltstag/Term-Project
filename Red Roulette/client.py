"""Play the CS 457 two-player Russian Roulette game over TCP."""

import argparse
from dataclasses import dataclass
import os
from queue import Empty, Queue
import selectors
import socket
import sys
from textwrap import wrap
from threading import Thread
import time

from protocol import (
    DEFAULT_HOST, DEFAULT_PORT, GAMEPLAY_EVENTS, IO_BUDGET, SERVER_TYPES,
    Peer, ProtocolError, message, move, validate,
)


COMMANDS = {"trigger": "TRIGGER", "pass": "PASS", "load": "LOAD", "end": "END_TURN", "end_turn": "END_TURN"}
CHOICES = {"TRIGGER": "trigger", "PASS": "pass", "LOAD": "load NUMBER", "END_TURN": "end"}
RESULT_TEXT = {"SHOT": "fatal shot", "FORFEIT": "forfeit", "RECONNECT_TIMEOUT": "reconnect timeout"}


def supports_border_color():
    if not sys.stdout.isatty() or os.getenv("NO_COLOR") or os.getenv("TERM") == "dumb":
        return False
    if sys.platform != "win32":
        return True

    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(sys.stdout.fileno()))
    except (OSError, ValueError):
        return False
    console = ctypes.windll.kernel32
    mode = wintypes.DWORD()
    if not console.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    processed_output = 0x0001
    virtual_terminal_processing = 0x0004
    flags = processed_output | virtual_terminal_processing
    if (mode.value & flags) == flags:
        return True
    return bool(console.SetConsoleMode(handle, wintypes.DWORD(mode.value | flags)))


def dial(hostname, port):
    deadline = time.monotonic() + IO_BUDGET
    lookup = Queue()

    def resolve():
        try:
            addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as error:
            lookup.put(error)
        else:
            lookup.put(addresses)

    Thread(target=resolve, daemon=True).start()
    try:
        addresses = lookup.get(timeout=max(0, deadline - time.monotonic()))
    except Empty as error:
        raise socket.gaierror("DNS lookup timed out") from error
    if isinstance(addresses, Exception):
        raise addresses
    last_error = ConnectionError("No usable server address")
    for family, kind, protocol, _, address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Connect deadline expired")
        try:
            sock = socket.socket(family, kind, protocol)
        except OSError as error:
            last_error = error
            continue
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            sock.setblocking(False)
        except OSError as error:
            sock.close()
            last_error = error
        except BaseException:
            sock.close()
            raise
        else:
            return sock
    raise last_error


@dataclass(frozen=True)
class Prompt:
    generation: int
    turn: int
    revision: int
    actions: tuple


class Console:
    def __init__(self):
        self.requests = Queue()
        self.answers = Queue()
        Thread(target=self.read, daemon=True).start()

    def read(self):
        while True:
            prompt = self.requests.get()
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                line = ""
            self.answers.put((prompt, line))
            if not line:
                return


class Client:
    def __init__(self, selector, hostname, port, style="table"):
        self.selector = selector
        self.hostname = hostname
        self.port = port
        self.style = style
        self.red_borders = style == "table" and supports_border_color()
        self.peer = None
        self.registered = 0
        self.generation = 0
        self.console = Console()
        self.prompt = None
        self.snapshot = None
        self.credentials = None
        self.sync_deadline = None
        self.welcomed = False
        self.resuming = False
        self.pending_move = False
        self.terminal = False
        self.result = None
        self.quitting = False
        self.draining = False
        self.status = None

    def connect(self, resume=False):
        try:
            sock = dial(self.hostname, self.port)
        except (socket.gaierror, UnicodeError):
            print(f"Could not resolve server hostname {self.hostname!r}. "
                  "Check the hostname and your router's DNS configuration.", file=sys.stderr)
        except OSError:
            print(f"Could not connect to {self.hostname!r} on port {self.port}. "
                  "Check that the server is running and that the port and network are correct.", file=sys.stderr)
        else:
            self.peer = Peer(sock)
            self.generation += 1
            self.welcomed = False
            self.snapshot = None
            self.pending_move = False
            self.resuming = resume
            self.sync_deadline = time.monotonic() + IO_BUDGET
            frame = message("CONNECT")
            if resume:
                frame = message("RECONNECT", match_id=self.credentials["match_id"],
                                session_token=self.credentials["session_token"])
            self.peer.queue(frame)
            print(f"Connected to {self.hostname} on port {self.port}.", flush=True)
            return True
        return False

    def detach(self):
        if self.peer is not None:
            if self.registered:
                self.selector.unregister(self.peer.sock)
                self.registered = 0
            self.peer.close()
            self.peer = None
        self.sync_deadline = None

    def disconnected(self, detail):
        self.detach()
        if self.terminal or self.quitting or self.draining:
            if self.terminal and self.result is None:
                print("The match ended before the result arrived.", flush=True)
            self.status = 0
        elif self.credentials is None:
            print(detail, file=sys.stderr, flush=True)
            self.status = 1
        else:
            print(f"{detail} Type reconnect when prompted to try your current seat.", flush=True)
            if self.prompt is not None:
                print("Press Enter to refresh the prompt.", flush=True)

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        if self.peer is None or not self.welcomed:
            self.detach()
            self.status = 0
        elif not self.draining and not self.terminal:
            now = time.monotonic()
            self.peer.begin_close(now)
            self.peer.discard_unsent({"MOVE"})
            self.peer.queue(message("DISCONNECT"))
            self.sync_deadline = None
            print("Leaving the match...", flush=True)

    def current_prompt(self):
        if self.quitting or self.draining or self.terminal:
            return None
        if self.peer is None and self.credentials is not None:
            return Prompt(self.generation, None, None, ("reconnect",))
        if self.snapshot is not None and not self.pending_move:
            actions = tuple(self.snapshot["allowed_actions"])
            if actions:
                return Prompt(self.generation, self.snapshot["turn_id"], self.snapshot["state_revision"], actions)
        return None

    def offer_prompt(self):
        current = self.current_prompt()
        if self.prompt is None and current is not None:
            self.prompt = current
            choices = [CHOICES.get(action, action) for action in current.actions]
            print(f"Choose {' / '.join((*choices, 'quit'))}: ", end="", flush=True)
            self.console.requests.put(current)

    def process_input(self):
        try:
            prompt, line = self.console.answers.get_nowait()
        except Empty:
            return
        self.prompt = None
        tokens = line.strip().lower().split()
        if not line or tokens == ["quit"]:
            self.quit()
            return
        if prompt != self.current_prompt():
            print("The prompt changed. Use the current choices.", flush=True)
            return
        if prompt.actions == ("reconnect",):
            if tokens == ["reconnect"]:
                self.connect(resume=True)
            elif tokens:
                print("Choose reconnect or quit.", flush=True)
            return
        if not tokens:
            return
        action = COMMANDS.get(tokens[0])
        count = None
        if action not in prompt.actions:
            if action == "TRIGGER" and self.snapshot["phase"] == "LOAD":
                choices = " or ".join(CHOICES[allowed] for allowed in prompt.actions)
                print(f"You already fired this turn. Use {choices}.", flush=True)
            else:
                print("Choose one of the offered actions.", flush=True)
            return
        if action == "LOAD":
            if len(tokens) != 2 or not tokens[1].isascii() or not tokens[1].isdigit() or len(tokens[1]) > 4:
                print("Enter load followed by a positive bullet count.", flush=True)
                return
            count = int(tokens[1])
            if not 1 <= count <= self.snapshot["self"]["inventory"]:
                print("Choose a positive count within your inventory.", flush=True)
                return
        elif len(tokens) != 1:
            print("This action takes no arguments.", flush=True)
            return
        self.peer.queue(move(action, prompt.turn, prompt.revision, count))
        self.pending_move = True

    def show_panel(self, title, *lines):
        if self.style == "log":
            prefix = "\n" if self.prompt is not None else ""
            print(f"{prefix}[{title}] {' | '.join(lines)}", flush=True)
            return

        width = 68
        border = "+" + "-" * (width + 2) + "+"
        side = "|"
        if self.red_borders:
            border = f"\x1b[31m{border}\x1b[39m"
            side = f"\x1b[31m{side}\x1b[39m"
        heading = [f"{side} {part:<{width}} {side}" for part in wrap(title, width)]
        body = []
        for line in lines:
            body.extend(f"{side} {part:<{width}} {side}" for part in wrap(line, width))
        print("\n".join(("", border, *heading, border, *body, border)), flush=True)

    def show_snapshot(self, payload):
        phase = payload["phase"]
        own = payload["self"]
        if phase == "WAITING_FOR_PLAYERS":
            self.show_panel("Lobby", "Waiting for both players to be online.")
        elif phase == "PAUSED":
            self.show_panel("Paused", "Match paused while a player reconnects.")
        elif phase != "GAME_OVER":
            active = payload["active_player_id"]
            title = f"Turn {payload['turn_id']}"
            if phase == "LOAD":
                title += " / Loading"
                status = f"{active} survived the shot and is deciding whether to load"
            elif phase == "FORCED_REPLY":
                title += " / Dare reply"
                passer = "P2" if active == "P1" else "P1"
                status = f"{passer} passed. {active} must pull the trigger"
            elif phase == "FORCED_RETURN":
                title += " / Dare return"
                status = f"{active} must pull the trigger in return after the opponent survived"
            else:
                status = f"{active} to play"
            if payload["allowed_actions"]:
                title += " / Your move"
            self.show_panel(title, status, f"Your banked bullets: {own['inventory']}")

    def apply_snapshot(self, payload):
        if payload["self"]["player_id"] != self.credentials["player_id"]:
            raise ProtocolError("INVALID_SCHEMA", "The state update is for a different player.")
        before = -1 if self.snapshot is None else self.snapshot["state_revision"]
        revision = payload["state_revision"]
        if revision < before:
            return
        if self.terminal and (payload["phase"] != "GAME_OVER" or revision != before):
            raise ProtocolError("INVALID_SCHEMA", "The server changed a completed match.")
        probe = self.peer.probe
        if (probe is not None and revision > max(before, probe.revision)
                and payload["event"] in GAMEPLAY_EVENTS):
            self.peer.complete_probe()
        self.snapshot = payload
        self.sync_deadline = None
        self.pending_move = False
        self.terminal = payload["phase"] == "GAME_OVER"
        if self.terminal:
            self.peer.next_tick = None
        self.show_snapshot(payload)
        if self.prompt is not None and self.prompt != self.current_prompt() and not self.terminal:
            print("Press Enter to refresh the prompt if your choices changed.", flush=True)

    def handle(self, frame):
        kind = validate(frame, "server")
        payload = frame["payload"]
        if kind not in SERVER_TYPES:
            raise ProtocolError("INVALID_SCHEMA", "The server sent a client-only message.")
        if kind == "ERROR":
            detail = payload["detail"].encode("utf-8", "backslashreplace").decode("utf-8")
            print(f"{payload['code']}: {detail}", file=sys.stderr, flush=True)
            if payload["fatal"]:
                if payload["code"] == "RESUME_DENIED":
                    self.credentials = None
                self.disconnected("The server closed this connection.")
            return
        if kind == "WELCOME" and not self.welcomed:
            if payload["resumed"] != self.resuming or (self.resuming and any(
                    payload[key] != self.credentials[key] for key in ("match_id", "player_id", "session_token")
            )):
                raise ProtocolError("INVALID_SCHEMA", "The server returned a different session.")
            self.credentials = payload
            self.welcomed = True
            self.peer.start_heartbeats(time.monotonic())
            self.show_panel("RED ROULETTE", f"You are {payload['player_id']}.", "Press Ctrl+C to quit at any time.")
            return
        if not self.welcomed or kind == "WELCOME":
            raise ProtocolError("INVALID_SCHEMA", "Unexpected server handshake order.")
        if "match_id" in payload and payload["match_id"] != self.credentials["match_id"]:
            raise ProtocolError("INVALID_SCHEMA", "The server sent a message for a different match.")
        if kind == "PING" and not (self.quitting or self.terminal):
            self.peer.queue(message("PONG", probe_id=payload["probe_id"]))
        elif kind == "PONG":
            self.peer.pong(payload["probe_id"])
        elif kind == "GAME_START":
            print(f"Match started. {payload['first_player_id']} goes first.", flush=True)
        elif kind == "STATE_UPDATE":
            self.apply_snapshot(payload)
        elif kind == "GAME_OVER":
            if not self.terminal or self.snapshot["state_revision"] != payload["state_revision"]:
                raise ProtocolError("INVALID_SCHEMA", "The result arrived without its terminal state.")
            if self.result is None:
                self.peer.complete_probe()
                self.peer.stop_heartbeats()
                self.result = payload
                if payload["winner_id"] is None:
                    self.show_panel("Result", "Match ended without a winner.")
                else:
                    outcome = "You win" if payload["winner_id"] == self.credentials["player_id"] else "You lose"
                    self.show_panel("Result", f"{outcome}. {payload['winner_id']} wins by "
                                    f"{RESULT_TEXT[payload['reason']]}.")
        elif kind == "DISCONNECT":
            self.draining = True
            self.peer.begin_close(time.monotonic())
            self.peer.output.clear()
            self.sync_deadline = None

    def maintain(self, now):
        peer = self.peer
        if peer is None:
            return
        if any(deadline is not None and now >= deadline for deadline in (
            peer.close_deadline, peer.output_deadline(), peer.probe_deadline(), self.sync_deadline,
        )):
            self.disconnected("The connection timed out.")
            return
        revision = -1 if self.snapshot is None else self.snapshot["state_revision"]
        peer.tick(now, revision)
        try:
            peer.shutdown_writes()
        except OSError:
            self.disconnected("The connection closed.")
            return
        if peer.read_eof and peer.write_eof:
            self.disconnected("The connection closed.")

    def receive(self, peer):
        try:
            data = peer.read()
        except OSError:
            self.disconnected("Lost the connection to the server.")
            return
        if data is None or self.draining:
            return
        if not data:
            if not peer.closing:
                self.disconnected("The server closed the connection.")
            return
        peer.input.append(data)
        while self.peer is peer and not self.draining:
            self.maintain(time.monotonic())
            if self.peer is not peer:
                return
            try:
                frame = peer.input.pop()
                if frame is None:
                    return
                self.handle(frame)
            except ProtocolError:
                self.disconnected("The server sent an invalid protocol message.")
                return

    def step(self):
        self.maintain(time.monotonic())
        if self.status is not None:
            return
        self.process_input()
        self.offer_prompt()
        peer = self.peer
        if peer is None:
            time.sleep(0.05)
            return
        interest = peer.interests()
        if interest != self.registered:
            if not self.registered:
                self.selector.register(peer.sock, interest)
            else:
                self.selector.modify(peer.sock, interest)
            self.registered = interest
        # A short maximum wait lets the network loop handle console input without blocking on it.
        now = time.monotonic()
        deadlines = (self.sync_deadline, *peer.deadlines())
        wait = min((max(0, value - now) for value in deadlines if value is not None), default=0.05)
        for _, events in self.selector.select(min(wait, 0.05)):
            self.maintain(time.monotonic())
            if self.peer is peer and events & selectors.EVENT_READ:
                self.receive(peer)
            if self.peer is peer and events & selectors.EVENT_WRITE:
                self.maintain(time.monotonic())
                if self.peer is peer:
                    try:
                        peer.flush_one()
                    except OSError:
                        self.disconnected("Lost the connection to the server.")

    def run(self):
        try:
            if not self.connect():
                return 1
            while self.status is None:
                try:
                    self.step()
                except KeyboardInterrupt:
                    self.quit()
            return self.status
        finally:
            self.detach()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--hostname", default=DEFAULT_HOST,
                        help="server DNS hostname (default: %(default)s)")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT,
                        help="server TCP port (default: %(default)s)")
    parser.add_argument("--style", choices=("table", "log"), default="table",
                        help="terminal layout (default: %(default)s)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        with selectors.DefaultSelector() as selector:
            return Client(selector, args.hostname, args.port, args.style).run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
