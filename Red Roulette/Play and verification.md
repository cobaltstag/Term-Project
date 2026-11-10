# CS 457 Russian Roulette

Keep `server.py`, `client.py`, and `protocol.py` in the same folder. The program uses the Python 3.8+ standard library. The verification suite was run with Python 3.12 on Windows.

## Play locally

From the program folder, run the server in one terminal and the client in two other terminals.

```text
python server.py
python client.py -i localhost
```

Use the full path to the bundled Python executable in PowerShell if `python` is unavailable as a command. Both programs support `-h`. The server binds to `0.0.0.0` and listens on TCP port `45700` by default. Use `-p` on both sides to select a different port. The client defaults to `server.barton.edu` for CML.

The server randomly chooses who goes first once both players have joined. On a normal turn, `trigger` fires and `pass` dares the other player to shoot. If they survive and finish their loading decision, you must shoot in return. After surviving a shot, enter `load NUMBER` to spend banked bullets or `end` to hand over the turn. Use `quit` at a prompt or Ctrl+C to leave. After a connection interruption, follow the prompt to `reconnect` while the original process retains the session and the server's grace window is still open. If a prompt is obsolete, press Enter to refresh it.

## Compare the console styles

Choose a style when you start each client. The table is the default on this branch. The log keeps more of the match visible in a short terminal window.

```text
python client.py -i localhost --style table
python client.py -i localhost --style log
```

The table gives the current phase and inventory their own rows.

```text
+----------------------------------------------------------------------+
| Turn 2 / Dare reply / Your move                                      |
+----------------------------------------------------------------------+
| P2 passed. P1 must pull the trigger                                  |
| Your banked bullets: 0                                               |
+----------------------------------------------------------------------+
Choose trigger / quit:
```

The log displays that same turn on one line above the prompt.

```text
[Turn 2 / Dare reply / Your move] P2 passed. P1 must pull the trigger | Your banked bullets: 0
Choose trigger / quit:
```

Both styles draw with ASCII characters and append updates to the terminal's scrollback. The table draws its borders in red on supported terminals. Redirecting output or setting `NO_COLOR=1` gives you plain borders.

## Implementation baseline

The [GitHub design commit](https://github.com/cobaltstag/Term-Project/commit/55ce92953f1826247694bfee50b1db5e95591adf) specifies the wire protocol and state machine. Two decisions from the implementation discussion govern the code. The starter is random after both seats are online. Due write and heartbeat failures are both processed before the server awards a reconnect-timeout win.

## Verification

Run `python -B -m unittest -v test_game` with `test_game.py` in the program folder. The initial implementation passed all 22 tests with `python -B -W error::ResourceWarning -m unittest -v test_game`. The suite uses a scripted starter and chamber sequence for repeatable outcomes, a controlled clock for deadline boundaries, and real loopback TCP with separate console processes. Tests also check the help screens, hostname resolution failure, and connection refusal. For the console wording update, ten tests that need no network passed and an in-memory replay verified the reported moves. The environment blocked the loopback rerun.

For the style prototypes, ten tests that need no network passed. A two-client replay through the server in memory verified the lobby, dare, loading decision, result, and inventory display in both styles. The environment blocked local TCP connections.

| Requirement | Implementation | Executed coverage |
|---|---|---|
| Protocol A1–A4 and FSM T1–T6, I1–I8 | `server.py` `Match`, `protocol.py` `allowed_actions` | S1–S8. Both starter seats, both fatal dare paths, survival and forced return, invalid pass, independent rewards, one loading decision, full cylinder, and private snapshots. |
| Protocol A5 and FSM T7–T13, I9–I12 | `server.py` `Server.handle`, `resume`, `lose`, `maintain`, `publish_result` | S9–S14, S17–S18, S20–S23. Resume in all playing phases, stale revision rejection after a lost reply, quit and final output, exact grace expiry, timeout win, abort, binding replacement, and refusal of post-game resume. S19 lobby loss and later reclaim were inspected separately in code. |
| Protocol F1–F6 and S15–S16 | `protocol.py` `Framer`, `encode`, `validate` | Fragmented UTF-8, coalesced frames, 4096/4097-byte cases, malformed JSON, duplicate fields, prohibited numeric values, corrected input, and fatal oversize. |
| Message envelopes, directions, and fields in protocol sections 4–5.1 | `protocol.py` `validate`, `PAYLOAD_RULES`, `snapshot_valid`, `result_valid`; dispatch in both endpoints | All 12 message types exercised across the suite. Validation probes cover types, exact keys, framing, token-bound resume, turn/revision checks, private snapshots, and terminal frame order. The full per-field and wrong-direction rejection matrix remains unverified. |
| R1–R3 and R5–R6 | `Peer.read`, `Server.receive`, `Server.lose`, `Server.resume`, `Client.receive` | C1–C5, C7, C15, C20–C21, C25–C26 in whole or in part. Real streams exercise EOF, partial input, committed gameplay with a reply the client never reads, fatal diagnostics, listener availability, and replacement. An OS reset during response writing and duplicate callback injection remain unverified. |
| R4, R8, and the agreed write-before-grace rule | `Peer.queue`, `Peer.flush_one`, `Server.maintain`, `Client.maintain`, `dial` | C6, C10, C17–C18, C24–C25 in whole or in part. Controlled tests exercise short writes, would-block, queue-time expiry, the original server handshake deadline, and simultaneous transport/grace deadlines. Client synchronization expiry and a deliberately stalled final flush remain unverified. |
| R7 and R9 | `Client.quit`, `Client.disconnected`, `Client.process_input`, `Peer.begin_close`, `Peer.shutdown_writes` | C2, C3, C16, C19. Real TCP checks exercise quit, final messages and EOF, user-directed reconnect, and rejection of obsolete console input. Losing a quit before its frame is processed (C14) and packet-level FIN/ACK capture remain unverified. |
| R10 and grace scheduler | `Peer.read`, `Peer.flush_one`, `Peer.close`, `Server.io_failure`, `Server.maintain` | The scheduler is tested at exact boundaries and after losing both sessions. Specific injected reset, broken pipe, failed diagnostic write, failed cleanup, and unbound reconnect failure scenarios (C8–C9, C11–C13, C22–C23) have partial or no direct coverage. Production writes track partial offsets through `send()`. |
| H1–H7 | `Peer.tick`, `complete_probe`, `pong`, `flush_one`, `Server.handle`, `Client.apply_snapshot` | HSC1–HSC2, HSC5–HSC7, HSC11–HSC13, and HSC17 in whole or in part. Checks cover pairing, old PONGs, exact deadline expiry, in-flight completion, removal of an unsent probe, and a live prompt left idle past the first heartbeat deadline. The full gameplay-completion, independent-probe, late terminal, and send-failure matrix (HSC3–HSC4, HSC8–HSC10, HSC14–HSC16, HSC18) remains unverified. |

CML routing, lab DNS, and Wireshark captures remain to be checked in the lab. Run the server on the server node and use `python client.py -i server.barton.edu -p 45700` on both client nodes. Check name resolution and access to TCP port `45700` from each subnet before running a match.
