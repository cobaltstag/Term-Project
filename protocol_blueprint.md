# Application Protocol Blueprint — v1

**Project:** CS 457 Term-Project  
**Author:** Andrew Barton (design decisions developed with ChatGPT)  
**Updated:** 2026-10-04  
**Status:** Sprint 1 design; final rubric alignment and October 4 decisions incorporated. Implementation verification remains future work.

## 1. Scope and decision status

The confirmed design uses two players, server-controlled state, independent random chamber sampling, the forced-shot dare sequence, banked inventory, and the private repeating survival reward cycle. Intentional departure forfeits; interrupted connections permit reconnection. UTF-8 JSON with newline framing is selected. Application-level, bidirectional PING/PONG heartbeats use a ten-second deadline. A matching PONG or qualifying incoming gameplay progress (H4) completes the receiver's outstanding probe. This deadline verifies connection responsiveness and never limits how long a player may think.

Multiplayer expansion, UI tutorials, persistent scores, server-crash recovery, and program implementation are outside this document. Language and concurrency-library selection belong to later SOW sprints. Reconnection supports an unfinished match only; recovering a missed terminal result is deliberately unsupported.

### 1.1 Lab endpoint

Use TCP at the lab DNS name `server.barton.edu`, default port `45700`. The server binds its configured CML interface (or all local interfaces); clients use that DNS name and the same port. Host and port may be overridden consistently through configuration for testing. The port is a project-local default, not a claim of an assigned Internet service. Application messages contain no hostname or port fields.

## 2. Authority and visibility

**A1.** The server alone samples chambers, awards rewards, deducts inventory, places/removes bullets, assigns turns, and resolves outcomes. A client submits intentions.

**A2.** Each player has a private inventory and reward position. Successful shots award 1, 2, 4, 5, then repeat at 1. Award first, advance second. Loading does not change reward position.

**A3.** Send a player their own inventory only. Never send either reward position, future reward, cylinder array, sampled index, loaded count, opponent inventory, opponent reward amount, or opponent loading quantity. Reward amounts can be inferred from a player's own inventory changes; that discovery is intentional.

**A4.** LOAD and END_TURN generate the same public TURN_READY notification. Error feedback can still permit deductions about capacity; the design prevents direct disclosure, not all inference. LOAD_LIMIT must not report the number of empty chambers.

**A5.** Bind player identity to the server's current connection/session. MOVE has no client-controlled player_id. A resumed session replaces its old connection; old connections are fenced from further mutations.

## 3. Serialization and framing — newline-delimited JSON

**F1.** TCP carries UTF-8 JSON objects. Exactly one object occupies each line. Append exactly one LF byte (0x0A) after the closing object; do not send CRLF, a BOM, blank lines, pretty-printed multiline JSON, or trailing whitespace.

**F2.** Maximum: 4,096 bytes before 0x0A, measured after UTF-8 encoding. It is a ceiling, not a padded allocation or minimum. A 150-byte object transmits 151 application bytes including 0x0A.

**F3.** Retain incoming bytes until 0x0A. For each complete line, enforce the limit, decode strict UTF-8, and parse one complete JSON object. Keep any unfinished suffix. Drain all complete lines in order. Reads and TCP packets are not message boundaries.

**F4.** A line of exactly 4,096 bytes followed by 0x0A is valid-sized. More than 4,096 bytes before 0x0A is fatal, even if no delimiter has yet arrived. EOF discards an incomplete suffix; it never executes as a message.

**F5.** Reject invalid UTF-8, invalid JSON, duplicate object keys, non-object roots, NaN/Infinity, raw embedded 0x0A, and CR bytes in a frame. Escaped string content such as backslash-n is allowed and is not a delimiter. Object key order has no significance.

**F6.** At the server, a complete LF-delimited line with malformed UTF-8/JSON/framing receives nonfatal MALFORMED_FRAME; discard that line, leave game state unchanged, and process subsequent complete frames normally. A well-framed schema/action error is also nonfatal. The client may correct its input or try joining again; no player/address blacklist is maintained. Exceeding 4,096 bytes is fatal FRAME_TOO_LARGE, including when no LF has arrived: attempt ERROR, then close under R6. For an assigned player this fatal closure is an interruption, not an intentional forfeit.

