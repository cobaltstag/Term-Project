# Application Protocol Blueprint — Draft v1

**Project:** CS 457 Term-Project  
**Author:** Andrew Barton (design decisions developed with ChatGPT)  
**Updated:** 2026-10-02  
**Status:** Review draft; no socket implementation is included.

## 1. Scope and decision status

The confirmed design uses two players, server-controlled state, independent random chamber sampling, the forced-shot dare sequence, banked inventory, and the private repeating survival reward cycle. Intentional departure forfeits; interrupted connections permit reconnection. UTF-8 JSON with newline framing is selected.

The detailed schemas below are proposed for review, not claims that the student has already approved every field. These remaining design choices must be resolved before implementation:

| ID | Proposed choice | Reason |
|---|---|---|
| D1 | Maximum JSON object size: 4,096 UTF-8 bytes, excluding LF. | A bound on buffering; small messages remain small. |
| D2 | Reconnection grace: 30 seconds from server detection of loss, measured with a monotonic clock. | Concrete, configurable recovery window. |
| D3 | Pause active gameplay while either player is disconnected; retain phase and forced-turn obligations. | Prevent the opponent taking actions during recovery. |
| D4 | One accepted LOAD or END_TURN ends the post-survival loading phase. | A single loading decision per survival; no extra confirmation message. |
| D5 | Start both inventories at zero and both reward positions at the first entry. | Establish a complete initial state. |
| D6 | Retain terminal results and session tokens for 30 seconds before cleanup; no rematches or simultaneous rooms in v1. | Permit recovery of a result that was missed during interruption. |
| D7 | Detect transport loss through EOF/transport failure for this sprint; decide later whether silent-link failure detection needs a heartbeat. | A reconnect deadline cannot begin until the server detects loss. |
| D8 | If one player times out while the opponent is also disconnected, abort with no winner. | Avoid awarding a win to an absent player. |

Do not implement an unresolved proposal as an approved requirement. Multiplayer expansion, UI tutorials, score persistence, and socket boilerplate are outside this document.

## 2. Authority and visibility

**A1.** The server alone samples chambers, awards rewards, deducts inventory, places/removes bullets, assigns turns, and resolves outcomes. A client submits intentions.

**A2.** Each player has a private inventory and reward position. Successful shots award 1, 2, 4, 5, then repeat at 1. Award first, advance second. Loading does not change reward position.

**A3.** Send a player their own inventory only. Never send either reward position, future reward, cylinder array, sampled index, loaded count, opponent inventory, opponent reward amount, or opponent loading quantity. Reward amounts can be inferred from a player's own inventory changes; that discovery is intentional.

**A4.** LOAD and END_TURN generate the same public TURN_READY notification. Error feedback can still permit deductions about capacity; the design prevents direct disclosure, not all inference. LOAD_LIMIT must not report the number of empty chambers.

**A5.** Bind player identity to the server's current connection/session. MOVE has no client-controlled player_id. A resumed session replaces its old connection; old connections are fenced from further mutations.

## 3. Serialization and framing

**F1.** TCP carries UTF-8 JSON objects. Exactly one object occupies each line. Append exactly one LF byte (hex 0A) after the closing object; do not send CRLF, a BOM, blank lines, pretty-printed multiline JSON, or trailing whitespace.

**F2.** Proposed maximum: 4,096 bytes before LF, measured after UTF-8 encoding. It is a ceiling, not a padded allocation or minimum. A 150-byte object transmits 151 application bytes including LF.

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

Numbers described as integers must be finite whole JSON numbers, not booleans, strings, null, or fractional numbers. Client encoders use ordinary integer notation. Nonnegative counters have no gameplay-imposed cap; implementations must preserve exact values rather than silently overflow. Frame bounds still apply.

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

Payload: empty object. Valid only as the first complete message on a fresh, unbound connection. Assign an available seat and send WELCOME. If both seats are occupied or reserved, send ROOM_FULL and close this unbound connection. No state is restored through CONNECT. An accepted join increments the revision, sends lobby notifications/snapshots if still waiting, or executes match initialization if both seats are online.

### RECONNECT — client -> server

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Must identify the retained lobby/match. |
| session_token | SessionToken | Must identify a disconnected, unexpired session. |

Valid only as the first complete message on a fresh connection. Invalid, expired, or already-connected sessions receive RESUME_DENIED and closure of the new connection. On success send WELCOME, then the current authorized snapshot immediately. If resuming a terminal match, send GAME_OVER after the snapshot. Never recreate inventory, reroll a shot, award a reward, or clear forced-turn obligations during reconnection.

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

Follow with recipient-specific STATE_UPDATE. This message is emitted once per match, not replayed on reconnection.

