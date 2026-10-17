# CS 457 Project Concurrency Design

**Student Name:** Andrew Barton  
**Date:** 2026-10-17    
**Course:** CS 457 - Computer Networks and the Internet

## Player Turn Sequence Diagrams:

### The Player Loop:

```mermaid
sequenceDiagram
    participant A as Player A client
    participant S as Server

    S->>A: STATE_UPDATE: phase, turn, revision, allowed actions
    Note over A,S: Entry may be TURN_CHOICE, a forced shot, or resumed LOAD

    loop While A owns the turn and gameplay is running
        A->>S: MOVE: action, turn_id, state_revision
        S->>S: Validate against current authoritative state

        alt Recoverable invalid MOVE
            S->>A: ERROR
            S->>A: STATE_UPDATE: current private snapshot
            Note over A,S: Gameplay unchanged, player may submit a corrected action

        else Valid PASS in TURN_CHOICE
            S->>S: Record dare initiator, activate B in FORCED_REPLY
            S->>S: Increment turn and revision
            S->>A: STATE_UPDATE: PASSED, active player B

        else Valid TRIGGER in a shooting phase
            S->>S: Sample a fresh random chamber exactly once

            alt Loaded chamber
                S->>S: Commit A's elimination and B's win
                S->>A: Terminal STATE_UPDATE
                S->>A: GAME_OVER: SHOT
                S->>A: DISCONNECT: MATCH_CLOSED

            else Empty chamber
                S->>S: Award inventory, advance A's reward cycle
                S->>S: Enter LOAD, preserve shot context
                S->>S: Increment revision, retain turn_id
                S->>A: STATE_UPDATE: SURVIVED, LOAD
                Note over A,S: A still owns the turn
            end

        else Valid LOAD or END_TURN in LOAD
            opt Action is LOAD
                S->>S: Deduct count, fill distinct empty chambers
            end
            S->>S: Choose next player and phase from shot context
            S->>S: Increment turn and revision
            S->>A: STATE_UPDATE: TURN_READY, active player B
        end
    end
    ```

### Two Player Loop Demonstrating 'PASS' Flow and Architecture with Non-Terminal Handoff Paths:

```mermaid
sequenceDiagram
    participant A as Player A client
    participant S as Server
    participant B as Player B client

    S->>A: STATE_UPDATE: TURN_CHOICE, A active
    S->>B: STATE_UPDATE: A active, no allowed actions

    alt A chooses PASS
        A->>S: MOVE: PASS
        S->>S: Validate, record A as dare initiator
        S->>S: Activate B in FORCED_REPLY

    else A chooses TRIGGER and survives
        A->>S: MOVE: TRIGGER
        S->>S: Validate, resolve shot, reward A, enter LOAD
        S->>A: STATE_UPDATE: SURVIVED, A may LOAD or END_TURN
        S->>B: STATE_UPDATE: SURVIVED, A still active
        A->>S: MOVE: LOAD(count) or END_TURN
        S->>S: Validate, apply loading decision
        S->>S: Activate B in TURN_CHOICE
    end

    S->>A: STATE_UPDATE: B active, no allowed actions
    S->>B: STATE_UPDATE: current phase and allowed actions

    alt B chooses PASS in TURN_CHOICE
        B->>S: MOVE: PASS
        S->>S: Validate, record B as dare initiator
        S->>S: Activate A in FORCED_REPLY

    else B chooses TRIGGER and survives
        B->>S: MOVE: TRIGGER
        S->>S: Validate, resolve shot, reward B, enter LOAD
        S->>A: STATE_UPDATE: SURVIVED, B still active
        S->>B: STATE_UPDATE: SURVIVED, B may LOAD or END_TURN
        B->>S: MOVE: LOAD(count) or END_TURN
        S->>S: Validate, apply loading decision

        alt B was answering A's PASS
            S->>S: Activate A in FORCED_RETURN
        else B took an ordinary shot
            S->>S: Activate A in TURN_CHOICE
        end
    end

    S->>A: STATE_UPDATE: A active, current allowed actions
    S->>B: STATE_UPDATE: A active, no allowed actions
    ```