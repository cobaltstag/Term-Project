# CS 457 Project Statement of Work (SOW) & Protocol Specification Template

**Student Name:** Andrew Barton  
**Date:** 2026-09-16  
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
- **Player Capacity:** 2 Players (Simulated via 2 CML Client nodes, could potentially go higher if allowed)
- **Game Summary:** It's more or less how it sounds. Players take turns passing back and forth a 1D array of length 6. Only one address in that array is occupied by a value. During a turn, one player 'fires' by entering the command 'trigger'. This will randomly select an index in the array between [0] and [5]. If that index in the array is occupied, the gun fires, the player 'dies' and loses the game. If the player doesn't die, they can choose to add another bullet to the revolver using the 'load' command (adding an additional value to the array) to increase the chances that the other player loses if they attempt play, or they can choose to do nothing. Their choice isn't revealed to the other player, only that the player that just 'fired' didn't lose. If on their next turn they successfully survive once more, they will be allowed to choose to add double the previous number of rounds available to be added to the revolver, increasing their chances of winning further. Players can choose to abstain from taking their turn by entering the 'pass' command. If a player abstains and the next player survives their round, the player that passes automatically loses by failing to take their chances when it worked 'in their favor' to do so. The game is over when only one player remains and that player is the winner of the game.

### 1.2 Core Game Rules & Win/Draw Conditions
- **Turn Mechanics:**
    1. Player turn order is decided at random, the revolver begins with one index in the array set to any value.

    2. The player that plays next is presented with a choice to shoot or pass.

        3. If the player elected to shoot, the game 'shoots' by selecting a random index in the array and checking to see if it is 'none'.

            o If that value is *not* 'none':
              - The player that just entered 'trigger' is shown a 'game over - you lost' message while the other player is shown a 'congratulations! you win!' message. (in a two-player construct.) 
              - Alternatively, that player would be be shown a 'game over - you lost' message and returned to the game's splash screen while the remaining players continue to play. The player that lost could have their data retained by the server for displaying match results at the end for the winner, or it can be discarded. In this style of architecture, if the revolver fires the only remaining round in the chamber, one is automatically loaded again, either invisible to the players or demonstrated through a message displayed to clients describing a referee for the game loading the round and spinning the chamber themselves.

            o If that value *is* 'none':
              - The player is presented with an option to 'add more bullets' or 'pass'

                4. If the player chooses to 'add more bullets':
                  - The player is allowed to select up to ***survivedRounds*** 'bullets' to add to the array, where (6>=(***survivedRounds*** - bulLoaded) >= 0). If ***survivedRounds*** > 2, a player can choose to double the amount of rounds added to the revolver, ensuring that if the game lasts longer than two rounds a loser is guaranteed if the current player adds as many rounds to the revolver as possible (Round 1: 1 bullet ->3 bullets(now 50% chance, both players loaded after surviving), Round 2: 3 -> 5 bullets @83% chance to lose on next player turn (6 if both players survive), Round 3: 6 -> P1 dead. This can occur whether the player loaded rounds on the first turn or not, because the number of rounds a player can add with each round survived doubles. On the 3rd round, the first player can add 4 bullets to the array (1 x 2 x 2 = 4), which is highly likely to have at least 1 in it at that point for the same 83% chance at the minimum.

                    o If the player chooses to add rounds:
                      - ***N*** rounds are 'added to' the array in addresses indexed at random until there are no more rounds to be added.

                   If the player does not choose to add rounds:
                  - the turn is automatically passed back to the other player and the turn mechanics repeat from 2.

- **Victory Condition:** The player wins the game when there are no other players alive.

- **Draw/Tie Condition:** The victory condition and attrition through increased round counts and iteration over probability organically prevents tie conditions. The chances that a player dies only go up until one player has died, and in the 2-player only version of the game this is an instant win for the player that doesn't die. Even in a game with more players than two, the same invariant holds as long as there remains a player to continue playing because the amount of maximum bullets than can be added to the revolver is undefined. 

---

## 2. Application-Layer Messaging Protocol Blueprint (Sprint 1 Deliverable)

### 2.1 Message Transport & Serialization Format
- **Transport Protocol:** TCP
- **Serialization Format:** [JSON / Fixed-Header Binary / Delimited Text]
- **Framing Mechanism:** [e.g., Newline-delimited (`\n`) JSON payloads OR 4-byte big-endian length prefix]

### 2.2 Message Schema Definitions

#### Message Types:
1. `CONNECT` (Client -> Server): Request to join the game room.
2. `LOBBY_WAIT` (Server -> Client): Notification that server is waiting for Player 2.
3. `GAME_START` (Server -> Clients): Game initiated, assigns roles (e.g. Player X vs Player O).
4. `MOVE` (Client -> Server): Player action (e.g., cell coordinates or answer choice).
5. `STATE_UPDATE` (Server -> Clients): Broadcast current game board / state and active player turn.
6. `GAME_OVER` (Server -> Clients): Victory / Draw notification with final scores.
7. `ERROR` (Server -> Client): Invalid move or malformed packet error.

#### Example JSON Protocol Schema:
```json
{
  "msg_type": "MOVE",
  "player_id": "Player_1",
  "payload": {
    "row": 0,
    "col": 2
  },
  "timestamp": 1727000000
}
```

---

### 2.3 Game State Machine (FSM) Design (Sprint 1 Deliverable)
- **State Transitions:** Detail state flow: `INIT` -> `WAITING_FOR_PLAYERS` -> `PLAYER_TURN` -> `EVALUATE_MOVE` -> `CHECK_WIN_DRAW` -> `GAME_OVER` -> `CLEANUP`.

---

## 3. Game Behavior & Server Concurrency Architecture (Sprint 2 Deliverable)

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** [Multi-Threading (`threading.Thread`) OR Non-blocking I/O multiplexing (`select.select` / `selectors`)]
- **Synchronization Logic:** Explain how shared game state and client list are thread-safe (e.g. `threading.Lock`) to prevent race conditions during turn processing.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Detail how the server validates active player ID before processing moves and broadcasts updated turn notifications to all clients.
- **Score & Board Synchronization:** Describe how state broadcasts keep client screens synchronized in real time.

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **Permitted AI Tools:** [e.g., GitHub Copilot, ChatGPT, Claude]
- **AI Prompting & Constraint Strategy:** Explain how you will constrain AI models to generate code (in Python or your chosen language) that adheres strictly to the protocol blueprint and FSM designed in Sprints 1 & 2.
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
- **Router R2 Authoritative DNS:** Configured with `ip dns server` and static host mapping `server.[yourlastname].edu` -> `192.168.20.100`.

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** Deploy `server.py` onto Subnet C node (`192.168.20.100`) behind Router R2, and `client.py` onto Subnet A and Subnet B nodes behind Router R1.
- **Cisco Infrastructure Configuration:** Router R1 DHCP pools (`CLIENT1_POOL`, `CLIENT2_POOL`) and Router R2 authoritative DNS (`ip host server.[lastname].edu 192.168.20.100`).
- **Wireshark Trace Capture Plan:** Capture DHCP DORA exchange (`dhcp_negotiation.pcap`) and DNS query/response resolution (`dns_lookup.pcap`).
