"""Repeatable CS 457 checks. Run with python -B -m unittest -v test_game."""

from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
import selectors
import socket
import subprocess
import sys
from threading import Thread
import time
import unittest

from protocol import Framer, Peer, ProtocolError, encode, message, move, validate
from server import Match, Seat, Server


ROOT = Path(__file__).resolve().parent


class ScriptedRandom:
    def __init__(self, *chambers, starter="P1"):
        self.chambers = iter(chambers)
        self.starter = starter

    def randrange(self, stop):
        assert stop == 6
        return next(self.chambers)

    def choice(self, players):
        assert players == ("P1", "P2")
        return self.starter

    def sample(self, choices, count):
        return choices[:count]


class Clock:
    now = 0

    def __call__(self):
        return self.now


def play(match, player, action, count=None):
    frame = move(action, match.turn, match.revision, count)
    seat = match.seats[player]
    match.check_move(seat, frame)
    return match.apply_move(seat, frame["payload"])


class RulesTests(unittest.TestCase):
    def match(self, *chambers, starter="P1"):
        match = Match(ScriptedRandom(*chambers, starter=starter))
        for player in ("P1", "P2"):
            match.seats[player] = Seat(player, "token", None)
            match.revision += 1
        match.start()
        return match

    def test_both_dare_survivors_and_next_normal_turn(self):
        match = self.match(0, 1, 2, starter="P2")
        self.assertEqual((match.active, match.turn, match.revision), ("P2", 1, 3))
        self.assertEqual(play(match, "P2", "PASS"), "PASSED")
        self.assertEqual((match.active, match.turn, match.phase), ("P1", 2, "FORCED_REPLY"))
        with self.assertRaisesRegex(ProtocolError, "offered"):
            play(match, "P1", "PASS")
        self.assertEqual(play(match, "P1", "TRIGGER"), "SURVIVED")
        self.assertEqual((match.turn, match.seats["P1"].inventory), (2, 1))
        play(match, "P1", "LOAD", 1)
        self.assertEqual((match.active, match.turn, match.phase), ("P2", 3, "FORCED_RETURN"))
        play(match, "P2", "TRIGGER")
        play(match, "P2", "END_TURN")
        self.assertEqual((match.active, match.turn, match.phase, match.dare_initiator), ("P1", 4, "TURN_CHOICE", None))
        self.assertEqual((match.seats["P1"].inventory, match.seats["P2"].inventory), (0, 1))
        self.assertEqual([seat.reward_index for seat in match.seats.values()], [1, 1])

    def test_fatal_dare_paths(self):
        for shots, winner in [((0, 0), "P1"), ((0, 1, 0), "P2")]:
            with self.subTest(winner=winner):
                match = self.match(*shots)
                play(match, "P1", "PASS")
                play(match, "P2", "TRIGGER")
                if winner == "P2":
                    play(match, "P2", "END_TURN")
                    play(match, "P1", "TRIGGER")
                self.assertEqual(match.result["payload"]["winner_id"], winner)
                self.assertEqual(match.result["payload"]["reason"], "SHOT")
                self.assertIsNone(match.turn)
                self.assertEqual(match.seats["P1"].inventory, 0)
                self.assertEqual(match.seats["P2"].inventory, int(winner == "P2"))

    def test_reward_cycle_and_private_snapshots(self):
        match = self.match(0, *([5] * 9))
        balances = []
        for turn in range(9):
            seat = match.seats[match.active]
            play(match, seat.player, "TRIGGER")
            if turn % 2 == 0:
                balances.append(seat.inventory)
            other = match.seats["P2" if seat.player == "P1" else "P1"]
            snapshot = match.snapshot(other, "SURVIVED", 0)
            self.assertEqual(validate(snapshot, "server"), "STATE_UPDATE")
            self.assertEqual(snapshot["payload"]["self"], {"player_id": other.player, "inventory": other.inventory})
            self.assertEqual(snapshot["payload"]["allowed_actions"], [])
            self.assertEqual(set(snapshot["payload"]["players"][0]), {"player_id", "connection"})
            play(match, seat.player, "END_TURN")
        self.assertEqual(balances, [1, 3, 7, 12, 13])
        self.assertEqual(match.seats["P1"].reward_index, 1)

    def test_loading_can_fill_cylinder_and_does_not_leak(self):
        match = self.match(0, 5, 2)
        play(match, "P1", "TRIGGER")
        match.cylinder = [True, True, True, True, False, False]
        match.seats["P1"].inventory = 7
        before = match.revision
        with self.assertRaises(ProtocolError) as error:
            play(match, "P1", "LOAD", 3)
        self.assertEqual(error.exception.code, "LOAD_LIMIT")
        self.assertEqual((match.revision, match.seats["P1"].inventory), (before, 7))
        self.assertEqual(play(match, "P1", "LOAD", 2), "TURN_READY")
        self.assertEqual(match.seats["P1"].inventory, 5)
        self.assertTrue(all(match.cylinder))
        payload = match.snapshot(match.seats["P2"], "TURN_READY", 0)["payload"]
        self.assertEqual(payload["self"]["inventory"], 0)
        self.assertNotIn("count", payload)
        self.assertNotIn("cylinder", payload)
        play(match, "P2", "PASS")
        play(match, "P1", "TRIGGER")
        self.assertEqual(match.result["payload"]["winner_id"], "P2")


