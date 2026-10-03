# Application Protocol Blueprint — v1

**Project:** CS 457 Term-Project  
**Author:** Andrew Barton (design decisions developed with ChatGPT)  
**Updated:** 2026-10-02  
**Status:** Submission candidate; design decisions resolved; implementation verification remains future work.

## 1. Scope and decision status

The confirmed design uses two players, server-controlled state, independent random chamber sampling, the forced-shot dare sequence, banked inventory, and the private repeating survival reward cycle. Intentional departure forfeits; interrupted connections permit reconnection. UTF-8 JSON with newline framing is selected. Application-level, bidirectional PING/PONG heartbeats with a ten-second matching-response deadline are now selected; this deadline verifies connection responsiveness and never limits how long a player may think.

The following v1 choices are resolved. D1–D6 and D8–D10 were confirmed during the final review; D7 was confirmed when heartbeats were selected. Later implementation work must follow these values unless the design is explicitly revised.

| ID | Final decision | Purpose |
|---|---|---|
| D1 | 4,096 UTF-8 bytes maximum per JSON object, excluding LF. | Bounded framing. |
| D2 | 30-second server reconnection grace from detected loss. | Recovery window. |
| D3 | Pause active gameplay while either player is disconnected; preserve exact phase and obligations. | Fair recovery. |
| D4 | One accepted LOAD or END_TURN finishes the post-survival loading phase. | One loading decision. |
| D5 | Zero starting inventory; each player's first survival reward is one. | Complete initial state. |
| D6 | Retain terminal results/tokens for 30 seconds; one room, one match at a time; no automatic rematch. | Missed-result recovery and cleanup. |
| D7 | Bidirectional PING/PONG, exact probe pairing, ten-second response deadline. | Detect unresponsive peers. |
| D8 | If both players are disconnected when the earliest grace expires, abort without a winner. | Deterministic no-winner outcome. |
| D9 | Five-second frame-output, close-flush, and client quit-wait budgets. | Bounded output/cleanup. |
| D10 | Five-second heartbeat scheduling ticks; at most one pending probe per direction. | Background liveness checks. |

Multiplayer expansion, UI tutorials, persistent scores, server-crash recovery, and program implementation are outside this document. Language and concurrency-library selection belong to later SOW sprints.

### 1.1 Lab endpoint

Use TCP at the lab DNS name `server.barton.edu`, default port `45700`. The server binds its configured CML interface (or all local interfaces); clients use that DNS name and the same port. Host and port may be overridden consistently through configuration for testing. The port is a project-local default, not a claim of an assigned Internet service. Application messages contain no hostname or port fields.

## 2. Authority and visibility

**A1.** The server alone samples chambers, awards rewards, deducts inventory, places/removes bullets, assigns turns, and resolves outcomes. A client submits intentions.

**A2.** Each player has a private inventory and reward position. Successful shots award 1, 2, 4, 5, then repeat at 1. Award first, advance second. Loading does not change reward position.

**A3.** Send a player their own inventory only. Never send either reward position, future reward, cylinder array, sampled index, loaded count, opponent inventory, opponent reward amount, or opponent loading quantity. Reward amounts can be inferred from a player's own inventory changes; that discovery is intentional.

**A4.** LOAD and END_TURN generate the same public TURN_READY notification. Error feedback can still permit deductions about capacity; the design prevents direct disclosure, not all inference. LOAD_LIMIT must not report the number of empty chambers.

**A5.** Bind player identity to the server's current connection/session. MOVE has no client-controlled player_id. A resumed session replaces its old connection; old connections are fenced from further mutations.

## 3. Serialization and framing

**F1.** TCP carries UTF-8 JSON objects. Exactly one object occupies each line. Append exactly one LF byte (hex 0A) after the closing object; do not send CRLF, a BOM, blank lines, pretty-printed multiline JSON, or trailing whitespace.

**F2.** Maximum: 4,096 bytes before LF, measured after UTF-8 encoding. It is a ceiling, not a padded allocation or minimum. A 150-byte object transmits 151 application bytes including LF.

**F3.** Retain incoming bytes until LF. For each complete line, enforce the limit, decode strict UTF-8, and parse one complete JSON object. Keep any unfinished suffix. Drain all complete lines in order. Reads and TCP packets are not message boundaries.

**F4.** A line of exactly 4,096 bytes followed by LF is valid-sized. More than 4,096 bytes before LF is fatal, even if no delimiter has yet arrived. EOF discards an incomplete suffix; it never executes as a message.

**F5.** Reject invalid UTF-8, invalid JSON, duplicate object keys, non-object roots, NaN/Infinity, raw embedded LF, and CR bytes in a frame. Escaped string content such as backslash-n is allowed and is not a delimiter. Object key order has no significance.

**F6.** Oversize or malformed framing: send ERROR when a writable connection permits, then close the connection. For an assigned player this is a detected interruption, not an intentional forfeit. Well-framed schema/action errors keep the connection open and do not change game state.

