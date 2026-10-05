# CS 457 Project Statement of Work (SOW) & Protocol Specification Template

**Student Name:** Andrew Barton  
**Date:** 2026-09-16  
**Last Updated:** 2026-10-04  
**Course:** CS 457 - Computer Networks  
**Target Server Domain:** `server.barton.edu`  

---

## 1. Game Selection & Scope (Sprint 0)

> Planning is going to be an iterative process through the sprints so you don't have to have all the details now. Focus on big overview concepts. You will be updating the SOW as we plan.
> You have a lot of freedom to choose a game. There are a couple caveats.  

> - It must run in the console. The lab nodes won't be able to handle extensive graphics.
> - It has to be self-contained. You can use a internet-connector to download you code, but because the architecture must run 5 nodes you won't be able to run 
> - You are encouraged to use python, but I'm not going to make it a strict requirement. The instructor and TA's ability to help with C or Rust, etc will be diminished in other languages.

### 1.1 Game Overview
- **Chosen Game:** Russian Roulette
- **Player Capacity:** Two players, simulated by two CML client nodes. More than two players is outside the current deliverable.
- **Game Summary:** A server manages a shared six-chamber revolver and two players. One randomly chosen chamber initially contains a bullet. Each trigger pull independently samples an index uniformly from 0 through 5; it does not advance through chambers in sequence. A loaded sample kills the firing player and ends the match. An empty sample earns private banked bullets and an opportunity to load before handing over the turn. A pass is a dare that forces the opponent to shoot and, if that opponent survives, forces the passer to shoot on the following turn. It does not automatically eliminate the passer.

### 1.2 Core Game Rules & Win/Draw Conditions
1. **Initial state:** Randomize the first player. The revolver has six slots, one loaded and five empty. Initial inventory is zero for each player.
2. **Normal turn:** The active player chooses `TRIGGER` or `PASS`. Each trigger independently samples a chamber. Empty slots are represented conceptually by `None`; occupied slots contain a bullet.
3. **Fatal shot:** Remove the fired bullet from the sampled chamber and eliminate its shooter. The other player wins immediately. No further reward or loading phase occurs.
4. **Survival reward:** Every successful trigger survival credits the firing player's inventory according to their own repeating cycle: `1 -> 2 -> 4 -> 5 -> 1 -> ...`. Award five before resetting the next reward to one. Unspent inventory persists across that reset. Loading or declining to load does not reset or advance the cycle. The next reward position is never disclosed to either client; each player sees their own current inventory.
5. **Loading:** After surviving, the player may load a positive number of banked bullets, up to both their inventory and the number of empty chambers, including filling the cylinder. Loaded bullets occupy randomly chosen distinct empty slots. There is no unloading action. Declining to load spends nothing. Version-one behavior: one accepted `LOAD` or `END_TURN` finishes the loading phase.
6. **Pass/dare:** A normal-turn `PASS` transfers the turn and obligates the opponent to pull the trigger. If that opponent dies, the passer wins. If they survive, they earn their reward and may load or end their turn; the original passer must then pull the trigger. If the passer also survives, they earn their reward and may load or end their turn, after which the opponent has a normal turn. Neither forced turn permits another pass.
7. **Hidden information:** Neither client receives cylinder contents, the loaded chamber count, the opponent's inventory, their loading quantity, or either next reward position. A full cylinder guarantees death for the next player who pulls the trigger. This does not identify in advance which player will be obliged to shoot. Outcomes and validation feedback may permit deductions.
8. **Leaving:** An intentional `DISCONNECT` forfeits an active match. A detected connection interruption reserves the player's server state for a reconnection window; a successful reconnection immediately receives their authorized current state. The match pauses during a 30-second reconnection grace. Valid credentials may immediately replace an old online connection. Due heartbeat failures are processed before grace failures. Once the match ends, credentials expire; post-game result recovery is deliberately unsupported. Bidirectional heartbeats have a ten-second response deadline and five-second scheduling interval. A matching PONG or qualifying incoming gameplay progress satisfies an outstanding probe; late PONGs are ignored, while peer PING requests still receive replies.
- **Victory Condition:** The other player is eliminated or forfeits.
- **Draw/Tie Condition:** Successful gameplay actions are resolved serially, so a fatal shot produces one winner. Transport failures can instead abort a match with no winner, as specified in the FSM. No fixed maximum number of turns is promised; loading is optional and shots are random.

---

## 2. Application-Layer Messaging Protocol Blueprint (Sprint 1 Deliverable)