class WireTests(unittest.TestCase):
    def test_split_utf8_and_coalesced_frames(self):
        first = b'{"x":"' + "é".encode() + b'"}\n'
        second = encode(message("PING", probe_id=1))
        framer = Framer()
        framer.append(first[:7])
        self.assertIsNone(framer.pop())
        framer.append(first[7:] + second + second[:3])
        self.assertEqual(framer.pop(), {"x": "é"})
        self.assertEqual(framer.pop(), message("PING", probe_id=1))
        self.assertIsNone(framer.pop())
        framer.append(second[3:])
        self.assertEqual(framer.pop(), message("PING", probe_id=1))

    def test_frame_limit_counts_bytes_per_object(self):
        exact = b'{"x":"' + "é".encode() * 2044 + b'"}'
        self.assertEqual(len(exact), 4096)
        framer = Framer()
        framer.append(exact + b'\n' + exact + b'\n')
        self.assertEqual(len(framer.pop()["x"]), 2044)
        self.assertEqual(len(framer.pop()["x"]), 2044)
        framer.append(exact[:-2] + b'X"}')
        with self.assertRaises(ProtocolError) as error:
            framer.pop()
        self.assertEqual(error.exception.code, "FRAME_TOO_LARGE")

    def test_malformed_frames_are_consumed_before_next_frame(self):
        for line in (b'\n', b'{"x":NaN}\n', b'{"x":0,"x":1}\n', b'{}\r\n',
                     b'{} \n', b'[]\n', b'{"x":"\xff"}\n', b'\xef\xbb\xbf{}\n'):
            with self.subTest(line=line):
                framer = Framer()
                framer.append(line + encode(message("CONNECT")))
                with self.assertRaises(ProtocolError) as error:
                    framer.pop()
                self.assertEqual(error.exception.code, "MALFORMED_FRAME")
                self.assertEqual(validate(framer.pop(), "client"), "CONNECT")

    def test_integer_syntax_and_exact_fields(self):
        for value in (b'-0', b'true', b'1.0', b'1e0', b'"1"', b'null'):
            framer = Framer()
            framer.append(b'{"version":' + value + b',"msg_type":"CONNECT","payload":{}}\n')
            with self.assertRaises(ProtocolError) as error:
                validate(framer.pop(), "client")
            self.assertEqual(error.exception.code, "INVALID_SCHEMA")
        for frame in (message("CONNECT", extra=1), message("RECONNECT"), move("PASS", 1, 3, count=2)):
            with self.assertRaises(ProtocolError):
                validate(frame, "client")


class FakeTransport:
    def __init__(self, *sizes):
        self.sizes = iter(sizes)
        self.written = bytearray()

    def send(self, data):
        size = next(self.sizes, len(data))
        if size is None:
            raise BlockingIOError()
        self.written.extend(data[:size])
        return min(len(data), size)