References: [JSON, RFC 8259](https://www.rfc-editor.org/rfc/rfc8259); [TCP, RFC 9293](https://www.rfc-editor.org/rfc/rfc9293). The LF delimiter and size limit are this application's rules, not TCP or JSON requirements. Newline framing is a form of delimiter-based text framing.

## 4. Common fields and data types

Every message requires these three fields; all other keys are forbidden unless specified for that type:

| Field | Type | Specification |
|---|---|---|
| version | Integer | Exactly 1. Unsupported versions produce UNSUPPORTED_VERSION and connection closure. |
| msg_type | String | Exact uppercase name from section 5. Case-sensitive. |
| payload | Object | Exactly the fields listed for this message. No unspecified fields. |

MOVE additionally requires top-level turn_id and state_revision. No other message uses those top-level fields.

Numbers described as integers must use JSON integer notation without a decimal point or exponent; booleans, numeric strings, null, and negative zero are invalid. Nonnegative fields use 0 or an unsigned nonzero decimal integer; positive fields exclude 0. Nonnegative counters have no gameplay-imposed cap; implementations must preserve exact values rather than silently overflow. Frame bounds still apply.

| Named type | Definition |
|---|---|
| PlayerId | String, exactly P1 or P2. |
| MatchId | String, 32 lowercase hexadecimal characters; server-created opaque identifier. |
| SessionToken | String, 64 lowercase hexadecimal characters; server-created unpredictable session credential. |
| TurnId | Integer >= 1 during active play; starts at 1 and increments at each handoff, including passes and forced returns. |
| Revision | Integer >= 0; server increases it for each accepted state mutation, connection-state change, or terminal outcome. |
| Inventory | Integer >= 0; authoritative banked bullets. |
| Phase | WAITING_FOR_PLAYERS, TURN_CHOICE, FORCED_REPLY, FORCED_RETURN, LOAD, PAUSED, or GAME_OVER. |

Session tokens go only to their owner and back to the server. Never include them in public snapshots or traces intended for sharing. This lab protocol does not specify transport encryption; tokens are session credentials, not a claim of secure transport.

## 5. All message schemas

All listed fields are required unless explicitly described as conditional. Directions are relative to client and server.

### CONNECT — client -> server

Payload: empty object. Valid only as the first complete message on a fresh, unbound connection. Assign the lowest available seat (P1 before P2), initialize its inventory/reward position to zero, and send WELCOME before any other message to that connection. If both seats are occupied or reserved, send ROOM_FULL and close this unbound connection. No state is restored through CONNECT. Each accepted join increments the revision once. If still waiting, send lobby notifications/snapshots; if both seats are online, execute match initialization as one additional revision. Thus initial joins produce revisions 1 and 2, and initial GAME_START snapshots have revision 3.

### RECONNECT — client -> server

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Must identify the retained lobby/match. |
| session_token | SessionToken | Must identify an eligible retained session: disconnected within gameplay/lobby grace, or not currently online within terminal-result retention. |

Valid only as the first complete message on a fresh connection. Invalid or expired credentials receive RESUME_DENIED and closure of the new connection. A valid credential whose old connection is still considered ONLINE receives RESUME_BUSY and closure of only the new connection; it does not replace that old connection or extend grace. On success send WELCOME, then the current authorized snapshot immediately. If resuming a terminal match, send GAME_OVER after the snapshot. Terminal eligibility uses the 30-second result-retention deadline even if gameplay grace expired; it restores access to the result, never to gameplay. Never recreate inventory, reroll a shot, award a reward, or clear forced-turn obligations during reconnection.

### PING — either direction

| Payload field | Type | Constraint |
|---|---|---|
| probe_id | Integer | Positive; generated by this sender and monotonically increasing within its current connection generation, starting at 1. |

Both endpoints can initiate probes, independently. Accept PING only after session binding/WELCOME, on a live non-closing connection, in any lobby, gameplay, PAUSED, or retained GAME_OVER phase. The receiver queues a PONG with exactly the received probe_id promptly through the same serialized output stream. This is automatic background handling; no player input, turn ownership, game revision, or gameplay action validation is required.

A well-framed duplicate PING can be answered again; it does not complete the receiver's own pending probe or reset any local deadline. Malformed PING fields follow INVALID_SCHEMA handling. An unbound first-message PING receives HANDSHAKE_REQUIRED from the server and closure.

### PONG — either direction

| Payload field | Type | Constraint |
|---|---|---|
| probe_id | Integer | Positive; must echo the specific peer PING being answered. |

A PONG completes a local pending probe only when msg_type is PONG, the identifier matches that probe, the connection generation matches, and it is validated strictly before the armed response deadline (or satisfies the early-response case in H3). A valid incoming PING, MOVE, STATE_UPDATE, or other traffic never substitutes for the matching response.

Well-framed PONG with no pending probe, a different identifier, or an already completed/expired identifier is ignored without a gameplay mutation, ERROR-response loop, or deadline extension. Malformed fields remain schema errors. Do not apply MOVE's active-player, revision, or phase checks to heartbeat messages. Before WELCOME, clients do not send heartbeat messages.

### WELCOME — server -> requesting client

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Assigned lobby/match identifier. |
| player_id | PlayerId | Assigned seat. |
| session_token | SessionToken | Owner's credential; unchanged by resume. |
| resumed | Boolean | False after CONNECT, true after successful RECONNECT. |

### LOBBY_WAIT — server -> connected lobby clients

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Current lobby identifier. |
| connected_players | Integer | 1 or 2; match cannot start until both seats are present and connected. |
| required_players | Integer | Exactly 2. |

Follow with a STATE_UPDATE containing lobby state. Two connections can be present only transiently while their accepted joins are being resolved; a fully connected lobby starts immediately.

### GAME_START — server -> both clients

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Match being started. |
| first_player_id | PlayerId | Server's randomized first player. |

Follow with recipient-specific STATE_UPDATE. This message is emitted once per match, not replayed on reconnection. At start, increment the lobby revision once, set turn_id to 1, and ensure WELCOME precedes GAME_START for the second joining client.

### MOVE — client -> server

| Field | Type | Constraint |
|---|---|---|
| turn_id (top level) | TurnId | Must equal current turn. |
| state_revision (top level) | Revision | Must equal latest authoritative revision. |
| payload.action | String | TRIGGER, PASS, LOAD, or END_TURN. |
| payload.count | Integer | Present only for LOAD, positive, <= own inventory and empty chamber count. Forbidden for other actions. |

TRIGGER and PASS are accepted only in TURN_CHOICE; forced phases permit TRIGGER only. LOAD and END_TURN are accepted only in LOAD. The sending session must own the active turn. PASS is never a substitute for END_TURN.

One accepted LOAD deducts exactly count, fills that many distinct empty chambers chosen randomly by the server, then hands over the turn. END_TURN hands over without spending. Both preserve reward position. Invalid requests spend nothing and retain the phase.

Validate serially in this order: envelope/schema; session; match active/not paused; revision; turn; sender active; action allowed; count limits. Apply an accepted action and all its effects atomically before processing another event. No automatic MOVE retries are part of v1: after interruption obtain a fresh snapshot before issuing any new action. Only current-session client MOVE enters this validation sequence; transport controls use their own guards. A duplicate accepted action is never executed twice. It receives MATCH_NOT_ACTIVE or MATCH_PAUSED when those earlier guards apply, otherwise STALE_STATE for its old revision.

### STATE_UPDATE — server -> each connected assigned client individually

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Current match/lobby. |
| state_revision | Revision | Current authoritative revision. |
| phase | Phase | Current state. |
| resume_phase | Phase or null | Previous active phase when PAUSED; otherwise null. When non-null, exactly TURN_CHOICE, FORCED_REPLY, FORCED_RETURN, or LOAD. |
| turn_id | Integer or null | TurnId during active/paused match; null before start or after terminal outcome. |
| active_player_id | PlayerId or null | Preserved during pause; null in lobby/terminal state. |
| allowed_actions | Array of strings | Unique legal action names, ordered TRIGGER/PASS or LOAD/END_TURN as applicable. Empty when inactive, paused, waiting, or terminal. |
| self | Object | Exactly player_id: PlayerId and inventory: Inventory. |
| players | Array of objects | One per occupied/reserved seat (both seats retained at GAME_OVER), sorted P1 then P2 with no duplicates; each has player_id: PlayerId and connection: ONLINE, DISCONNECTED, or LEFT. No private data. |
| event | String | SNAPSHOT, MATCH_STARTED, PASSED, SURVIVED, TURN_READY, CONNECTION_LOST, RESUMED, or MATCH_ENDED. |
| reconnect_remaining_ms | Integer or null | Nonnegative milliseconds to earliest reserved lobby/gameplay grace deadline, rounded up and clamped at zero; null if none or phase GAME_OVER. Informational only; the server clock controls expiry. |

TURN_CHOICE permits TRIGGER and PASS. FORCED_REPLY/FORCED_RETURN permit TRIGGER. LOAD advertises LOAD only if the recipient has positive inventory, and always END_TURN; do not condition the advertised LOAD action on secret cylinder capacity. Validation enforces capacity. Allowed actions describe gameplay only; DISCONNECT can be sent separately in any bound session.

A reconnect snapshot uses SNAPSHOT (or RESUMED when all players are now online), and includes current inventory, turn and phase. It never includes next reward information.

### GAME_OVER — server -> all connected assigned clients

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Terminal match. |
| state_revision | Revision | Frozen revision at terminal outcome; later connection snapshots may have a higher revision. |
| winner_id | PlayerId or null | Winner; null for aborted match. |
| loser_id | PlayerId or null | Loser; null for aborted match. |
| reason | String | SHOT, FORFEIT, RECONNECT_TIMEOUT, or MATCH_ABORTED. |

SHOT/FORFEIT/RECONNECT_TIMEOUT have distinct winner and loser IDs. MATCH_ABORTED has both null. Send a GAME_OVER snapshot first, then GAME_OVER. Terminal results cannot subsequently change.

### DISCONNECT — either direction

Client -> server: payload is empty. In an active or paused match, the sending player forfeits immediately, regardless of whose turn it is. Send terminal updates/results to writable peers, then send the departing client a DISCONNECT acknowledgement when possible and close it. In a lobby, release their seat; in a terminal match, close without changing the result.

Server -> client payload:

| Payload field | Type | Constraint |
|---|---|---|
| reason | String | CLIENT_REQUEST or MATCH_CLOSED. |

This is acknowledgement/closure notification, not a client gameplay command. A server closure cannot manufacture an additional player forfeit. A queued message is not a guarantee of delivery.

### ERROR — server -> offending client only

| Payload field | Type | Constraint |
|---|---|---|
| code | String | Code from the table below. |
| detail | String | Human explanation, 1–160 Unicode code points, with no secrets. |
| fatal | Boolean | Whether this connection will be closed. |

| Code | Meaning | Fatal |
|---|---|---|
| MALFORMED_FRAME | Invalid framing/UTF-8/JSON/root/duplicate keys. | true |
| FRAME_TOO_LARGE | More than configured byte ceiling. | true |
| UNSUPPORTED_VERSION | Version other than 1. | true |
| INVALID_SCHEMA | Missing, extra, wrong-type, or invalid-enum field. | false |
| HANDSHAKE_REQUIRED | MOVE/DISCONNECT before binding; or inappropriate first type. | true |
| ROOM_FULL | No available seat. | true |
| RESUME_DENIED | Invalid or expired resume credential. | true |
| RESUME_BUSY | Valid credential, but old connection is still ONLINE; retry policy in R8. | true |
| MATCH_NOT_ACTIVE | No active game or already terminal. | false |
| MATCH_PAUSED | Gameplay frozen for reconnection. | false |
| STALE_STATE | Revision mismatch. | false |
| STALE_TURN | Turn mismatch after revision passed. | false |
| NOT_YOUR_TURN | Sender does not own active turn. | false |
| ACTION_NOT_ALLOWED | Action invalid in current phase; includes repeated CONNECT/RECONNECT on bound connection. | false |
| LOAD_LIMIT | Count exceeds inventory or cylinder space; do not say which hidden capacity failed. | false |

Here fatal means close the offending connection, not immediate player forfeit; an assigned session follows R5/R6 unless a terminal/intentional-close result already exists. Version must first be a syntactically valid integer: wrong type is INVALID_SCHEMA, while an unsupported integer is UNSUPPORTED_VERSION. Unknown message names/fields are INVALID_SCHEMA. A known server-only type sent by a bound client is ACTION_NOT_ALLOWED; a correctly formed type other than CONNECT/RECONNECT on an unbound connection is HANDSHAKE_REQUIRED. Clients do not send ERROR messages: malformed/wrong-direction server input causes client-side retirement and recovery without an ERROR loop.

After a nonfatal MOVE error send the sender a fresh STATE_UPDATE with event SNAPSHOT. Schema errors without a bound session receive ERROR only. Count <= 0 or noninteger is INVALID_SCHEMA, not LOAD_LIMIT.

### 5.1 State ordering and publication

On every accepted gameplay transition, update all authoritative fields and increment state_revision exactly once before constructing per-recipient snapshots. Snapshot replies to errors do not increment it. Heartbeat success never increments it. A connection-status change has its own revision; serialize it with gameplay. Frame order on each connection follows that commit order.

Lobby snapshots use inventory 0, turn_id/active_player_id/resume_phase null, no allowed_actions, and phase WAITING_FOR_PLAYERS. Occupied seats, including reserved disconnected ones, appear in players. A seat addition/removal/resume/loss increments revision and notifies existing connected peers with a current snapshot. LOBBY_WAIT accompanies remaining lobby waiting; do not start until both seats are online.

Entering GAME_OVER sets phase GAME_OVER, active_player_id/turn_id/resume_phase null, allowed_actions empty, clears transient load/dare/pause obligations and all gameplay grace timers, and preserves inventories for private snapshots. Record the terminal outcome/revision once. Send terminal STATE_UPDATE before GAME_OVER to each writable peer. Later connection changes may advance snapshot revision but cannot change the frozen terminal result.

A client applies complete snapshots atomically and discards older revisions on the same match. Equal revisions are permitted for repeated snapshot delivery. GAME_OVER's frozen outcome revision may be lower than the latest snapshot after result recovery; do not discard that result merely for this difference. The same connection's pending local MOVE is never automatically replayed.

## 6. Connection interruption and forfeit

Recovery policy:

1. At detected EOF/transport failure on the current bound connection, mark the player disconnected, reserve their session, and start their 30-second monotonic deadline. Ignore events from superseded connections.
2. Pause active gameplay and save the exact prior phase, active player, turn, revision, inventory, cylinder and dare obligations. Notify the remaining connected player by STATE_UPDATE. Do not reveal private saved state.
3. For an ongoing match/lobby, accept RECONNECT strictly before that player's grace deadline; at equality timeout wins. Once the match is terminal, use the result-retention deadline instead. Valid resume rebinds the session and sends its snapshot. Resume gameplay only when both players are online. Send each connected player a fresh recipient-specific STATE_UPDATE whenever session status changes, including the remaining peer when gameplay resumes.
4. A second connection loss receives its own deadline. Invalid resume attempts do not extend a deadline. Repeated valid interruptions get a new grace window; imposing an anti-stalling budget is outside v1.
5. If an active player's deadline expires while the opponent is online, declare RECONNECT_TIMEOUT and award the opponent the win. If the opponent is also disconnected, declare MATCH_ABORTED. Terminal outcomes are frozen.
6. Lobby interruption reserves the seat for the same grace window. Expiry releases it without a win/loss. Both seats must be connected before starting.
7. Intentional DISCONNECT bypasses grace. If the opponent is disconnected, still record them as the winner and retain that result for possible resume.
8. Retain terminal state and both tokens for 30 seconds from terminal resolution, independently of expired gameplay grace. Permit result-only recovery for any non-ONLINE retained seat, including LEFT; send results on valid reconnect during retention, then notify writable peers with DISCONNECT/MATCH_CLOSED, close connections, invalidate tokens and clean up.

A server process crash and recovery from disk are outside v1. Grace begins at detection, not at the unknowable instant the physical connection failed. A matching-response heartbeat timeout is now another loss detector; section 6.7 specifies it. The ten-second heartbeat deadline is not the reconnection grace period.


### 6.1 Transport event classification

**R1 — Intent is established by the application message.** An intentional forfeit requires a complete, schema-valid client DISCONNECT that the server processes on the current bound connection. TCP FIN, EOF, reset, an exception, or a client closing its window is not evidence of that message. Even an orderly TCP shutdown without processed DISCONNECT follows interruption/grace policy. A malformed or unterminated DISCONNECT is not a forfeit.

Python API names below illustrate the behavior for a likely Python implementation; equivalent socket APIs must preserve the same classification. This is an API behavior contract, not socket boilerplate.

| Observation on the current established connection | Classification | Required behavior |
|---|---|---|
| A receive requested with a positive buffer size returns empty bytes, e.g. recv(n) returns b'' with n > 0 | EOF: peer will send no further bytes on that connection | Stop receiving; apply R2 and the one-time loss procedure R5 unless closure/outcome was already resolved. Do not repeatedly read EOF. |
| Peer closes its sending direction only, potentially retaining its receive direction | TCP half-close | V1 does not support half-closed active sessions. Treat as EOF/interruption unless a processed DISCONNECT already established intentional departure. |
| ConnectionResetError, ConnectionAbortedError, BrokenPipeError, or equivalent fatal socket error during established-session read/write | Transport failure | Immediately detach the connection and invoke R5. No attempt to send ERROR back over the failed connection. |
| Nonempty send returns a positive count smaller than the remaining frame | Partial write, not failure | Advance that frame's byte offset by exactly the returned count; retain and send only the suffix on the same connection. Preserve frame order. |
| Nonempty send returns zero | Failed write / no usable progress | Invoke R5; do not spin or resubmit a whole gameplay action. |
| BlockingIOError / EAGAIN / EWOULDBLOCK on established read/write | Temporary would-block condition | Keep buffers, byte offsets, session and gameplay intact; await readiness. Do not mark disconnected or busy-loop. Write-completion deadline still applies. |
| EINTR / InterruptedError from an interrupted low-level I/O operation, without application cancellation | Interrupted operation | Retry only that I/O operation with known remaining bytes and preserved state, not the MOVE. Python often retries automatically. An API failure with unknown partial-send progress follows R3 instead. |
| Explicitly configured receive polling timeout expires | No data within one polling interval | Check timers and continue waiting. No automatic forfeit, EOF inference, or connection-loss declaration. No inactivity/game-turn deadline is specified in v1. |
| OS-reported connection timeout, established write timeout, or write-completion deadline expires | Failed transport/output operation | Invoke R5. A receive polling timeout must be distinguishable from this by operation and error context; do not classify every TimeoutError identically. |
| Ten-second deadline expires without the specific matching PONG | Heartbeat-detected unresponsive connection | Invoke R5 exactly once; apply interruption/grace, never immediate forfeit. |
| Other OSError from established-session read/write, after recoverable conditions above have been excluded | Unclassified socket failure | Log operation and numeric error; detach safely using R5. Never silently continue on a potentially unusable socket. |
| EOF/error/timeout before a CONNECT or RECONNECT has successfully bound the new connection | Unbound connection failure | Close that connection only. Do not invent a player, alter a reserved existing session, or start a new grace period. |
| Later EOF/error after processed DISCONNECT, server closure, terminal resolution, or detachment | Duplicate/expected closure event | Retire transport resources without another gameplay outcome or grace-period reset. |

Short receives with positive byte counts are ordinary data, not EOF. A readiness notification alone is not EOF; the actual receive result determines it. An accept/connect failure affects the listener or new connection, not an existing player's game. Nonblocking connect-in-progress is not a successful binding or a player loss.

### 6.2 Receive ordering and incomplete data

**R2.** Complete frames already obtained from the stream must be validated and processed in stream order before that stream's EOF event is resolved, while the connection remains eligible to accept input. Stop at a processed DISCONNECT or fatal framing error and ignore all subsequent bytes from that closing connection. Do not attempt to drain an unread/reset socket after declaring a fatal error.

An incomplete final suffix is discarded without executing it or manufacturing a MALFORMED_FRAME reply to a closed peer. Thus a valid DISCONNECT frame followed by FIN remains an intentional forfeit; a partial DISCONNECT followed by FIN follows interruption policy. If a successful MOVE precedes EOF, its atomic effects remain committed before the interruption. A later socket error may prevent bytes still in the network/OS from being obtained; do not claim all sent commands were received.

### 6.3 Failed writes and delivery uncertainty

**R3.** Gameplay commit and output delivery are separate. Failure to send a result must not undo, repeat, or resample the accepted MOVE. The client's next successful RECONNECT receives the committed snapshot. Neither local write success nor queueing a message proves that the other application processed it.

For a partially written frame, retain known offsets only while the same live connection remains in use. After connection failure discard its outgoing bytes, including any unfinished frame; never transfer that suffix to the new connection. A reconnect begins a new stream with WELCOME and a complete fresh snapshot.

If a send-all API raises, the delivered byte count can be unknown. Do not restart the same frame from byte zero on that stream or automatically replay its associated action. Retire the failed connection and recover by snapshot. An ERROR or DISCONNECT acknowledgement whose send fails is best-effort; its failure never changes a previously committed forfeit or terminal result.

**R4 — Bounded output (D9).** Each outgoing frame has a five-second monotonic output budget beginning when it is queued for transmission, including queue residence and writes; partial progress does not restart it. This prevents a queued heartbeat from waiting indefinitely before its response timer can begin. A transient would-block observation before this deadline is recoverable. Deadline expiry classifies the connection as failed output. A server-requested close also has a five-second total flush budget beginning when closure is decided; queue final control/result frames in order, then close when output completes, a fatal write occurs, or that budget expires. Use the earlier applicable deadline. Do not wait indefinitely for a peer acknowledgement or FIN. No blocking network I/O or flush may hold the authoritative-state lock or prevent the server processing another session's events/timers.

This budget bounds output attempts; heartbeat probes provide traffic and a separate matching-response deadline to detect quiet unresponsive connections. It is distinct from reconnection grace, receive polling, and terminal-result retention.

### 6.4 One-time connection retirement and cleanup

**R5.** For a current bound active connection, atomically:

1. Verify the session binding/generation still identifies this connection and it has not already been retired. Stale callbacks may clean up only their own obsolete transport resources; they must not change the current session binding or gameplay, even if an OS descriptor number has been reused.
2. Mark it retired and remove it from gameplay input/readiness registration before closing; subsequent reads/writes/callbacks cannot mutate the session.
3. Preserve authoritative match data. If there is no prior intentional departure, cleanup closure, or terminal result, mark the player DISCONNECTED and set their grace deadline exactly once from the original loss-detection time. For active play, save the active phase/context and enter PAUSED. If already PAUSED for the other player, preserve the original saved phase and give only the newly absent player a deadline.
4. Remove transport buffers/queues belonging to the retired connection. Queue authorized snapshots for remaining peers; process any resulting peer write failure as a separate session loss rather than recursively performing network I/O inside this transition.
5. Best-effort shutdown and close the socket explicitly. Log cleanup errors; they cannot escape cleanup, restart grace, alter a match result, or prevent cleanup of the other connection.

Unbound connections skip all match/session mutation. Lobby sessions use the reserved-seat policy. Terminal transport loss still marks an ONLINE seat DISCONNECTED and advances the snapshot revision, enabling result-only recovery; it preserves the fixed result/reason/terminal revision and retention deadline and never starts gameplay grace. Intentional departures set LEFT; releasing a lobby seat removes it from players instead. Cleanup is idempotent: EOF followed by write failure, multiple worker callbacks, or shutdown followed by close cannot create multiple loss transitions. The first detected loss time controls grace even if cleanup or notification fails.

**R6.** Mark server-requested closing connections as closing, stop accepting their input, and record the closure cause before flushing/closing. After processed client DISCONNECT, outcome/seat release is committed before final messages are attempted. After fatal protocol validation, an assigned active/lobby session follows the existing interruption policy immediately, while a single diagnostic ERROR may be flushed within R4; stop parsing subsequent frames. This closing socket is output-only: it cannot accept gameplay or restart grace. If an outgoing frame was already partially written, finish its remaining bytes before the diagnostic; if that cannot complete within budget, skip the diagnostic and close. No diagnostic may be inserted into the middle of an unfinished frame. A later resume may replace the session binding before this flush finishes; old-socket cleanup still cannot affect the new binding. After normal match cleanup, never reopen grace for a resulting EOF or exception.

Expected already-disconnected/already-closed errors during shutdown/close are cleanup diagnostics, not new failures. Application coding errors, signal-driven cancellation, listener failures, and process shutdown must not be blanket-caught and relabeled as player forfeits. Surface internal faults to server diagnostics; server-crash persistence remains outside v1.

### 6.5 Client-side termination contract

**R7.** A client that detects unexpected EOF/fatal established-socket error without a received terminal/closure notification stops sending gameplay, discards that connection's partial buffers, and retains its last credential for a reconnect attempt. It cannot declare itself or the opponent the winner locally. After RECONNECT, wait for WELCOME and STATE_UPDATE before issuing a new MOVE; never automatically resend a potentially accepted action.

A player choosing to quit sends the complete DISCONNECT frame, disables further actions and automatic reconnection, and attempts to receive the final result/acknowledgement. Client quit-wait budget is five seconds from the quit request; close on acknowledgement, EOF/error, or expiry. If the server never processes the complete request, the server follows EOF/failure policy; the client's intention or successful local send cannot substitute for receipt.

Received DISCONNECT/CLIENT_REQUEST or MATCH_CLOSED stops reconnection. Received GAME_OVER fixes the displayed result; subsequent closure does not imply an additional loss. Client retry and handshake deadlines are specified by R8 below. No turn-inactivity deadline is imposed: a responsive player can take time to think. Heartbeat scheduling and response processing are specified in section 6.7.

**R8 — Bounded handshake and recovery attempts.** On each accepted TCP connection, the server allows five seconds to validate and bind a complete CONNECT or RECONNECT. Invalid frames do not restart this handshake deadline; expiry closes the unbound connection without allocating a seat or changing an existing session. A client allows five seconds for a connect attempt and, once connected, five seconds for WELCOME plus the initial STATE_UPDATE. Use the earlier applicable overall recovery deadline.

After unexpected loss with known credentials, the client starts a local 30-second recovery-attempt window, tries RECONNECT immediately, and retries failed transport attempts or RESUME_BUSY one second after that attempt ends. Only one attempt may be in flight. Stop on successful WELCOME plus snapshot, RESUME_DENIED, deliberate quit, a terminal/closure notification, or local window expiry. Never resend an old MOVE. No failure/retry extends the server's grace. A busy response can occur because client and server detect failure at different times; heartbeat expiry will eventually retire an unresponsive old connection.

The local client window begins at its own detection time and does not claim to equal the server's independently timed grace. A failed automatic recovery is reported as unavailable/unknown until the server returns a result; the client must not invent a winner. If WELCOME was never received on the initial connection, no credential is available: report the failed join and allow an explicit fresh join later; do not guess a token or automatically occupy another seat. A retained valid credential can be used in a later explicit result-recovery request while the server's terminal-retention window remains open.

### 6.6 Transport conformance scenarios

| ID | Scenario | Required outcome |
|---|---|---|
| C1 | Positive-sized receive returns empty bytes without processed DISCONNECT | One interruption, grace begins; no immediate forfeit. |
| C2 | Full valid DISCONNECT then FIN, with later read/write exception | One intentional forfeit; no new grace or changed result. |
| C3 | Half-close without DISCONNECT | No active half-closed session; use interruption policy even if writes might still succeed. |
| C4 | EOF after partial JSON / partial DISCONNECT | Discard suffix; no action/forfeit manufactured. |
| C5 | Accepted survival shot, then reset while sending its result | Reward awarded exactly once; resume shows committed LOAD state. |
| C6 | Read/write would-block, short positive receive/write, or receive polling timeout | Preserve connection and game; no forfeit or grace unless a separate fatal condition occurs. |
| C7 | Duplicate loss callbacks, including old-generation callback after resume | No deadline extension; old connection cannot detach the newly bound session. |
| C8 | Fatal error while sending ERROR or a DISCONNECT acknowledgement | No recursive error-send loop; original outcome retained; cleanup completes. |
| C9 | Send-all failure with unknown progress | No same-stream whole-frame retry, action replay, or old-suffix transfer to reconnect. |
| C10 | Write/close-flush budget expires | Retire transport within budget; gameplay/timers for the other player continue. |
| C11 | Shutdown/close raises while retiring a connection | Log diagnostic; finish cleanup; no new outcome or grace reset. |
| C12 | EOF/error on unbound or rejected reconnect attempt | Existing player's reserved session and deadline remain unchanged. |
| C13 | Second player loses connection while match already paused | Preserve saved gameplay phase; independent grace deadlines. |
| C14 | Client sends quit but message is lost before server processing | Client stops reconnecting; server uses interruption/grace, not assumed immediate forfeit. | 
| C15 | Valid resume credential while old connection is still ONLINE | RESUME_BUSY closes only new attempt; bounded retries continue without changing old session/grace. |
| C16 | Gameplay grace expires, then absent player reconnects during terminal retention | WELCOME, terminal snapshot, GAME_OVER; result only, no restarted game. |
| C17 | Unbound client never completes handshake, or bound client never receives initial synchronization | Five-second handshake/sync timeout; no invented seat/result or automatic MOVE replay. |
| C18 | Outgoing frame remains queued without a first write attempt | Five-second budget still expires; no indefinitely queued probe. |

API references: [Python socket HOWTO](https://docs.python.org/3/howto/sockets.html), [socket API including partial sends and sendall failure](https://docs.python.org/3/library/socket.html), and [OS exception classifications](https://docs.python.org/3/library/exceptions.html). EOF and exception semantics come from the socket API; half-close rejection, timeout classification, output budgets, and game consequences are application design decisions.


### 6.7 Heartbeat scheduling, correlation, and expiry

**H1 — Connection control, not game state.** Both endpoints send PING and answer PING with PONG in the background. Heartbeats are bound to the existing session/connection; they contain only probe_id. Successful exchanges do not change phase, inventory, turn_id, reward position, or state_revision. Expiry changes connection status through R5 and may therefore pause the match and advance its revision.

**H2 — Scheduling (D10).** maintain five-second monotonic scheduling ticks per bound connection. Start the server's schedule after successful CONNECT/RECONNECT binding; start the client's after WELCOME. The first probe opportunity is five seconds later. At a tick, create/send a probe only if that endpoint has no queued or awaiting-response probe. Otherwise skip the tick: do not overlap probes or resend a pending PING. After completion, the next regular tick can create a new identifier. Each direction has its own counter and pending record; identically numbered probes in opposite directions are distinct.

Probes and automatic responses continue during lobby waiting, turn deliberation, and PAUSED while that particular connection remains online. They may continue during terminal retention until closing. Stop scheduling for closing/retired connections; clear all pending/timer state on retirement, quit, or a new connection generation. Reconnection starts a fresh schedule/counter, with no carried-over deadline or response.

**H3 — Ten-second deadline.** Record the expected probe before queueing its frame. Once the complete LF-terminated PING has been submitted successfully to the local socket, arm one deadline at monotonic completion time plus ten seconds. This local completion is not proof of peer delivery. Do not restart the deadline on partial progress, unrelated traffic, duplicates, mismatched PONG, or any new timer tick.

A matching PONG can be processed after transmission has started even if the local write-completion callback has not yet run; if it already completed that probe, the callback must not later arm a stale deadline. If the PING cannot be transmitted, handle the failed write/budget through R3–R5 instead of pretending a response was awaited. PONG writes use the same bounded output rules.

**H4 — Exact pairing.** Validate the PONG envelope, integer identifier, current generation, and pending probe under serialized connection-control handling. When its deadline is armed, process a matching response strictly before it; equality is expired. Before the completion callback arms a deadline, use H3's early-response rule. Clear that pending record atomically on success. An incoming PING with the same numeric ID is still a request, never a response. Ignore well-framed stale/unmatched/unsolicited PONG; it cannot reset responsiveness timers.

**H5 — Expiry.** At or after a pending deadline, if no matching response completed it, retire the current connection once using R5 with diagnostic cause HEARTBEAT_TIMEOUT. This cause is internal, not a new GAME_OVER reason or ERROR code. The server marks the session disconnected, pauses if required, and begins the separate 30-second grace. A client stops gameplay and attempts session recovery using R7. Do not send a fatal ERROR down the timed-out stream or wait for further proof. A late PONG cannot revive the old connection or extend grace.

With a five-second interval and prompt local scheduling/output, an idle silent failure can be noticed roughly up to fifteen seconds later (up to five until the next probe plus ten awaiting its response). That is not a ten-second end-to-end reconnection/forfeit promise. Output budgets, scheduling delays, and the separate grace period affect total elapsed time. Expiry means the connection is no longer responsive enough under this policy; it does not prove the remote process has crashed.

**H6 — Background progress.** Socket receive/parsing, PONG responses, probe scheduling, and deadlines must continue while the console waits for keyboard input and while gameplay is paused. A blocking prompt must not hold the connection handler or game-state lock. All heartbeat/control frames share the existing TCP stream and framing limits; serialize writes so frame bytes cannot interleave. PING/PONG can be handled before the first gameplay snapshot after WELCOME, but MOVE remains prohibited until synchronization is complete. Threading vs multiplexing remains a later implementation decision.

**H7 — Application keepalive.** This protocol uses its own JSON heartbeat over TCP; it does not adopt WebSocket framing or require a WebSocket library. OS TCP keepalive may be supplementary but cannot replace H1–H6. Reference: [Ping/Pong timing rationale](https://websockets.readthedocs.io/en/stable/topics/keepalive.html).

| ID | Heartbeat conformance scenario | Required outcome |
|---|---|---|
| HSC1 | Send PING id 17; receive valid PONG id 17 before deadline | Clear only that connection/direction's pending probe; gameplay/revision unchanged. |
| HSC2 | Pending id 17; receive PONG id 16, duplicate old PONG, or unsolicited PONG | Ignore; no deadline extension or response loop. |
| HSC3 | Pending id 17; receive PING id 17 from peer | Reply PONG id 17; own pending request still requires its own incoming PONG. |
| HSC4 | Receive MOVE/STATE_UPDATE but no matching PONG | Heartbeat deadline still expires; arbitrary traffic cannot replace correlation. |
| HSC5 | Matching PONG just before deadline vs exactly at deadline | Before succeeds; equality expires and follows R5. |
| HSC6 | Deadline expires during any running phase or while already PAUSED | One transport loss; preserve committed gameplay/context; begin only this player's grace. |
| HSC7 | Client waits at console prompt longer than ten seconds | Background heartbeat continues; player thinking does not itself cause loss. |
| HSC8 | Reconnect, then stale old-generation timeout/PONG callback occurs | New connection is unaffected; no deadline carried over. |
| HSC9 | PING send fails or response to peer PING fails | Existing bounded-write/loss rules; no action replay or duplicate retirement. |
| HSC10 | Peer GAME_OVER/intentional DISCONNECT already resolved when heartbeat expires | Preserve terminal result; do not manufacture another loss. |
| HSC11 | Scheduling tick occurs with an outstanding probe | Skip creating a new probe; original deadline unchanged. |
| HSC12 | Matching PONG processed before local send-completion callback | Callback must not arm a deadline for the completed probe. |


## 7. Wire examples

Each line below depicts one entire frame. The final two displayed characters backslash-n stand for one actual LF byte, not two literal bytes. Tokens/IDs are illustrative, not credentials. Examples are independent unless stated otherwise. The PING/PONG pair requires an already bound/WELCOME session and is not sent before CONNECT.

```text
{"version":1,"msg_type":"PING","payload":{"probe_id":17}}\n
{"version":1,"msg_type":"PONG","payload":{"probe_id":17}}\n
{"version":1,"msg_type":"CONNECT","payload":{}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":false}}\n
{"version":1,"msg_type":"LOBBY_WAIT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","connected_players":1,"required_players":2}}\n
{"version":1,"msg_type":"GAME_START","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","first_player_id":"P1"}}\n
{"version":1,"msg_type":"MOVE","turn_id":1,"state_revision":3,"payload":{"action":"PASS"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":5,"payload":{"action":"TRIGGER"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":5,"payload":{"action":"LOAD","count":1}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":5,"payload":{"action":"END_TURN"}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":5,"phase":"LOAD","resume_phase":null,"turn_id":2,"active_player_id":"P2","allowed_actions":["LOAD","END_TURN"],"self":{"player_id":"P2","inventory":1},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"SURVIVED","reconnect_remaining_ms":null}}\n
{"version":1,"msg_type":"RECONNECT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":true}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{}}\n
{"version":1,"msg_type":"GAME_OVER","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":7,"winner_id":"P2","loser_id":"P1","reason":"FORFEIT"}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{"reason":"CLIENT_REQUEST"}}\n
{"version":1,"msg_type":"ERROR","payload":{"code":"ACTION_NOT_ALLOWED","detail":"This turn requires TRIGGER.","fatal":false}}\n
{"version":1,"msg_type":"ERROR","payload":{"code":"RESUME_BUSY","detail":"Previous connection is still active; retry within recovery window.","fatal":true}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{"reason":"MATCH_CLOSED"}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":6,"phase":"PAUSED","resume_phase":"LOAD","turn_id":2,"active_player_id":"P2","allowed_actions":[],"self":{"player_id":"P1","inventory":0},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"DISCONNECTED"}],"event":"CONNECTION_LOST","reconnect_remaining_ms":30000}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":9,"phase":"GAME_OVER","resume_phase":null,"turn_id":null,"active_player_id":null,"allowed_actions":[],"self":{"player_id":"P1","inventory":0},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"SNAPSHOT","reconnect_remaining_ms":null}}\n
{"version":1,"msg_type":"GAME_OVER","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":8,"winner_id":"P2","loser_id":"P1","reason":"RECONNECT_TIMEOUT"}}\n
```

The LOAD and END_TURN examples are alternatives, not two requests to execute in succession. The final two examples show result recovery: a revision-9 snapshot accompanies the already frozen revision-8 outcome. These are separate from the earlier forfeit example. Byte example: the CONNECT frame ends with hexadecimal 7D 7D 0A (two closing braces, then LF).


### 7.1 Connected-session revision trace

This trace assumes both joins succeed without connection events, P1 starts, and all actions are valid. It gives the wire examples' expected order; messages for each recipient use their private snapshot.

| Step | Client input / server publication | Resulting revision, turn, phase |
|---|---|---|
| P1 joins | CONNECT -> WELCOME, LOBBY_WAIT, STATE_UPDATE | 1, null, WAITING_FOR_PLAYERS |
| P2 joins | CONNECT -> WELCOME to P2; initialize then GAME_START and STATE_UPDATE to both | Seat addition 2, initialized snapshot 3, turn 1, TURN_CHOICE |
| P1 dares | MOVE PASS with revision 3 / turn 1 -> STATE_UPDATE | 4, turn 2, FORCED_REPLY |
| P2 survives | MOVE TRIGGER with revision 4 / turn 2 -> STATE_UPDATE | 5, turn 2, LOAD; P2 inventory 1 |
| P2 loads | MOVE LOAD count 1 with revision 5 / turn 2 -> STATE_UPDATE | 6, turn 3, FORCED_RETURN; P2 inventory 0 |
| P1 quits | DISCONNECT -> terminal STATE_UPDATE, GAME_OVER, then acknowledgement to P1 | 7, null, GAME_OVER; P2 wins FORFEIT |

## 8. Conformance and related documents

Protocol behavior is checked by the message schemas, requirements A1–A5/F1–F6/R1–R8/H1–H7, transport cases C1–C18, heartbeat cases HSC1–HSC12, and FSM scenarios S1–S23 in [fsm_specification.md](fsm_specification.md). These are expected behaviors for future implementation verification, not claims of tests already executed.

Minimum coverage includes fragmented/coalesced frames; the 4,096/4,097 byte boundary; UTF-8 byte counting; missing/extra/wrong-type fields; invalid directions and states; forced-pass rejection; loading limits; stale revisions after a lost response; recovery during every phase; exact timeout boundaries; terminal-result immutability; and private-field exclusion.

The reusable agent prompt, specification commit pinning, independent test expectations, and requirement-to-code-to-evidence reporting are maintained separately in [prompt_management.md](prompt_management.md). The [SOW](CS457_AndrewBarton_TermProjectSOW.md) provides course scope and later-sprint planning.