References: [JSON, RFC 8259](https://www.rfc-editor.org/rfc/rfc8259); [TCP, RFC 9293](https://www.rfc-editor.org/rfc/rfc9293). The LF newline delimiter and size limit are this application's rules, not TCP or JSON requirements.

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

Payload: empty object. Valid only as the successful initial handshake on an unbound connection; earlier nonfatal malformed/schema errors do not bind it or prevent a corrected attempt within R8's original deadline. Assign the lowest available seat (P1 before P2), initialize its inventory/reward position to zero, and send WELCOME before any post-binding notification or heartbeat to that connection. Earlier errors on an unbound connection are allowed. If the lobby has no free seat, or the current match is running/closing, send ROOM_FULL and close this unbound connection. No state is restored through CONNECT. Each accepted join increments the revision once. If still waiting, send lobby notifications/snapshots; if both seats are online, execute match initialization as one additional revision. Thus initial joins produce revisions 1 and 2, and initial GAME_START snapshots have revision 3.

### RECONNECT — client -> server

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Must identify the current nonterminal lobby/match. |
| session_token | SessionToken | Must identify a current session: ONLINE, or DISCONNECTED strictly within its grace deadline. |

Valid as the successful initial handshake on an unbound connection. Invalid/expired credentials or a terminal/cleaned-up match receive RESUME_DENIED and closure of only the new connection. No terminal result is returned.

**Valid credentials immediately replace an old ONLINE connection.** Atomically fence the old generation, invalidate its pending callbacks/input, discard its buffers, bind the new socket, increment state_revision once, and send WELCOME then the current authorized STATE_UPDATE. Close the old transport without treating that replacement as a forfeit or a new interruption. Preserve turn, phase, inventory, cylinder, and dare obligations. The opponent receives a current snapshot as well. If replacing an ONLINE binding, use SNAPSHOT and do not introduce a pause or a grace period. If resuming a DISCONNECTED seat, cancel its old grace timer; restore the saved active phase only when both seats are now online, using RESUMED. If the other seat is still absent, remain PAUSED and use SNAPSHOT. A fully online lobby starts once under the initialization rules.

Only one connection owns a seat at any instant. A subsequent valid replacement fences its predecessor by the same rule; a stale callback can never detach the newest binding. No automatic reconnect loop is prescribed for a displaced client (R8). Never recreate inventory, reroll a shot, award a reward, or clear forced-turn obligations during reconnection.

### PING — either direction

| Payload field | Type | Constraint |
|---|---|---|
| probe_id | Integer | Positive; generated by this sender and monotonically increasing within its current connection generation, starting at 1. |

Both endpoints can initiate probes, independently. Accept PING only after session binding/WELCOME, on a live non-closing connection, in any lobby, gameplay, or PAUSED phase. No new probes are started once GAME_OVER initiates closure. The receiver queues a PONG with exactly the received probe_id promptly through the same serialized output stream. This is automatic background handling; no player input, turn ownership, game revision, or gameplay action validation is required.

A well-framed duplicate PING can be answered again; it does not complete the receiver's own pending probe or reset any local deadline. Malformed PING fields follow INVALID_SCHEMA handling. An unbound PING receives HANDSHAKE_REQUIRED from the server and closure.

### PONG — either direction

| Payload field | Type | Constraint |
|---|---|---|
| probe_id | Integer | Positive; must echo the specific peer PING being answered. |

A PONG completes a local pending probe only when msg_type is PONG, the identifier matches that probe, the connection generation matches, and it is validated strictly before the armed response deadline (or satisfies the early-response case in H3). Separately, qualifying incoming gameplay progress can complete that same pending probe under H4. This does not turn a MOVE into a PONG or relax PONG identifier matching.

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

TURN_CHOICE accepts TRIGGER or PASS; FORCED_REPLY and FORCED_RETURN accept TRIGGER only. LOAD and END_TURN are accepted only in LOAD. The sending session must own the active turn. PASS is never a substitute for END_TURN.

One accepted LOAD deducts exactly count, fills that many distinct empty chambers chosen randomly by the server, then hands over the turn. END_TURN hands over without spending. Both preserve reward position. Invalid requests spend nothing and retain the phase.

Validate serially in this order: envelope/schema; session; match active/not paused; revision; turn; sender active; action allowed; count limits. Apply an accepted action and all its effects atomically before processing another event. No automatic MOVE retries are part of v1: after interruption obtain a fresh snapshot before issuing any new action. Only current-session client MOVE enters this validation sequence; transport controls use their own guards. A duplicate accepted action is never executed twice. While the connection remains eligible for input, it receives MATCH_NOT_ACTIVE or MATCH_PAUSED when those earlier guards apply, otherwise STALE_STATE for its old revision. If the original action already initiated terminal closure, discard later application frames under R2 instead of attempting another error.

### STATE_UPDATE — server -> each connected assigned client individually

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Current match/lobby. |
| state_revision | Revision | Current authoritative revision. |
| phase | Phase | Current state. |
| resume_phase | Phase or null | Previous active phase when PAUSED; otherwise null. When non-null, exactly TURN_CHOICE, FORCED_REPLY, FORCED_RETURN, or LOAD. |
| turn_id | Integer or null | TurnId during active/paused match; null before start or after terminal outcome. |
| active_player_id | PlayerId or null | Preserved during pause; null in lobby/terminal state. |
| allowed_actions | Array of strings | Unique legal action names, ordered TRIGGER/PASS or LOAD/END_TURN as applicable. Empty for any recipient who is not the active player, or while paused, waiting, or terminal. |
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
| state_revision | Revision | Frozen revision at terminal outcome; matches the preceding terminal snapshot. |
| winner_id | PlayerId or null | Winner; null for aborted match. |
| loser_id | PlayerId or null | Loser; null for aborted match. |
| reason | String | SHOT, FORFEIT, RECONNECT_TIMEOUT, or MATCH_ABORTED. |

SHOT/FORFEIT/RECONNECT_TIMEOUT have distinct winner and loser IDs. MATCH_ABORTED has both null. Send a GAME_OVER snapshot first, then GAME_OVER, then the applicable DISCONNECT notification/acknowledgement. Begin bounded closure under R4/R9. Terminal results cannot subsequently change, and reconnecting to retrieve them is unsupported.

### DISCONNECT — either direction

Client -> server: payload is empty. In an active or paused match, the sending player forfeits immediately, regardless of whose turn it is. Send terminal updates/results to writable peers, then send the departing client a DISCONNECT acknowledgement when possible and close it. In a lobby, release their seat; in a terminal match, close without changing the result.

Server -> client payload:

| Payload field | Type | Constraint |
|---|---|---|
| reason | String | CLIENT_REQUEST or MATCH_CLOSED. |

This is acknowledgement/closure notification, not a client gameplay command. A server closure cannot manufacture an additional player forfeit. After a terminal outcome, writable peers receive terminal snapshot, GAME_OVER, then DISCONNECT/MATCH_CLOSED; a client whose explicit quit caused the outcome receives DISCONNECT/CLIENT_REQUEST instead as its final acknowledgement. A queued message is not a guarantee of delivery.

### ERROR — server -> offending client only

| Payload field | Type | Constraint |
|---|---|---|
| code | String | Code from the table below. |
| detail | String | Human explanation, 1–160 Unicode code points, with no secrets. |
| fatal | Boolean | Whether this connection will be closed. |

| Code | Meaning | Fatal |
|---|---|---|
| MALFORMED_FRAME | Invalid complete LF-delimited framing/UTF-8/JSON/root/duplicate keys; discard this line. | false |
| FRAME_TOO_LARGE | More than configured byte ceiling. | true |
| UNSUPPORTED_VERSION | Version other than 1. | true |
| INVALID_SCHEMA | Missing, extra, wrong-type, or invalid-enum field. | false |
| HANDSHAKE_REQUIRED | MOVE/DISCONNECT before binding; or inappropriate unbound type. | true |
| ROOM_FULL | No available seat. | true |
| RESUME_DENIED | Invalid or expired resume credential. | true |
| MATCH_NOT_ACTIVE | No active game or already terminal. | false |
| MATCH_PAUSED | Gameplay frozen for reconnection. | false |
| STALE_STATE | Revision mismatch. | false |
| STALE_TURN | Turn mismatch after revision passed. | false |
| NOT_YOUR_TURN | Sender does not own active turn. | false |
| ACTION_NOT_ALLOWED | Action invalid in current phase; includes repeated CONNECT/RECONNECT on bound connection. | false |
| LOAD_LIMIT | Count exceeds inventory or cylinder space; do not say which hidden capacity failed. | false |

Here fatal means close the offending connection, not immediate player forfeit; an assigned session follows R5/R6 unless a terminal/intentional-close result already exists. Version must first be a syntactically valid integer: wrong type is INVALID_SCHEMA, while an unsupported integer is UNSUPPORTED_VERSION. Unknown message names/fields are INVALID_SCHEMA. A known server-only type sent by a bound client is ACTION_NOT_ALLOWED; a correctly formed type other than CONNECT/RECONNECT on an unbound connection is HANDSHAKE_REQUIRED. Nonfatal unbound errors permit a corrected handshake before the original five-second deadline. Clients do not send ERROR messages: malformed/wrong-direction server input causes client-side retirement without an ERROR loop. On ERROR, display its code/detail; fatal errors close that transport, while nonfatal errors leave it usable. The player can decide to try again. No per-code automatic repair or retry procedure is required.

After a nonfatal MOVE error send the sender a fresh STATE_UPDATE with event SNAPSHOT. Schema errors without a bound session receive ERROR only. Count <= 0 or noninteger is INVALID_SCHEMA, not LOAD_LIMIT.

### 5.1 State ordering and publication

On every accepted gameplay transition, update all authoritative fields and increment state_revision exactly once before constructing per-recipient snapshots. Snapshot replies to errors do not increment it. Heartbeat success never increments it. A connection-status change has its own revision; serialize it with gameplay. Frame order on each connection follows that commit order.

Lobby snapshots use inventory 0, turn_id/active_player_id/resume_phase null, no allowed_actions, and phase WAITING_FOR_PLAYERS. Occupied seats, including reserved disconnected ones, appear in players. A seat addition/removal/resume/loss increments revision and notifies existing connected peers with a current snapshot. LOBBY_WAIT accompanies remaining lobby waiting; do not start until both seats are online.

Entering GAME_OVER sets phase GAME_OVER, active_player_id/turn_id/resume_phase null, allowed_actions empty, clears transient load/dare/pause obligations and all gameplay grace timers, and preserves inventories for private snapshots. Record the terminal outcome/revision once. Send terminal STATE_UPDATE before GAME_OVER to each writable peer. Invalidate resume credentials immediately at terminal resolution, mark associated connections closing, and stop accepting gameplay/probes. Keep immutable final output only long enough for R4's bounded delivery/close attempts. A failed send or disconnect during that period cannot reopen grace, alter the result, or enable result recovery. After all those connections close or their budgets expire, clear match state and create a fresh lobby. This bounded flush is not a terminal-result retention service.

A client applies complete snapshots atomically and discards older revisions on the same match. Equal revisions are permitted for repeated snapshot delivery. A GAME_OVER snapshot disables gameplay; the separate GAME_OVER message identifies the winner/reason. If that result never arrives before closure, display that the match ended but its result was not received. Do not infer a winner or open a result-recovery connection. The same connection's pending local MOVE is never automatically replayed.

## 6. Connection interruption and forfeit

Recovery policy:

1. At detected EOF/transport failure on the current bound connection, mark the player DISCONNECTED and start their 30-second monotonic grace. Ignore events from superseded connections.
2. Pause active gameplay and save the exact prior phase, active player, turn, inventory, cylinder and dare obligations. Advance the revision for the connection change and notify the connected opponent with an authorized snapshot.
3. In a nonterminal lobby/match, accept valid RECONNECT before any applicable grace expiry. Valid credentials can also immediately replace an ONLINE connection as section 5 specifies. Restore active play only when both seats are online; send current snapshots to both. Reconnection never rolls back the revision or replays a move.
4. A second connection loss gets its own deadline. Invalid resume attempts do not extend it. Repeated valid interruptions get a new grace window; an anti-stalling budget is outside v1.
5. Before resolving due grace timers, process all currently due heartbeat failures. Then resolve the earliest expired grace deadline. If the absent player's opponent is online, award that opponent RECONNECT_TIMEOUT; if both are disconnected, record MATCH_ABORTED with no winner. This order also applies when heartbeat and grace deadlines are equal.
6. Lobby interruption reserves the seat for the same grace window; expiry releases it without a match outcome. Both seats must be online before starting.
7. An accepted intentional DISCONNECT during active/paused play bypasses grace: the sender loses and the opponent wins even if absent. Publish the result to connected players only; an absent player cannot later retrieve it.
8. GAME_OVER invalidates reconnect eligibility immediately. Attempt terminal output/closure within the existing five-second budgets, then CLEANUP opens a new lobby. There is no post-game result recovery or separate 30-second terminal-retention window.

Deadline eligibility uses server monotonic time when serialized handling checks the event, not a client timestamp. Before accepting a new command/handshake, settle due heartbeat failures and then due grace expiry as above; equality is expired. Once an outcome is committed, subsequent timer/connection events cannot change it. During ordinary play, each accepted command and its effects commit before the next command.

**Deliberate limitation:** a client that disconnects after the match ends may never learn who won, including when it received only the terminal snapshot. Another player may know the result, but the server provides no lookup/recovery feature. S23 verifies rejection of post-game reconnects.

A server process crash and recovery from disk are outside v1. Grace begins at detection, not at the unknowable instant the physical connection failed. A heartbeat timeout without matching PONG or qualifying gameplay progress is another loss detector; section 6.7 specifies it. The ten-second heartbeat deadline is not the reconnection grace period.


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
| Ten-second deadline expires without matching PONG or qualifying incoming gameplay progress (H4) | Heartbeat-detected unresponsive connection | Invoke R5 exactly once; apply interruption/grace, never immediate forfeit. |
| Other OSError from established-session read/write, after recoverable conditions above have been excluded | Unclassified socket failure | Log operation and numeric error; detach safely using R5. Never silently continue on a potentially unusable socket. |
| EOF/error/timeout before a CONNECT or RECONNECT has successfully bound the new connection | Unbound connection failure | Close that connection only. Do not invent a player, alter a reserved existing session, or start a new grace period. |
| Later EOF/error after processed DISCONNECT, server closure, terminal resolution, or detachment | Duplicate/expected closure event | No second outcome or grace reset. During orderly closure, EOF ends only receiving; finish bounded final output under R9 before close when possible. Terminal loss only finishes cleanup under R5. |

Short receives with positive byte counts are ordinary data, not EOF. A readiness notification alone is not EOF; the actual receive result determines it. An accept/connect failure affects the listener or new connection, not an existing player's game. Nonblocking connect-in-progress is not a successful binding or a player loss.

### 6.2 Receive ordering and incomplete data

Every accepted socket has a receive handler, including while waiting for CONNECT or RECONNECT. After a positive-size receive, explicitly test `if not data: break` (or the event-loop equivalent: unregister this socket and return). That exit must invoke the appropriate one-time R5/R6 cleanup; it must not merely leave a registered EOF socket ready to spin. It exits only this connection's receive loop, never the server's listener/accept loop. An empty application buffer while waiting for more bytes is not EOF, and would-block/timeout exceptions are not empty receives.

**Extraction contract for back-to-back messages:**

1. Append each nonempty receive to that connection's byte buffer. Do not decode individual chunks: UTF-8 characters can cross reads.
2. While an LF is present, take only the bytes before the first LF as one frame, remove that frame plus its LF, enforce its byte limit, decode, validate, and dispatch it.
3. Dispatch in stream order and finish each atomic transition before validating the next frame against the resulting state. Recheck closing status and applicable deadlines between frames; preserve order if yielding to other connections/timers.
4. Repeat for all complete frames. Retain only the incomplete suffix for the next read, enforcing its 4,096-byte ceiling even without LF. A receive containing several legal frames may exceed 4,096 total bytes; the ceiling applies separately to each frame.
5. Stop dispatching at a processed DISCONNECT, a terminal outcome that starts closure, or fatal protocol failure. Discard trailing application frames on that closing connection. Closing-only reads may drain/discard transport bytes under R9; they cannot execute commands.

For example, one read may contain a complete MOVE line, a complete PONG line, and half the next JSON object. Dispatch MOVE then PONG, retain the suffix, and complete it on the next read. If the MOVE qualifies under H4, its late PONG is now ignored. Conversely, PONG followed by MOVE completes the probe first, then processes the action. A second back-to-back MOVE still needs a current revision, turn, and legal phase; batching never bypasses validation. Writers queue complete LF-terminated frames through one serialized stream, so `frame1 + frame2` needs no extra separator and frame bytes never interleave.

**R2.** Complete frames already obtained from the stream must be validated and processed in stream order before that stream's EOF event is resolved, while the connection remains eligible to accept input. Stop at processed DISCONNECT, terminal closure, or a fatal protocol error and ignore subsequent application frames from that closing connection. Do not attempt to drain an unread/reset socket after declaring a fatal error.

An incomplete final suffix is discarded without executing it or manufacturing a MALFORMED_FRAME reply to a closed peer. Thus a valid DISCONNECT frame followed by FIN remains an intentional forfeit; a partial DISCONNECT followed by FIN follows interruption policy. If a successful MOVE precedes EOF, its atomic effects remain committed before the interruption. A later socket error may prevent bytes still in the network/OS from being obtained; do not claim all sent commands were received.

### 6.3 Failed writes and delivery uncertainty

**R3.** Gameplay commit and output delivery are separate. Failure to send a result must not undo, repeat, or resample the accepted MOVE. The client's next successful RECONNECT receives the committed snapshot. Neither local write success nor queueing a message proves that the other application processed it.

For a partially written frame, retain known offsets only while the same live connection remains in use. After connection failure discard its outgoing bytes, including any unfinished frame; never transfer that suffix to the new connection. A reconnect begins a new stream with WELCOME and a complete fresh snapshot.

If a send-all API raises, the delivered byte count can be unknown. Do not restart the same frame from byte zero on that stream or automatically replay its associated action. Retire the failed connection and recover by snapshot. An ERROR or DISCONNECT acknowledgement whose send fails is best-effort; its failure never changes a previously committed forfeit or terminal result.

**R4 — Bounded output.** Each outgoing frame has a five-second monotonic output budget beginning when it is queued for transmission, including queue residence and writes; partial progress does not restart it. This prevents a queued heartbeat from waiting indefinitely before its response timer can begin. A transient would-block observation before this deadline is recoverable. Deadline expiry classifies the connection as failed output. A server-requested close also has a five-second total flush budget beginning when closure is decided; queue final control/result frames in order, then shut down writes when output completes and drain to EOF within the remaining budget as R9 specifies. Close on drained EOF, a fatal I/O error, or budget expiry. Use the earlier applicable deadline. Do not wait indefinitely for a peer acknowledgement or FIN. No blocking network I/O or flush may hold the authoritative-state lock or prevent the server processing another session's events/timers.

The terminal flush/close budgets begin at outcome commitment and do not restart on partial progress or later errors. This budget bounds output attempts; heartbeat probes provide traffic and a separate heartbeat-completion deadline to detect quiet unresponsive connections. It is distinct from reconnection grace and receive polling; there is no separate terminal-result recovery period.

### 6.4 One-time connection retirement and cleanup

**R5.** For a current bound active connection, atomically:

1. Verify the session binding/generation still identifies this connection and it has not already been retired. Stale callbacks may clean up only their own obsolete transport resources; they must not change the current session binding or gameplay, even if an OS descriptor number has been reused.
2. Mark it retired and remove it from gameplay input/readiness registration before closing; subsequent reads/writes/callbacks cannot mutate the session.
3. Preserve authoritative match data. If there is no prior intentional departure, cleanup closure, or terminal result, mark the player DISCONNECTED and set their grace deadline exactly once from the original loss-detection time. For active play, save the active phase/context and enter PAUSED. If already PAUSED for the other player, preserve the original saved phase and give only the newly absent player a deadline.
4. For a failed transport remove its buffers/queues. R6's writable diagnostic-close exception may retain output owned only by that closing socket until its bounded flush finishes. Queue authorized snapshots for remaining peers; process any resulting peer write failure as a separate session loss rather than recursively performing network I/O inside this transition.
5. Best-effort shutdown and close a failed transport explicitly. For R6's diagnostic-close exception, schedule the bounded output-only closure instead of disposing of its retained output immediately. Log cleanup errors; they cannot escape cleanup, restart grace, alter a match result, or prevent cleanup of the other connection.

Unbound connections skip all match/session mutation. Lobby sessions use the reserved-seat policy. After terminal resolution, transport loss only finishes cleanup: preserve the final snapshot/result and do not start grace or publish new revisions. Intentional departures set LEFT; releasing a lobby seat removes it from players instead. Cleanup is idempotent: EOF followed by write failure, multiple worker callbacks, or shutdown followed by close cannot create multiple loss transitions. The first detected loss time controls grace even if cleanup or notification fails.

**R6.** Mark server-requested closing connections as closing, stop accepting their input, and record the closure cause before flushing/closing. After processed client DISCONNECT, outcome/seat release is committed before final messages are attempted. After fatal protocol validation, an assigned active/lobby session follows the existing interruption policy immediately, while a single diagnostic ERROR may be flushed within R4; stop parsing subsequent frames. This is a deliberate exception to R5's immediate transport disposal: the session is logically detached now, but the still-writable closing socket owns its remaining output until the bounded flush ends. It cannot accept gameplay or restart grace; bounded input draining is permitted solely for R9 cleanup. If an outgoing frame was already partially written, finish its remaining bytes before the diagnostic; if that cannot complete within budget, skip the diagnostic and close. No diagnostic may be inserted into the middle of an unfinished frame. A later resume may replace the session binding before this flush finishes; old-socket cleanup still cannot affect the new binding. After normal match cleanup, never reopen grace for a resulting EOF or exception.

Expected already-disconnected/already-closed errors during shutdown/close are cleanup diagnostics, not new failures. Application coding errors, signal-driven cancellation, listener failures, and process shutdown must not be blanket-caught and relabeled as player forfeits. Surface internal faults to server diagnostics; server-crash persistence remains outside v1.

### 6.5 Client-side termination contract

**R7.** A client that detects unexpected EOF/fatal established-socket error before learning of a terminal/closure state stops gameplay, discards that connection's partial buffers, and retains its credential for a possible nonterminal RECONNECT. It cannot declare itself or the opponent the winner locally. After a successful RECONNECT, wait for WELCOME and STATE_UPDATE before issuing a new MOVE; never automatically resend a potentially accepted action.

A player choosing to quit, or a controlled normal exit from a bound session, disables further actions/probes and recovery. Remove queued gameplay frames that have transmitted no bytes. Preserve any already-started frame's ordering, then send the complete DISCONNECT and call sock.shutdown(socket.SHUT_WR). Receive final messages and EOF within a five-second total quit budget measured from the quit request; then call sock.close(). On I/O failure or budget expiry, close through best-effort cleanup. Do not rely on interpreter exit to send DISCONNECT.

**Framing constraint on quitting:** the server cannot execute a MOVE until its complete LF-terminated frame arrives. An abandoned partial frame is discarded on EOF. However, appending DISCONNECT bytes directly to that unfinished frame does not create a separate valid message. To communicate explicit quit on the same stream, complete any already-started frame first, then send DISCONNECT; an accepted earlier MOVE can therefore resolve before the quit. If the client instead closes with only a fragment, the server uses interruption/grace because no valid quit was received. Local intention cannot substitute for a received application message.

On terminal STATE_UPDATE, GAME_OVER, or server DISCONNECT, disable gameplay and further reconnection. A received GAME_OVER fixes the displayed outcome; a terminal snapshot without the result leaves the outcome unknown if the connection closes. There is no additional result-recovery timer or request. Receiving CLIENT_REQUEST or MATCH_CLOSED leads to R9 closure. A responsive player may still take time to think during nonterminal play; heartbeats continue independently.

Client command validation is tied to the prompt's connection generation, turn_id and state_revision. A syntactically valid action from an obsolete prompt must not be silently stamped with the newest revision. Invalid commands execute nothing; discard obsolete input and present the current permitted choices.

**R8 — Bounded handshake and player-directed retry.** On each accepted TCP connection, the server allows five seconds to validate and bind CONNECT or RECONNECT. Nonfatal malformed/schema errors may be corrected on that unbound socket; they do not reset the original deadline. Expiry closes only the unbound connection. A client allows five seconds for a connect attempt, then five seconds for WELCOME plus the initial STATE_UPDATE; failure closes that attempt and reports the error.

A player may try again after an error. The protocol specifies errors and eligibility, not a mandatory automatic repair/backoff loop. Use RECONNECT with an existing credential for the unfinished session; CONNECT requests a new lobby seat. No failed attempt extends server grace, and no client-side retry timer promises to match the server's clock. Never replay an old MOVE. Automatic reconnect attempts must not be used to compete with a valid replacement binding.

If the initial WELCOME was never received, the client has no session token and cannot resume that seat; it can make a fresh join attempt and may receive ROOM_FULL until the reserved seat is released. This is a known limitation. Terminal or expired credentials receive RESUME_DENIED; no old result is returned. Closing/rejecting an individual transport does not blacklist the player or prevent a later fresh connection.

### 6.5.1 Orderly shutdown and TCP observation

**R9 — Explicit close lifecycle.** FIN and ACK are TCP control flags generated/processed by the OS, not JSON message types and not values returned by recv(). A normal FIN close terminates the two directions independently; the usual FIN, ACK, FIN, ACK exchange can combine acknowledgements into fewer packets. The application observes EOF after preceding stream bytes have been consumed. A direct close or process exit does not prove graceful application departure or guarantee a four-packet trace, especially on failure or with unread data. See [RFC 9293 section 3.6](https://www.rfc-editor.org/rfc/rfc9293.html#section-3.6) and the [Python socket API](https://docs.python.org/3/library/socket.html#socket.socket.shutdown).

The following is the expected successful client-initiated quit sequence, not additional wire message schemas:

| Step | Client application / TCP | Server application / TCP |
|---|---|---|
| 1 | Controlled quit/normal exit enters R7; flushes DISCONNECT plus LF within the existing budget. | Receives bytes and dispatches the complete DISCONNECT in stream order. Commits forfeit/seat release or unchanged terminal result; marks connection closing. |
| 2 | Calls sock.shutdown(socket.SHUT_WR) after the final application frame is fully submitted. Client TCP sends FIN after earlier bytes. | Server TCP acknowledges FIN. After draining preceding bytes, a positive-size recv() returns b''; the handler exits receiving. Already processed DISCONNECT means no new grace. |
| 3 | Keeps its receive half open for terminal snapshot/result and DISCONNECT acknowledgement. | Flushes applicable final frames under R4; then calls sock.shutdown(socket.SHUT_WR). Server TCP sends its FIN. |
| 4 | Client TCP acknowledges server FIN. Client receives remaining application frames then EOF and calls sock.close(). | Once output is finished and inbound EOF observed, calls sock.close(); otherwise drains/discards remaining input until EOF or the existing close budget ends. |

The streams are independent: the server may finish its response/send-side shutdown before its application observes client EOF. The outcome does not depend on that ordering. R6 forbids further command dispatch once closing, but permits bounded transport draining solely for orderly cleanup. A valid DISCONNECT followed by EOF still receives final output when possible; EOF must not prematurely discard that closing socket's output queue. Both sides use the earlier applicable output/quit/close deadline and explicitly close on expiry or error; no wait for FIN or TIME_WAIT may block other players or timers. Shutdown errors are logged and cannot prevent close.

Server-initiated MATCH_CLOSED uses the same bounded sequence: flush notification, shutdown the sending direction, drain to EOF within R4, then close. On receipt, the client stops gameplay/reconnection, stops writes, shuts down its sending direction, and drains/closes within five seconds of notification (or an earlier already-running quit budget). An active client that calls only sock.close() or exits without a processed DISCONNECT produces EOF/reset interruption policy at the server; FIN alone cannot establish intent.

### 6.5.2 Exception boundaries and grace timer

**R10 — Classify at the socket boundary.** Receive and serialized-send handlers catch socket exceptions around the I/O operation, with its connection generation and operation recorded. Handle specific recoverable/fatal cases in R1 before a fallback OSError handler. Do not wrap gameplay dispatch in a blanket exception handler that mistakes a coding error for a peer disconnect.

- **ConnectionResetError:** the connection was reset. Stop using it and invoke R5 once; do not wait for a heartbeat.
- **BrokenPipeError:** writing failed on a closed or locally write-shut-down stream. On an otherwise live connection invoke R5 once. On an already closing/retired connection perform cleanup only; a write attempted after local shutdown also merits a local diagnostic, not a claim that the opponent quit.
- **TimeoutError / socket.timeout:** classify by operation and configured timer. Receive polling expiry only wakes the loop to check timers. An established write timeout or OS connection timeout is loss; a connect/handshake timeout closes only that unbound attempt. These are not interchangeable with gameplay grace expiry.
- Other exceptions follow R1/R6; ensure cleanup runs even if the handler exits by an exception. Do not send an ERROR over a socket already classified as failed.

A server-side failure of P1's current socket makes P1 absent and informs P2 through the authoritative snapshot. A client-side failure concerns that client's connection to the server; it cannot diagnose the other player's connection or declare their loss.

**Grace is retained-session state, not a 30-second recv() on the failed socket.** R5 closes that socket immediately and records `grace_deadline = loss_detected_at + 30 seconds` using monotonic time. The server's independent timer scheduler checks deadlines even with no incoming network traffic. At expiry it serializes a grace-expired event, rechecks session generation/status/deadline, then applies lobby release or FSM T11/T12. Successful resume invalidates the old timer; duplicate/stale timer callbacks do nothing. Do not require or manufacture a generic socket TimeoutError to trigger this transition. If gameplay grace expiry resolves the match, invalidate tokens immediately and finish only bounded final output/closure before clearing match state. No post-game recovery window starts.

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
| C15 | Valid resume credential while old connection is still ONLINE | Atomically replace/fence old binding; WELCOME and current snapshot; no forfeit or new pause. |
| C16 | Match ends, then absent player tries RECONNECT | RESUME_DENIED; no terminal result recovery, no game restart. |
| C17 | Unbound client never completes handshake, or bound client never receives initial synchronization | Five-second handshake/sync timeout; no invented seat/result or automatic MOVE replay. |
| C18 | Outgoing frame remains queued without a first write attempt | Five-second budget still expires; no indefinitely queued probe. |
| C19 | Controlled client exit flushes DISCONNECT, shuts down writes, and drains EOF | Server commits intent before EOF; final output is attempted; both sockets explicitly close within their budgets. |
| C20 | EOF while waiting for CONNECT/RECONNECT, then a new client arrives | Per-connection receive loop exits once; accept loop stays available; existing reservations are unchanged. |
| C21 | Several valid frames total more than 4,096 bytes in one read, followed by a partial frame | Enforce limits per frame; dispatch complete frames in order, retain suffix, and revalidate each against current state. |
| C22 | No traffic for 30 seconds after a detected loss, or stale grace callback after resume | Independent scheduler expires the absent session; a stale callback cannot end the resumed match. |
| C23 | BrokenPipeError after local orderly shutdown vs on a live stream | Closing case only cleans up/logs; live case retires once; neither proves intentional opponent departure. |
| C24 | P1 grace and P2 heartbeat are both due in one server dispatch | Apply P2 heartbeat loss first; both absent means MATCH_ABORTED, not a timeout win for P2. |
| C25 | Unbound connection sends malformed complete line, then valid CONNECT before original deadline | ERROR for the line, then successful handshake; no blacklist or deadline extension. |
| C26 | Incomplete MOVE is abandoned by socket close before LF | Discard fragment; no shot. Without a separately received complete DISCONNECT, use interruption/grace, not inferred quit. |

API references: [Python socket HOWTO](https://docs.python.org/3/howto/sockets.html), [socket API including partial sends and sendall failure](https://docs.python.org/3/library/socket.html), and [OS exception classifications](https://docs.python.org/3/library/exceptions.html). EOF and exception semantics come from the socket API; half-close rejection, timeout classification, output budgets, and game consequences are application design decisions.


### 6.7 Heartbeat scheduling, correlation, and expiry

**H1 — Connection control, not game state.** Both endpoints send PING and answer PING with PONG in the background. Heartbeats are bound to the existing session/connection; they contain only probe_id. Completing a probe does not itself change phase, inventory, turn_id, reward position, or state_revision. A qualifying gameplay message still has its normal gameplay effects. Expiry changes connection status through R5 and may pause the match and advance its revision.

**H2 — Scheduling.** Maintain five-second monotonic scheduling ticks per bound connection. Start the server's schedule after successful CONNECT/RECONNECT binding; start the client's after WELCOME. The first probe opportunity is five seconds later. At a tick, create/send a probe only if that endpoint has no queued, partially transmitting, or awaiting-response probe. Otherwise skip the tick: do not overlap probes or resend a pending PING. After completion, the next regular tick can create a new identifier. Each direction has its own counter and pending record; identically numbered probes in opposite directions are distinct. Gameplay completion does not restart the tick schedule.

Probes and automatic responses continue during lobby waiting, turn deliberation, and PAUSED while that particular connection remains online. GAME_OVER stops scheduling and starts closure. Stop scheduling for closing/retired connections; clear all pending/timer state on retirement, quit, or a new connection generation. Reconnection starts a fresh schedule/counter, with no carried-over deadline or response.

**H3 — Ten-second deadline.** Register the expected probe before queueing its frame, including the current connection generation and the client's latest applied snapshot revision when applicable. Once the complete LF-terminated PING has been submitted successfully to the local socket, arm one deadline at monotonic completion time plus ten seconds, unless H4 already completed that probe. This local completion is not proof of peer delivery. Partial progress, unrelated traffic, duplicates, mismatched PONG, and timer ticks do not restart the deadline.

A matching PONG may complete the probe after its transmission has started even if the local write-completion callback has not yet run. Qualifying gameplay progress may also complete a queued or transmitting probe under H4. A completed probe's later write callback must never arm a stale deadline. If a completed probe has not written any bytes, remove its queued PING. If any bytes have been submitted, finish that frame under its original output budget to preserve framing; do not splice it out or create another probe before transmission finishes. Failure still invokes R3–R5 even if liveness was already demonstrated. PONG writes use the same bounded output rules.

**H4 — Matching response or incoming gameplay progress.** Under serialized connection-control handling, a local outstanding probe succeeds through either route:

| Route | Exact qualifying input |
|---|---|
| Matched heartbeat | Valid PONG with the pending probe_id, from the current connection generation, satisfying H3's transmission-start rule. |
| Gameplay received by server | A complete MOVE from that same current session that passes every normal validation guard and is accepted. TRIGGER, PASS, LOAD, and END_TURN all qualify. Merely receiving bytes or parsing a schema-valid but rejected MOVE does not. |
| Gameplay received by client | A valid STATE_UPDATE for its current match, accepted for application with revision newer than both the previous applied snapshot (compare before applying this one) and the revision recorded when the probe was registered, and event PASSED, SURVIVED, TURN_READY, or MATCH_ENDED. The first valid GAME_OVER for the current match also qualifies, with the terminal snapshot/result ordering in section 5.1. Repeated results do not. |

Process the qualifying input after the probe has been registered, on the same live connection generation, and strictly before any armed deadline; equality is expired. Check expiry before accepting a MOVE or applying a snapshot for this purpose. Before the write callback arms a deadline, H3 governs completion. Clear only the receiver's currently outstanding probe, atomically with its acceptance decision; no extra game revision is added for heartbeat completion. No outstanding probe means no heartbeat effect.

This is evidence of incoming application activity, not proof the peer read that PING. It deliberately relaxes the earlier PONG-only policy. Heartbeats do not wait for a particular player's choice and have no turn_id; the next qualifying incoming gameplay event can complete the current probe. A server receiving P1's MOVE completes its probe to P1 only, never its probe to P2. A client's own outgoing MOVE cannot complete its probe to the server; receiving an eligible server update/result can.

Malformed input, rejected/stale/out-of-turn MOVE, repeated/older snapshots, SNAPSHOT/CONNECTION_LOST/RESUMED notifications, ERROR, handshake traffic, and arbitrary bytes do not substitute for heartbeat completion. A valid DISCONNECT initiates closure under R6/R7 instead of renewing liveness.

After completion by gameplay or PONG, ignore a well-framed late PONG for that completed identifier; do not send ERROR, extend a deadline, or let it complete a newer probe. An incoming PING remains the peer's independent request and must receive its matching PONG while the connection is live, even after gameplay or a local probe's completion. Never discard PING merely because a player acted. A mismatched PONG cannot be treated as gameplay progress.

**H5 — Expiry.** At or after a pending deadline, if neither route completed it, retire the current connection once using R5 with diagnostic cause HEARTBEAT_TIMEOUT. This cause is internal, not a new GAME_OVER reason or ERROR code. The server marks the session disconnected, pauses if required, and begins the separate 30-second grace. A client stops gameplay and may request nonterminal recovery using R7. Do not send a fatal ERROR down the timed-out stream or wait for further proof. Late PONG or gameplay cannot revive the retired connection or extend grace. Old timer callbacks must check both generation and pending probe_id before taking action.

With a five-second interval and prompt local scheduling/output, an idle silent failure can be noticed roughly up to fifteen seconds later (up to five until the next probe plus ten awaiting completion). That is not a ten-second end-to-end reconnection/forfeit promise. Output budgets, scheduling delays, and the separate grace period affect total elapsed time. Expiry means the connection is no longer responsive enough under this policy; it does not prove the remote process has crashed.

**H6 — Background progress and dispatch.** Socket receive/parsing, PONG responses, probe scheduling, and deadlines must continue while the console waits for keyboard input and while gameplay is paused. A blocking prompt must not hold the connection handler or game-state lock. All heartbeat/control frames share the existing TCP stream and framing limits; serialize writes so frame bytes cannot interleave. Dispatch complete frames in their received order through their type-specific handlers; do not reorder a batch to favor PONG or MOVE. H4 specifies the result of either order. PING/PONG may be handled before the first gameplay snapshot after WELCOME, but MOVE remains prohibited until synchronization is complete. Threading vs multiplexing remains a later implementation decision.

**H7 — Application keepalive.** This protocol uses its own JSON heartbeat over TCP; it does not adopt WebSocket framing or require a WebSocket library. OS TCP keepalive may be supplementary but cannot replace H1–H6. Reference: [Ping/Pong timing rationale](https://websockets.readthedocs.io/en/stable/topics/keepalive.html). The gameplay-completion exception in H4 is this project's own policy.

| ID | Heartbeat conformance scenario | Required outcome |
|---|---|---|
| HSC1 | Send PING id 17; receive valid PONG id 17 before deadline | Clear only that connection/direction's pending probe; gameplay/revision unchanged. |
| HSC2 | Pending id 17; receive PONG id 16, duplicate old PONG, or unsolicited PONG | Ignore; no deadline extension or response loop. |
| HSC3 | Pending id 17; receive PING id 17 from peer | Reply PONG id 17; own pending request remains pending until matching PONG or qualifying gameplay. |
| HSC4 | Server accepts current-session MOVE, or client applies qualifying newer gameplay snapshot, before pending deadline | Complete that receiver's outstanding probe; preserve normal gameplay effects; no additional revision. |
| HSC5 | Matching PONG or qualifying gameplay just before deadline vs exactly at deadline | Before succeeds; equality expires and follows R5; late gameplay is not accepted on the retired connection. |
| HSC6 | Deadline expires during any running phase or while already PAUSED | One transport loss; preserve committed gameplay/context; begin only this player's grace. |
| HSC7 | Client waits at console prompt longer than ten seconds | Background heartbeat continues; player thinking does not itself cause loss. |
| HSC8 | Reconnect, then stale old-generation timeout/PONG callback occurs | New connection is unaffected; no deadline carried over. |
| HSC9 | PING send fails or response to peer PING fails | Existing bounded-write/loss rules; no action replay or duplicate retirement. |
| HSC10 | Peer GAME_OVER/intentional DISCONNECT already resolved when heartbeat expires | Preserve terminal result; do not manufacture another loss. |
| HSC11 | Scheduling tick occurs with queued, transmitting, or awaiting-response probe | Skip creating a new probe; original deadline unchanged. |
| HSC12 | Matching PONG or qualifying gameplay processed before local send-completion callback | Callback must not arm a deadline for the completed probe; finish any partially transmitted frame. |
| HSC13 | MOVE completes probe 17, probe 18 later starts, then PONG 17 arrives | Ignore PONG 17; probe 18 and its deadline remain unchanged. |
| HSC14 | Qualifying gameplay completes local probe, then peer PING arrives | Still answer PING; the peer's independent probe is not canceled. |
| HSC15 | Rejected/stale MOVE, repeated/old snapshot, ERROR, or outgoing MOVE while probe pending | No completion or deadline extension; expiry remains possible. |
| HSC16 | Coalesced valid MOVE then matching PONG vs matching PONG then MOVE | Both process in stream order; one probe completion and one accepted gameplay action. |
| HSC17 | Qualifying gameplay arrives while PING queued with zero bytes sent | Remove unsent PING and complete probe; no stale timer; next normal tick may issue next ID. |
| HSC18 | P1's accepted MOVE arrives while server has pending probes to P1 and P2 | Only P1's probe completes; P2 still needs its own qualifying input. |


## 7. Wire examples

Each line below depicts one entire frame. The final two displayed characters backslash-n stand for one actual 0x0A byte, not two literal bytes. Tokens/IDs are illustrative, not credentials. Examples are independent unless stated otherwise. The PING/PONG pair requires an already bound/WELCOME session and is not sent before CONNECT.

```text
{"version":1,"msg_type":"PING","payload":{"probe_id":17}}\n
{"version":1,"msg_type":"PONG","payload":{"probe_id":17}}\n
{"version":1,"msg_type":"CONNECT","payload":{}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":false}}\n
{"version":1,"msg_type":"LOBBY_WAIT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","connected_players":1,"required_players":2}}\n
{"version":1,"msg_type":"GAME_START","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","first_player_id":"P1"}}\n
{"version":1,"msg_type":"MOVE","turn_id":1,"state_revision":3,"payload":{"action":"PASS"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":4,"payload":{"action":"TRIGGER"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":5,"payload":{"action":"LOAD","count":1}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":5,"payload":{"action":"END_TURN"}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":5,"phase":"LOAD","resume_phase":null,"turn_id":2,"active_player_id":"P2","allowed_actions":["LOAD","END_TURN"],"self":{"player_id":"P2","inventory":1},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"SURVIVED","reconnect_remaining_ms":null}}\n
{"version":1,"msg_type":"RECONNECT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":true}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{}}\n
{"version":1,"msg_type":"GAME_OVER","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":7,"winner_id":"P2","loser_id":"P1","reason":"FORFEIT"}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{"reason":"CLIENT_REQUEST"}}\n
{"version":1,"msg_type":"ERROR","payload":{"code":"ACTION_NOT_ALLOWED","detail":"This turn requires TRIGGER.","fatal":false}}\n
{"version":1,"msg_type":"ERROR","payload":{"code":"RESUME_DENIED","detail":"Session is no longer eligible for reconnection.","fatal":true}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{"reason":"MATCH_CLOSED"}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":6,"phase":"PAUSED","resume_phase":"LOAD","turn_id":2,"active_player_id":"P2","allowed_actions":[],"self":{"player_id":"P1","inventory":0},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"DISCONNECTED"}],"event":"CONNECTION_LOST","reconnect_remaining_ms":30000}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":8,"phase":"GAME_OVER","resume_phase":null,"turn_id":null,"active_player_id":null,"allowed_actions":[],"self":{"player_id":"P2","inventory":1},"players":[{"player_id":"P1","connection":"DISCONNECTED"},{"player_id":"P2","connection":"ONLINE"}],"event":"MATCH_ENDED","reconnect_remaining_ms":null}}\n
{"version":1,"msg_type":"GAME_OVER","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":8,"winner_id":"P2","loser_id":"P1","reason":"RECONNECT_TIMEOUT"}}\n
```

The LOAD and END_TURN examples are alternatives, not two requests to execute in succession. The final two examples show a revision-8 timeout outcome delivered to still-connected P2. These are separate from the earlier forfeit example and are not a result-recovery exchange. Byte example: the CONNECT frame ends with hexadecimal 7D 7D 0A (two closing braces, then 0x0A).


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

### 7.2 One TCP stream: coalescing and fragmentation

This example is entirely **server -> P1 on one already-bound connection**, after WELCOME. It is one continuous UTF-8 byte sequence containing GAME_START immediately followed by STATE_UPDATE. Every displayed `\n` represents exactly one byte `0x0A`; there are no other separators or literal backslash-n bytes. All characters in this example are ASCII, so displayed character counts before substitution equal UTF-8 byte counts.

```text
{"version":1,"msg_type":"GAME_START","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","first_player_id":"P1"}}\n{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":3,"phase":"TURN_CHOICE","resume_phase":null,"turn_id":1,"active_player_id":"P1","allowed_actions":["TRIGGER","PASS"],"self":{"player_id":"P1","inventory":0},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"MATCH_STARTED","reconnect_remaining_ms":null}}\n
```

The two object lengths are 118 and 419 bytes; with their LF delimiters the stream is 539 bytes. The delimiter boundary is `7D 7D 0A 7B`: the first object's closing braces, LF, then the next object's opening brace.

**Coalesced read:** if one recv() supplies all 539 bytes, the receiver extracts and dispatches GAME_START, then STATE_UPDATE, and retains no suffix. One read is not one message.

**Fragmented reads:** these three successive recv() results contain exactly the same bytes as the stream above. Concatenate their contents without adding any separator:

Read 1: 25 bytes.

```text
{"version":1,"msg_type":"
```

Read 2: 134 bytes.

```text
GAME_START","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","first_player_id":"P1"}}\n{"version":1,"msg_type":"STATE_UPDATE","
```

Read 3: 380 bytes.

```text
payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":3,"phase":"TURN_CHOICE","resume_phase":null,"turn_id":1,"active_player_id":"P1","allowed_actions":["TRIGGER","PASS"],"self":{"player_id":"P1","inventory":0},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"MATCH_STARTED","reconnect_remaining_ms":null}}\n
```

After read 1, dispatch nothing: no LF has arrived. After read 2, dispatch GAME_START exactly once and keep the 40-byte prefix of STATE_UPDATE. After read 3, dispatch STATE_UPDATE exactly once and empty the buffer. Each complete object is validated separately under the 4,096-byte ceiling; a receive containing several frames is not limited to that total size.

## 8. Conformance and related documents

Protocol behavior is checked by the message schemas, requirements A1–A5/F1–F6/R1–R10/H1–H7, transport cases C1–C26, heartbeat cases HSC1–HSC18, and FSM scenarios S1–S23 in [fsm_specification.md](fsm_specification.md). These are expected behaviors for future implementation verification, not claims of tests already executed.

Minimum coverage includes fragmented/coalesced frames; the 4,096/4,097 byte boundary; UTF-8 byte counting; missing/extra/wrong-type fields; invalid directions and states; forced-pass rejection; loading limits; stale revisions after a lost response; recovery during every nonterminal phase; exact timeout boundaries; terminal-result immutability; and private-field exclusion.

The reusable agent prompt, specification commit pinning, independent test expectations, and requirement-to-code-to-evidence reporting are maintained separately in [ai_prompts.md](ai_prompts.md). The [SOW](CS457_AndrewBarton_TermProjectSOW.md) provides course scope and later-sprint planning.