class HeartbeatTests(unittest.TestCase):
    def test_partial_progress_and_early_completion(self):
        clock = Clock()
        sock = FakeTransport(None, 3)
        peer = Peer(sock, clock)
        peer.start_heartbeats(clock())
        clock.now = 5
        peer.tick(clock())
        probe = peer.probe
        peer.flush_one()
        self.assertEqual(probe.frame.offset, 0)
        peer.pong(1)
        self.assertIs(peer.probe, probe)
        peer.flush_one()
        self.assertEqual(probe.frame.offset, 3)
        peer.complete_probe()
        self.assertTrue(probe.completed)
        self.assertIsNone(peer.probe_deadline())
        peer.flush_one()
        self.assertIsNone(peer.probe)
        self.assertEqual(sock.written, encode(message("PING", probe_id=1)))

    def test_queued_cancel_and_old_pong_do_not_clear_next_probe(self):
        clock = Clock()
        peer = Peer(FakeTransport(), clock)
        peer.start_heartbeats(clock())
        clock.now = 5
        peer.tick(clock())
        peer.complete_probe()
        self.assertFalse(peer.output)
        clock.now = 10
        peer.tick(clock())
        peer.flush_one()
        self.assertEqual(peer.probe_deadline(), 20)
        peer.pong(1)
        self.assertEqual(peer.probe.identifier, 2)
        self.assertEqual(peer.probe_deadline(), 20)
        peer.pong(2)
        self.assertIsNone(peer.probe)


@contextmanager
def controlled_server(*chambers):
    clock = Clock()
    with selectors.DefaultSelector() as selector:
        server = Server(selector, clock, ScriptedRandom(*chambers))
        try:
            yield server, clock
        finally:
            for peer in list(server.peers):
                server.close(peer)


@contextmanager
def accepted(server):
    with socket.create_server(("127.0.0.1", 0)) as listener:
        with socket.create_connection(listener.getsockname(), timeout=2) as sock:
            server.accept(listener)
            peer = next(reversed(server.peers))
            yield peer, sock