### 2.1 Message Transport & Serialization Format
- **Transport Protocol:** TCP; lab default `server.barton.edu:45700` (configurable consistently for server/client).
- **Serialization Format:** UTF-8 JSON objects.
- **Framing Mechanism:** Newline-delimited JSON: one single-line object followed by exactly one LF byte (`0x0A`). Multiple complete frames in one read are extracted and dispatched in order; incomplete bytes wait for the next read.
- **Maximum Frame Payload:** Limit of 4,096 bytes per JSON object, excluding LF; not per receive call. This is a ceiling, not padding or a target size.
- **Termination & Recovery:** Controlled quit flushes DISCONNECT before send-side shutdown and bounded receiving/close. Bare EOF, reset, or failed write is interruption. A separate monotonic 30-second grace timer operates after failed-socket cleanup; a socket polling timeout does not forfeit a player.

### 2.2 Message Schema Definitions
See [protocol_blueprint.md](protocol_blueprint.md) for the message inventory, field specifications, validation, privacy rules, framing behavior, and wire examples. Receive extraction, EOF/exception handling, explicit socket shutdown, and the independent reconnection timer are specified there.

### 2.3 Game State Machine (FSM) Design (Sprint 1 Deliverable)
See [fsm_specification.md](fsm_specification.md) for native Mermaid lifecycle/gameplay diagrams (including INIT, GAME_START, PLAYER_TURN, EVALUATE_MOVE and explicit ERROR paths), the transition table, forced-turn sequence, reconnection behavior, and conformance scenarios.

---

## 3. Game Behavior & Server Concurrency Architecture (Sprint 2 Deliverable)

The language, concurrency library, and synchronization implementation are later-sprint choices. The protocol/FSM already require serialized authoritative transitions and background heartbeat processing.

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** [Multi-Threading (`threading.Thread`) OR Non-blocking I/O multiplexing (`select.select` / `selectors`)]
- **Synchronization Logic:** Explain how shared game state and client list are thread-safe (e.g. `threading.Lock`) to prevent race conditions during turn processing.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Validate the connection-bound player, current revision, turn, and allowed phase/action before mutation; publish recipient-specific snapshots after each accepted transition.
- **Score & Board Synchronization:** Use authoritative revisions and full recipient-specific snapshots, including on reconnect. No persistent score or public cylinder contents are part of v1.

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **AI Tools Used for Planning:** ChatGPT. Any later implementation use remains subject to course rules.
- **AI Prompting & Constraint Strategy:** See [ai_prompts.md](ai_prompts.md) for the design-stage prompting constraints and separate future implementation prompt, approved-commit pinning, specification-change boundaries, independent verification, and traceability requirements. Program generation is reserved for the implementation sprint.
- **Implementation Risk Management:** Detail your plan to leverage past programming experience and manage time to ensure code completion on schedule.

---

## 5. CML Multi-Subnet Topology & Wireshark Deployment Plan (Sprint 4 & 5 Deliverable)

> For now you can use the topology below. We may update this when we get to defining subnets.

### 5.1 Subnet & Router Design
- **Subnet A (Client 1):** `192.168.10.0/24` (Interface `Gi0/1` on Router R1)
- **Subnet B (Client 2):** `192.168.11.0/24` (Interface `Gi0/2` on Router R1)
- **Subnet C (Game Server):** `192.168.20.0/24` (Interface `Gi0/1` on Router R2)
- **Router Backbone:** `10.0.0.0/30` (Interface `Gi0/0` on R1 <-> `Gi0/0` on R2)

### 5.2 DHCP Pools & DNS Configuration Plan
- **Router R1 DHCP Pool 1 (`CLIENT1_POOL`):** Leases `192.168.10.10` - `192.168.10.50`, gateway `192.168.10.1`, DNS `10.0.0.2`.
- **Router R1 DHCP Pool 2 (`CLIENT2_POOL`):** Leases `192.168.11.10` - `192.168.11.50`, gateway `192.168.11.1`, DNS `10.0.0.2`.
- **Router R2 Authoritative DNS:** Configured with `ip dns server` and static host mapping `server.barton.edu` -> `192.168.20.100`.

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** Deploy `server.py` onto Subnet C node (`192.168.20.100`) behind Router R2, and `client.py` onto Subnet A and Subnet B nodes behind Router R1.
- **Cisco Infrastructure Configuration:** Router R1 DHCP pools (`CLIENT1_POOL`, `CLIENT2_POOL`) and Router R2 authoritative DNS (`ip host server.barton.edu 192.168.20.100`).
- **Wireshark Trace Capture Plan:** Capture DHCP DORA exchange (`dhcp_negotiation.pcap`) and DNS query/response resolution (`dns_lookup.pcap`).
