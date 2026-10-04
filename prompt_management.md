# Prompt Management and Future Implementation Contract

**Project:** CS 457 Term-Project  
**Author:** Andrew Barton (design decisions developed with ChatGPT)  
**Updated:** 2026-10-04  
**Status:** Submission candidate; reusable instructions for the later implementation stage.

Use the [protocol blueprint](protocol_blueprint.md) together with the [FSM specification](fsm_specification.md). The [SOW](CS457_AndrewBarton_TermProjectSOW.md) provides course context. Resolve conflicts explicitly rather than choosing an interpretation silently.

## 1. Freeze the approved inputs

If later revisions introduce new proposals, resolve and record them in the protocol before requesting implementation. Before the future coding request, select the implementation language, concurrency approach, and permitted dependencies as planned for the later sprints. Bracketed prompt inputs are intentional template fields, not missing wire-protocol definitions.

Identify the full Git commit SHA containing both approved specifications. A branch name or "latest version" alone is insufficient because its contents can change. The future agent must verify that it read the two files at that commit. The pinned specifications define observable protocol/game behavior. Later user-approved changes require an explicit specification revision and a new identified baseline; implementation convenience is not authorization to revise them.

## 2. Reusable future implementation prompt

Replace the bracketed inputs only after the design is approved. This is a prompt template for later use, not an instruction to implement code during the current task.

> Implement the finished two-player console client/server program for CS 457 Term-Project.
>
> Repository: cobaltstag/Term-Project. Approved specification commit: [FULL_COMMIT_SHA]. Read protocol_blueprint.md and fsm_specification.md at that exact commit and confirm the baseline in your delivery report. Implementation language: [LANGUAGE]. Concurrency approach: [APPROVED_APPROACH]. Permitted dependencies: [APPROVED_DEPENDENCIES].
>
> This future request authorizes the socket transport implementation needed for the finished program. Respect the specifications' two-player scope and deployment constraints.
>
> Before generating implementation code, identify unresolved decisions, missing requirements, and contradictions. All remaining design proposals must have an approved resolution. Do not silently invent a rule or promote a proposal to a requirement.
>
> Treat the approved protocol and FSM as the implementation contract. Preserve message names, directions, exact fields, types, limits, privacy boundaries, framing, validation order, heartbeat pairing/gameplay-completion rules and timing, connection termination behavior, game rules, and state transitions. Do not add messages, change the reward cycle, automatically retry gameplay actions, or introduce unsupported features.
>
> Do not modify the specifications or weaken expected test outcomes to accommodate implementation behavior. If a conflict or missing requirement prevents compliance, identify the exact passages and propose a resolution before implementing the affected behavior. Ordinary implementation choices that preserve the contract may be made without further approval. Report any requirement you cannot meet.
>
> Derive expected test outcomes from the specifications independently of the implementation's own calculations. Make randomness and time controllable within tests so documented fatal/survival outcomes, reward progression, deadline boundaries, and recovery cases can be exercised reliably. Test controls must not become undocumented wire messages or player-facing features.
>
> Map requirements and documented scenarios to implementation locations and verification evidence. Run the relevant checks and report the actual commands executed, results, failures, and checks that remain unperformed. Do not describe written tests as executed, or passing tests as proof of requirements they did not exercise.
>
> Deliver the complete program, run instructions for the approved environment, meaningful conformance checks, and the traceability/verification report described below. Correct implementation defects against the contract; propose specification changes separately.

This instruction makes adherence reviewable; it does not guarantee generated code is correct without verification.

## 3. Independent expected outcomes and controlled tests

Use scenario expectations in the FSM (S1–S23), transport cases (C1–C23), and heartbeat cases (HSC1–HSC18) as the behavioral reference. A test must not obtain its expected result by calling the same rule implementation it is supposed to check.

For example, S5 specifies rewards 1, 2, 4, 5, 1 and inventory totals 1, 3, 7, 12, 13 without spending. Assert those documented values rather than deriving the expected cycle through the production reward function. H4 permits an exact matching PONG or specifically defined incoming gameplay progress: a mismatched PONG, rejected MOVE, old snapshot, or outgoing action must leave the original deadline active. After qualifying gameplay completes probe 17, its late PONG cannot clear probe 18; an incoming PING must still be answered.

Provide controlled random outcomes and a controllable monotonic clock within the test environment. This enables repeatable loaded/empty chamber samples and checks immediately before, at, and after deadlines without relying on random luck or long real-time sleeps. Use those controls to exercise behavior, not merely to mirror implementation structure.

Timing tests and parser/game tests do not substitute for real TCP integration checks. The final report must distinguish simulated/controlled checks from checks using actual socket connections.

## 4. Requirement traceability and verification report

Supply a compact table linking each protocol requirement/message constraint, FSM invariant/transition, and documented scenario to relevant implementation and evidence. Group related items where the mapping remains clear. Use exact field names and section references where a requirement lacks a numbered ID. Any uncovered requirement must be marked unverified rather than omitted.

Illustrative table structure, to be completed against the delivered program:

| Requirement or scenario | Implementation location | Verification evidence | Status / limitation |
|---|---|---|---|
| H4: response pairing or qualifying gameplay completion | [actual module/function] | [executed checks: accepted MOVE completes probe; wrong/late PONG cannot complete a newer probe] | [result and any limits] |
| R3 / S10: committed action survives response failure | [actual module/function] | [executed recovery scenario with lost response] | [result and any limits] |
| A3: opponent/cylinder/reward privacy | [actual message construction locations] | [executed checks inspecting emitted payloads] | [result and any limits] |

Record:

- The approved specification commit and actual implementation revision.
- Commands and relevant environment used for verification.
- Observed results, including failures and incomplete checks.
- Any unverified requirements, assumptions, or limitations.
- Specification change proposals separately from implementation changes.

## 5. Minimum conformance coverage

Minimum future checks include fragmented/coalesced frames; the 4,096/4,097 byte boundary; multibyte UTF-8 byte counting; missing/extra/wrong-type fields; forced-pass rejection; loading limits and inventory; old revisions rejected after a lost response; resume during every active phase; exact timeout boundaries; terminal-result immutability; absence of private opponent/cylinder/reward fields; explicit normal-exit DISCONNECT/shutdown/close; EOF loop exit without stopping accept; independent grace expiry with no socket activity; back-to-back gameplay/heartbeat frames; and all documented transport/heartbeat cases.

Passing a subset is evidence only for that subset. Fix failed implementation behavior and repeat the checks affected by the fix. If a requirement cannot be verified, state that explicitly; do not claim complete conformance.