class DeadlineTests(unittest.TestCase):
    def test_handshake_error_keeps_original_deadline(self):
        with controlled_server() as (server, clock), accepted(server) as (peer, sock):
            clock.now = 4.999
            sock.sendall(b"{}\n")
            server.receive(peer)
            self.assertEqual(peer.handshake_deadline, 5)
            self.assertTrue(peer.output)
            peer.flush_one()
            clock.now = 5
            sock.sendall(encode(message("CONNECT")))
            server.receive(peer)
            self.assertFalse(server.peers)
            self.assertFalse(server.match.seats)

    def test_probe_deadline_includes_equality(self):
        for instant in (14.999, 15):
            with self.subTest(instant=instant), controlled_server() as (server, clock), accepted(server) as (peer, sock):
                server.join(peer, clock())
                while peer.output:
                    peer.flush_one()
                clock.now = 5
                server.maintain(clock())
                peer.flush_one()
                self.assertEqual(peer.probe_deadline(), 15)
                clock.now = instant
                sock.sendall(encode(message("PONG", probe_id=1)))
                server.receive(peer)
                seat = server.match.seats["P1"]
                self.assertIsNone(peer.probe)
                if instant < 15:
                    self.assertEqual((seat.status, server.match.revision), ("ONLINE", 1))
                else:
                    self.assertEqual((seat.status, seat.grace, server.match.revision), ("DISCONNECTED", 45, 2))

    def test_reconnect_restores_every_playing_phase(self):
        cases = {
            "TURN_CHOICE": [],
            "FORCED_REPLY": [("P1", "PASS")],
            "LOAD": [("P1", "PASS"), ("P2", "TRIGGER")],
            "FORCED_RETURN": [("P1", "PASS"), ("P2", "TRIGGER"), ("P2", "END_TURN")],
        }
        for phase, moves in cases.items():
            with self.subTest(phase=phase), controlled_server(0, 5) as (server, clock):
                with accepted(server) as (one, _), accepted(server) as (two, _):
                    server.join(one, clock())
                    server.join(two, clock())
                    match = server.match
                    for player, action in moves:
                        play(match, player, action)
                    self.assertEqual(match.phase, phase)
                    turn_state = (match.active, match.turn, match.dare_initiator, match.load_context)
                    balances = [seat.inventory for seat in match.seats.values()]
                    credentials = {"match_id": match.id, "session_token": one.seat.token}
                    server.lose(one, clock())
                    self.assertEqual((match.phase, match.saved_phase), ("PAUSED", phase))
                    with accepted(server) as (new, _):
                        server.resume(new, credentials, clock())
                        self.assertEqual((match.phase, match.saved_phase), (phase, None))
                        self.assertEqual((match.active, match.turn, match.dare_initiator, match.load_context), turn_state)
                        self.assertEqual([seat.inventory for seat in match.seats.values()], balances)

    def test_write_or_heartbeat_timeout_precedes_grace(self):
        for failure in ("heartbeat", "write"):
            with self.subTest(failure=failure), controlled_server(0) as (server, clock):
                with accepted(server) as (one, _), accepted(server) as (two, _):
                    server.join(one, clock())
                    server.join(two, clock())
                    old = server.match
                    while two.output:
                        two.flush_one()
                    server.lose(one, clock())
                    while two.output:
                        two.flush_one()
                    if failure == "heartbeat":
                        clock.now = 20
                        two.tick(clock())
                        two.flush_one()
                    else:
                        clock.now = 25
                        two.queue(message("PONG", probe_id=1))
                    clock.now = 30
                    server.maintain(clock())
                    self.assertEqual(old.result["payload"]["reason"], "MATCH_ABORTED")
                    self.assertIsNone(old.result["payload"]["winner_id"])

    def test_resume_before_vs_at_deadline(self):
        for instant in (29.999, 30):
            with self.subTest(instant=instant), controlled_server(0) as (server, clock):
                with accepted(server) as (one, _), accepted(server) as (two, _):
                    server.join(one, clock())
                    server.join(two, clock())
                    old = server.match
                    credentials = {"match_id": old.id, "session_token": one.seat.token}
                    server.lose(one, clock())
                    while two.output:
                        two.flush_one()
                    clock.now = instant
                    server.maintain(clock())
                    with accepted(server) as (new, _):
                        if instant < 30:
                            server.resume(new, credentials, instant)
                            self.assertEqual((old.phase, old.turn, old.active), ("TURN_CHOICE", 1, "P1"))
                        else:
                            self.assertEqual(old.result["payload"]["winner_id"], "P2")
                            with self.assertRaises(ProtocolError) as error:
                                server.resume(new, credentials, instant)
                            self.assertEqual(error.exception.code, "RESUME_DENIED")


class Process:
    def __init__(self, *args, input_pipe=False):
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-u", *args], cwd=ROOT,
            stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.lines = Queue()
        self.reader = Thread(target=self.read_output, daemon=True)
        self.reader.start()

    def read_output(self):
        text = bytearray()
        while char := self.process.stdout.read(1):
            text.extend(char)
            if char == b"\n" or text.endswith(b": "):
                self.lines.put(text.decode("utf-8", "replace").strip())
                text.clear()
        if text:
            self.lines.put(text.decode("utf-8", "replace").strip())
        self.lines.put(None)

    def see(self, expected, timeout=8):
        seen = []
        deadline = time.monotonic() + timeout
        try:
            while True:
                line = self.lines.get(timeout=max(0, deadline - time.monotonic()))
                seen.append(line)
                if line is None:
                    break
                if expected in line:
                    return
        except Empty:
            pass
        raise AssertionError(f"Expected {expected!r}; saw {seen!r}")

    def type(self, command):
        self.process.stdin.write((command + "\n").encode())
        self.process.stdin.flush()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        if self.process.stdin:
            self.process.stdin.close()
        self.reader.join(timeout=5)
        self.process.stdout.close()