### MOVE — client -> server

| Field | Type | Constraint |
|---|---|---|
| turn_id (top level) | TurnId | Must equal current turn. |
| state_revision (top level) | Revision | Must equal latest authoritative revision. |
| payload.action | String | TRIGGER, PASS, LOAD, or END_TURN. |
| payload.count | Integer | Present only for LOAD, positive, <= own inventory and empty chamber count. Forbidden for other actions. |

TRIGGER and PASS are accepted only in TURN_CHOICE; forced phases permit TRIGGER only. LOAD and END_TURN are accepted only in LOAD. The sending session must own the active turn. PASS is never a substitute for END_TURN.

One accepted LOAD deducts exactly count, fills that many distinct empty chambers chosen randomly by the server, then hands over the turn. END_TURN hands over without spending. Both preserve reward position. Invalid requests spend nothing and retain the phase.

Validate serially in this order: envelope/schema; session; match active/not paused; revision; turn; sender active; action allowed; count limits. Apply an accepted action and all its effects atomically before processing another event. No automatic MOVE retries are part of v1: after interruption obtain a fresh snapshot before issuing any new action. A duplicate accepted action carrying its old revision is rejected as STALE_STATE, not executed twice.

### STATE_UPDATE — server -> each connected assigned client individually

| Payload field | Type | Constraint |
|---|---|---|
| match_id | MatchId | Current match/lobby. |
| state_revision | Revision | Current authoritative revision. |
| phase | Phase | Current state. |
| resume_phase | Phase or null | Previous active phase when PAUSED; otherwise null. Cannot be PAUSED or GAME_OVER when non-null. |
| turn_id | Integer or null | TurnId during active/paused match; null before start or after terminal outcome. |
| active_player_id | PlayerId or null | Preserved during pause; null in lobby/terminal state. |
| allowed_actions | Array of strings | Legal action names for this recipient's current phase. Empty when inactive, paused, waiting, or terminal. |
| self | Object | Exactly player_id: PlayerId and inventory: Inventory. |
| players | Array of objects | One per occupied/reserved seat; each has player_id: PlayerId and connection: ONLINE, DISCONNECTED, or LEFT. No private data. |
| event | String | SNAPSHOT, MATCH_STARTED, PASSED, SURVIVED, TURN_READY, CONNECTION_LOST, RESUMED, or MATCH_ENDED. |
| reconnect_remaining_ms | Integer or null | Nonnegative remaining time to earliest disconnected active session deadline; null if none. Informational, server clock controls expiry. |

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
| detail | String | Human explanation, 1–160 characters, with no secrets. |
| fatal | Boolean | Whether this connection will be closed. |

| Code | Meaning | Fatal |
|---|---|---|
| MALFORMED_FRAME | Invalid framing/UTF-8/JSON/root/duplicate keys. | true |
| FRAME_TOO_LARGE | More than configured byte ceiling. | true |
| UNSUPPORTED_VERSION | Version other than 1. | true |
| INVALID_SCHEMA | Missing, extra, wrong-type, or invalid-enum field. | false |
| HANDSHAKE_REQUIRED | MOVE/DISCONNECT before binding; or inappropriate first type. | true |
| ROOM_FULL | No available seat. | true |
| RESUME_DENIED | Invalid/expired/in-use resume credential. | true |
| MATCH_NOT_ACTIVE | No active game or already terminal. | false |
| MATCH_PAUSED | Gameplay frozen for reconnection. | false |
| STALE_STATE | Revision mismatch. | false |
| STALE_TURN | Turn mismatch after revision passed. | false |
| NOT_YOUR_TURN | Sender does not own active turn. | false |
| ACTION_NOT_ALLOWED | Action invalid in current phase; includes repeated CONNECT/RECONNECT on bound connection. | false |
| LOAD_LIMIT | Count exceeds inventory or cylinder space; do not say which hidden capacity failed. | false |

After a nonfatal MOVE error send the sender a fresh STATE_UPDATE with event SNAPSHOT. Schema errors without a bound session receive ERROR only. Count <= 0 or noninteger is INVALID_SCHEMA, not LOAD_LIMIT.

## 6. Connection interruption and forfeit

Proposed recovery policy:

1. At detected EOF/transport failure on the current bound connection, mark the player disconnected, reserve their session, and start their 30-second monotonic deadline. Ignore events from superseded connections.
2. Pause active gameplay and save the exact prior phase, active player, turn, revision, inventory, cylinder and dare obligations. Notify the remaining connected player by STATE_UPDATE. Do not reveal private saved state.
3. Accept RECONNECT strictly before that player's deadline; at equality timeout wins. Valid resume rebinds the session and sends its snapshot. Resume gameplay only when both players are online. Send each connected player a fresh recipient-specific STATE_UPDATE whenever session status changes, including the remaining peer when gameplay resumes.
4. A second connection loss receives its own deadline. Invalid resume attempts do not extend a deadline. Repeated valid interruptions get a new grace window; imposing an anti-stalling budget is outside the current proposal.
5. If an active player's deadline expires while the opponent is online, declare RECONNECT_TIMEOUT and award the opponent the win. If the opponent is also disconnected, declare MATCH_ABORTED. Terminal outcomes are frozen.
6. Lobby interruption reserves the seat for the same grace window. Expiry releases it without a win/loss. Both seats must be connected before starting.
7. Intentional DISCONNECT bypasses grace. If the opponent is disconnected, still record them as the winner and retain that result for possible resume.
8. Retain terminal state/tokens for 30 seconds from terminal resolution, send results on valid reconnect during retention, then notify writable peers with DISCONNECT/MATCH_CLOSED, close connections, invalidate tokens and clean up.

A server process crash and recovery from disk are outside v1. Grace begins at detection, not at the unknowable instant the physical connection failed. Silent failure detection remains D7.

## 7. Wire examples

Each line below depicts one entire frame. The final two displayed characters backslash-n stand for one actual LF byte, not two literal bytes. Tokens/IDs are illustrative, not credentials. Examples are independent unless stated otherwise.

```text
{"version":1,"msg_type":"CONNECT","payload":{}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":false}}\n
{"version":1,"msg_type":"LOBBY_WAIT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","connected_players":1,"required_players":2}}\n
{"version":1,"msg_type":"GAME_START","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","first_player_id":"P1"}}\n
{"version":1,"msg_type":"MOVE","turn_id":1,"state_revision":2,"payload":{"action":"PASS"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":3,"payload":{"action":"TRIGGER"}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":4,"payload":{"action":"LOAD","count":1}}\n
{"version":1,"msg_type":"MOVE","turn_id":2,"state_revision":4,"payload":{"action":"END_TURN"}}\n
{"version":1,"msg_type":"STATE_UPDATE","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":4,"phase":"LOAD","resume_phase":null,"turn_id":2,"active_player_id":"P2","allowed_actions":["LOAD","END_TURN"],"self":{"player_id":"P2","inventory":1},"players":[{"player_id":"P1","connection":"ONLINE"},{"player_id":"P2","connection":"ONLINE"}],"event":"SURVIVED","reconnect_remaining_ms":null}}\n
{"version":1,"msg_type":"RECONNECT","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}\n
{"version":1,"msg_type":"WELCOME","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","player_id":"P1","session_token":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","resumed":true}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{}}\n
{"version":1,"msg_type":"GAME_OVER","payload":{"match_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state_revision":8,"winner_id":"P2","loser_id":"P1","reason":"FORFEIT"}}\n
{"version":1,"msg_type":"DISCONNECT","payload":{"reason":"CLIENT_REQUEST"}}\n
{"version":1,"msg_type":"ERROR","payload":{"code":"ACTION_NOT_ALLOWED","detail":"This turn requires TRIGGER.","fatal":false}}\n
```

The LOAD and END_TURN examples are alternatives, not two requests to execute in succession. Byte example: the CONNECT frame ends with hexadecimal 7D 7D 0A (two closing braces, then LF).

## 8. Implementation contract and conformance

Use this blueprint together with [fsm_specificaiton.md](fsm_specificaiton.md). The SOW provides course context. Resolve conflicts explicitly rather than choosing an interpretation silently.

Proposed instruction for a future implementation request:

> Implement only the approved version of protocol_blueprint.md and fsm_specificaiton.md. Before generating implementation code, list unresolved decisions and contradictory requirements; stop to resolve those instead of inventing behavior. Preserve message names, directions, exact fields, types, limits, privacy boundaries, framing, validation order, and transitions. Map implementation components and meaningful tests to requirement IDs and FSM scenario IDs. Do not add messages, change the reward cycle, automatically retry actions, or introduce unsupported features. Report any requirement you cannot meet. Socket boilerplate is outside the current design task.

This instruction makes adherence reviewable; it does not guarantee generated code is correct without verification.

Minimum future conformance checks: fragmented/coalesced frames; 4,096/4,097 byte boundary; multibyte UTF-8 byte counting; missing/extra/wrong-type fields; forced-pass rejection; loading limits and inventory; old revision rejected after a lost response; resume during every active phase; timeout boundary; terminal result immutability; absence of private opponent/cylinder/reward fields. Use scenario expectations in the FSM as behavioral checks, rather than tests that merely repeat implementation code.