@contextmanager
def running_server(chambers):
    # Inject only deterministic randomness. Both clients still speak normal TCP.
    code = f'''from test_game import ScriptedRandom
import server, selectors, socket
with socket.create_server(("127.0.0.1", 0)) as listener, selectors.DefaultSelector() as selector:
    listener.setblocking(False)
    print(listener.getsockname()[1], flush=True)
    server.Server(selector, rng=ScriptedRandom(*{chambers!r})).run(listener)
'''
    with Process("-c", code) as process:
        yield process, int(process.lines.get(timeout=8))


class WireClient:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=4)
        self.framer = Framer()

    def send(self, frame):
        self.sock.sendall(encode(frame))

    def receive(self, kind):
        while True:
            frame = self.framer.pop()
            if frame is not None:
                self_kind = validate(frame, "server")
                if self_kind == "PING":
                    self.send(message("PONG", probe_id=frame["payload"]["probe_id"]))
                    continue
                if self_kind != kind:
                    raise AssertionError(f"Expected {kind}; received {self_kind}")
                return frame["payload"]
            data = self.sock.recv(4096)
            if not data:
                raise AssertionError(f"Expected {kind}; received EOF")
            self.framer.append(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.sock.close()


class IntegrationTests(unittest.TestCase):
    def test_console_reconnect_and_obsolete_prompt(self):
        match = Match(ScriptedRandom(0))
        seat = Seat("P1", "a" * 64, None)
        match.seats["P1"] = seat
        match.seats["P2"] = Seat("P2", "b" * 64, None)
        match.revision = 2
        match.start()

        def welcome(resumed):
            return message("WELCOME", match_id=match.id, player_id="P1", session_token=seat.token, resumed=resumed)

        def receive_command(sock):
            framer = Framer()
            while (frame := framer.pop()) is None:
                data = sock.recv(4096)
                if not data:
                    self.fail("The client closed before its command arrived")
                framer.append(data)
            validate(frame, "client")
            return frame

        with socket.create_server(("127.0.0.1", 0)) as listener:
            listener.settimeout(6)
            port = str(listener.getsockname()[1])
            with Process("client.py", "-i", "localhost", "-p", port, input_pipe=True) as player:
                first, _ = listener.accept()
                with first:
                    first.settimeout(4)
                    self.assertEqual(receive_command(first), message("CONNECT"))
                    frames = (welcome(False), message("GAME_START", match_id=match.id, first_player_id="P1"),
                              match.snapshot(seat, "MATCH_STARTED", 0))
                    first.sendall(b"".join(map(encode, frames)))
                    player.see("Choose trigger / pass / quit")
                    first.shutdown(socket.SHUT_WR)
                    player.see("Press Enter to refresh the prompt")
                player.type("")
                player.see("Choose reconnect / quit")
                player.type("reconnect")
                second, _ = listener.accept()
                with second:
                    second.settimeout(4)
                    command = receive_command(second)
                    self.assertEqual(command["msg_type"], "RECONNECT")
                    self.assertEqual(command["payload"]["match_id"], match.id)
                    self.assertTrue(command["payload"]["session_token"] == seat.token)
                    match.revision = 5
                    second.sendall(encode(welcome(True)) + encode(match.snapshot(seat, "RESUMED", 0)))
                    player.see("Choose trigger / pass / quit")
                    match.revision = 6
                    match.phase, match.saved_phase = "PAUSED", "TURN_CHOICE"
                    match.seats["P2"].status = "DISCONNECTED"
                    match.seats["P2"].grace = 30
                    second.sendall(encode(match.snapshot(seat, "CONNECTION_LOST", 0)))
                    player.see("Press Enter to refresh the prompt")
                    player.type("trigger")
                    player.see("The prompt changed")
                    match.revision = 7
                    match.phase, match.saved_phase = "TURN_CHOICE", None
                    match.seats["P2"].status, match.seats["P2"].grace = "ONLINE", None
                    second.sendall(encode(match.snapshot(seat, "RESUMED", 0)))
                    player.see("Choose trigger / pass / quit")
                    player.type("trigger")
                    self.assertEqual(receive_command(second), move("TRIGGER", 1, 7))
                    match.finish("SHOT", "P2", "P1")
                    frames = (match.snapshot(seat, "MATCH_ENDED", 0), match.result,
                              message("DISCONNECT", reason="MATCH_CLOSED"))
                    second.sendall(b"".join(map(encode, frames)))
                    second.shutdown(socket.SHUT_WR)
                    player.see("You lose. P2 wins by fatal shot.")
                    self.assertEqual(second.recv(4096), b"")
                    self.assertEqual(player.process.wait(timeout=6), 0)

    def test_help_dns_failure_and_connection_refusal(self):
        for script in ("client.py", "server.py"):
            result = subprocess.run([sys.executable, "-B", script, "-h"], cwd=ROOT,
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0)
            self.assertIn("-p", result.stdout)
            if script == "client.py":
                self.assertIn("-i", result.stdout)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = str(reservation.getsockname()[1])
            for host, expected in (("127.0.0.1", "Could not connect"),
                                   ("cs457-host-does-not-exist.invalid.", "router's DNS configuration")):
                result = subprocess.run([sys.executable, "-B", "client.py", "-i", host, "-p", port], cwd=ROOT,
                                        capture_output=True, text=True, timeout=9)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_two_console_clients_finish_a_real_game(self):
        with running_server((0, 1, 0)) as (_, port):
            with Process("client.py", "-i", "localhost", "-p", str(port), input_pipe=True) as p1:
                p1.see("Waiting for both players")
                with Process("client.py", "-i", "localhost", "-p", str(port), input_pipe=True) as p2:
                    p1.see("Choose trigger / pass / quit")
                    p1.type("pass")
                    p2.see("P1 passed. P2 must pull the trigger")
                    p2.see("Choose trigger / quit")
                    p2.type("trigger")
                    p2.see("P2 survived the shot and is deciding whether to load")
                    p2.see("Choose load NUMBER / end / quit")
                    p2.type("trigger")
                    p2.see("You already fired this turn. Use load NUMBER or end.")
                    p2.see("Choose load NUMBER / end / quit")
                    p2.type("end")
                    p1.see("P1 must pull the trigger in return after the opponent survived")
                    p1.see("Choose trigger / quit")
                    p1.type("trigger")
                    p1.see("You lose. P2 wins by fatal shot.")
                    p2.see("You win. P2 wins by fatal shot.")
                    self.assertEqual(p1.process.wait(timeout=6), 0)
                    self.assertEqual(p2.process.wait(timeout=6), 0)

    def test_lost_reply_then_resume_and_stale_retry(self):
        with running_server((0, 1, 0)) as (_, port):
            with WireClient(port) as p1:
                p1.send(message("CONNECT"))
                p1.receive("WELCOME")
                p1.receive("LOBBY_WAIT")
                self.assertEqual(p1.receive("STATE_UPDATE")["state_revision"], 1)
                with WireClient(port) as p2:
                    p2.send(message("CONNECT"))
                    cred = p2.receive("WELCOME")
                    for player in (p1, p2):
                        player.receive("GAME_START")
                        self.assertEqual(player.receive("STATE_UPDATE")["state_revision"], 3)
                    p1.send(move("PASS", 1, 3))
                    for player in (p1, p2):
                        self.assertEqual(player.receive("STATE_UPDATE")["phase"], "FORCED_REPLY")
                    p2.send(move("TRIGGER", 2, 4))
                    self.assertEqual(p1.receive("STATE_UPDATE")["phase"], "LOAD")
                    p2.sock.shutdown(socket.SHUT_WR)
                paused = p1.receive("STATE_UPDATE")
                self.assertEqual((paused["phase"], paused["resume_phase"], paused["turn_id"]), ("PAUSED", "LOAD", 2))
                with WireClient(port) as resumed:
                    resumed.send(message("RECONNECT", match_id=cred["match_id"], session_token=cred["session_token"]))
                    self.assertTrue(resumed.receive("WELCOME")["resumed"])
                    state = resumed.receive("STATE_UPDATE")
                    self.assertEqual((state["event"], state["phase"], state["self"]["inventory"]), ("RESUMED", "LOAD", 1))
                    p1.receive("STATE_UPDATE")
                    resumed.send(move("TRIGGER", 2, 4))
                    self.assertEqual(resumed.receive("ERROR")["code"], "STALE_STATE")
                    self.assertEqual(resumed.receive("STATE_UPDATE")["self"]["inventory"], 1)
                    resumed.send(message("DISCONNECT"))
                    resumed.sock.shutdown(socket.SHUT_WR)
                    for player, reason in ((p1, "MATCH_CLOSED"), (resumed, "CLIENT_REQUEST")):
                        self.assertEqual(player.receive("STATE_UPDATE")["phase"], "GAME_OVER")
                        self.assertEqual(player.receive("GAME_OVER")["winner_id"], "P1")
                        self.assertEqual(player.receive("DISCONNECT")["reason"], reason)
                        self.assertEqual(player.sock.recv(4096), b"")
                with WireClient(port) as denied:
                    denied.send(message("RECONNECT", match_id=cred["match_id"], session_token=cred["session_token"]))
                    self.assertEqual(denied.receive("ERROR")["code"], "RESUME_DENIED")

    def test_fatal_and_nonfatal_unbound_errors_leave_listener_available(self):
        with running_server((0,)) as (_, port):
            with WireClient(port) as broken:
                broken.sock.sendall(b"x" * 4097)
                self.assertEqual(broken.receive("ERROR")["code"], "FRAME_TOO_LARGE")
                self.assertEqual(broken.sock.recv(4096), b"")
            with WireClient(port) as corrected:
                corrected.sock.sendall(b'{"x":NaN}\n' + encode(message("CONNECT")))
                self.assertEqual(corrected.receive("ERROR")["code"], "MALFORMED_FRAME")
                self.assertEqual(corrected.receive("WELCOME")["player_id"], "P1")

    def test_replacement_fences_old_stream_and_partial_move_never_executes(self):
        with running_server((0,)) as (_, port), WireClient(port) as old, WireClient(port) as p2:
            old.send(message("CONNECT"))
            cred = old.receive("WELCOME")
            old.receive("LOBBY_WAIT")
            old.receive("STATE_UPDATE")
            p2.send(message("CONNECT"))
            p2.receive("WELCOME")
            for player in (old, p2):
                player.receive("GAME_START")
                player.receive("STATE_UPDATE")
            with WireClient(port) as new:
                old.sock.sendall(encode(move("TRIGGER", 1, 3))[:-1])
                new.send(message("RECONNECT", match_id=cred["match_id"], session_token=cred["session_token"]))
                self.assertTrue(new.receive("WELCOME")["resumed"])
                state = new.receive("STATE_UPDATE")
                self.assertEqual((state["state_revision"], state["phase"], state["event"]), (4, "TURN_CHOICE", "SNAPSHOT"))
                p2.receive("STATE_UPDATE")
                self.assertEqual(old.sock.recv(4096), b"")
                new.sock.sendall(encode(move("TRIGGER", 1, 4))[:-1])
                new.sock.shutdown(socket.SHUT_WR)
                paused = p2.receive("STATE_UPDATE")
                self.assertEqual((paused["phase"], paused["resume_phase"], paused["turn_id"]), ("PAUSED", "TURN_CHOICE", 1))

    def test_console_keeps_heartbeats_running_while_waiting_at_prompt(self):
        with running_server((0,)) as (_, port):
            with Process("client.py", "-i", "localhost", "-p", str(port), input_pipe=True) as p1:
                p1.see("Waiting for both players")
                with Process("client.py", "-i", "localhost", "-p", str(port), input_pipe=True) as p2:
                    p1.see("Choose trigger / pass / quit")
                    time.sleep(16)
                    p1.type("quit")
                    p1.see("You lose. P2 wins by forfeit.")
                    p2.see("You win. P2 wins by forfeit.")
                    self.assertEqual(p1.process.wait(timeout=6), 0)
                    self.assertEqual(p2.process.wait(timeout=6), 0)
                with WireClient(port) as fresh:
                    fresh.send(message("CONNECT"))
                    self.assertEqual(fresh.receive("WELCOME")["player_id"], "P1")
                    fresh.receive("LOBBY_WAIT")
                    self.assertEqual(fresh.receive("STATE_UPDATE")["state_revision"], 1)


if __name__ == "__main__":
    unittest.main()
