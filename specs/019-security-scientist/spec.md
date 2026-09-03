# Feature Specification: ARIA Security Scientist V1

**Feature Branch**: `019-security-scientist`

**Created**: 2026-09-03

**Status**: Draft

**Input**: User description: "Build a scientific-method security discovery loop, external to ARIA, that ARIA cannot influence, closing the gap exposed on 2026-09-03: a production venv ran two systemd services outside Docker, unscanned, invisible for six weeks (nine vulnerabilities found). The same morning produced three more defects of the same family (a truncated alert count read as zero-open, a stale CLI version from a wrong PATH, a scanner reporting green while the running process still executed deleted, vulnerable code) — in every case an incomplete measurement was presented as a fact. The goal is a loop that actively searches for the gap between assumed architecture and observed reality, and that can never turn absence of evidence into appearance of safety. Full operator-approved design (decisions, architecture, seven build steps, G1/G2/G3 guardrails, verification plan): `/root/.claude/plans/abundant-giggling-cloud.md`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every running production surface is discovered, or flagged as not (Priority: P1)

The system inventories every process actually running on the host, not just what documentation or configuration claims should be running, and classifies each one as production, development, test, residual, or unknown. Nothing that is running in production can silently stay outside this inventory. An operator or session can ask "what is actually running right now, and do we have proof it's safe?" and get an honest answer for every single process — including "we don't know" when that is the truth.

**Why this priority**: This is the exact capability whose absence caused the founding incident — a production venv running two systemd services was invisible for six weeks because nothing discovered it. Every other capability in this feature depends on first knowing what exists to observe.

**Independent Test**: Start a new, disposable process outside the normal deployment path (no lockfile, no prior scan). Confirm the inventory picks it up on its next pass and reports it as unresolved/unknown rather than omitting it. Stop and remove the process; confirm the inventory reflects its removal with a recorded reason.

**Acceptance Scenarios**:

1. **Given** a process is actively running in production, **When** the inventory runs, **Then** the process appears in the inventory, classified, with its dependency/scan state recorded.
2. **Given** an active production process has no dependency lockfile, no completed scan, or a stale scan, **When** the inventory evaluates it, **Then** the process is reported as unresolved ("unknown"), never as safe.
3. **Given** a process cannot be classified into any known category, **When** the inventory runs, **Then** it is recorded as unresolved and remains visible in every subsequent inventory until it is explicitly resolved — it never silently disappears.
4. **Given** a surface is known to exist but no observation mechanism currently covers it (a mechanism was removed or never built), **When** the overall security status is computed, **Then** the surface is reported as unobserved, distinctly from "observed but unproven" — no collector has to explicitly declare this state for it to surface.

---

### User Story 2 - A safety verdict can never be granted from an unproven or self-declared claim (Priority: P1)

Whenever the system is asked "is this safe" for anything (a runtime, a dependency, a control), the party that ran the check is never trusted to also decide the answer. Every check must survive a dedicated self-critique naming exactly what it did and did not verify, and a separate judgment step derives the final answer independently from raw facts, the self-critique, and nothing else. A check that skips or fails its self-critique can never result in a "safe" verdict — at best it results in "still unknown."

**Why this priority**: Three of the four real failures on the founding day were not failures of the thing being checked — they were failures of the checking instrument itself (a count that measured the wrong data, a version check that resolved the wrong file, a scanner reading a stale copy of a running process). Without this separation, better scanners alone cannot prevent recurrence, because the flaw is structural, not a missing check.

**Independent Test**: Submit a check whose own output already contains a "safe" conclusion baked in. Confirm the judgment step ignores that embedded conclusion and derives its own answer from the underlying facts. Submit a check whose self-critique is incomplete. Confirm the final verdict cannot be "safe" regardless of how good the underlying result looks.

**Acceptance Scenarios**:

1. **Given** a completed check, **When** it is submitted for judgment, **Then** the judgment step reads only raw observed facts, the check's definition, and its self-critique — never a pre-formed conclusion from the party that ran the check.
2. **Given** a check whose self-critique explicitly could not confirm full coverage, **When** judgment is rendered, **Then** the verdict is "unknown," never "safe," and the reason is recorded.
3. **Given** a check's own output contains a literal safe/unsafe conclusion, **When** judgment is rendered, **Then** that embedded conclusion is disregarded and has no bearing on the outcome.
4. **Given** a check concludes it could not prove something but explains exactly what it couldn't prove, **When** this is reviewed, **Then** it is treated as a successful, informative outcome — not as a failed check.

---

### User Story 3 - Every piece of safety evidence keeps a history and a traceable origin (Priority: P2)

Every observation the system makes about a security surface is kept permanently, tagged with exactly when it was made, what code/version produced it, and in which environment. An operator can ask "was this actually true three weeks ago, and how do we know" and get a real answer instead of only the latest snapshot. A verdict that was once true but has gone stale because too much time passed is distinguished from one that was never proven and from one that is currently violated. Any attempted observation that cannot prove where it came from is rejected and kept in a separate record — it can never be used to justify a "safe" verdict, but its rejection itself is remembered.

**Why this priority**: Today's mechanism overwrites its own results on every pass, so there is no trend, no freshness, and no way to answer "when did this stop being true." This is what turns a one-off scan into an actual historical record the rest of the system (and any future audit) can rely on.

**Independent Test**: Record two observations of the same surface at two different times with different outcomes. Confirm a query for the surface's state at each of those two times returns the correct, distinct answer. Submit an observation missing its origin information and confirm it is rejected and logged as rejected, never contributing to a verdict.

**Acceptance Scenarios**:

1. **Given** a surface was proven safe at one point in time and later re-evaluated, **When** its history is queried at the earlier time, **Then** the earlier result is returned unchanged, distinguishable from the current one.
2. **Given** a proof has aged beyond its trusted freshness window without being refreshed, **When** the surface's current status is requested, **Then** the status reflects that the evidence is too old to trust, distinct from "known unsafe" and from "never proven."
3. **Given** an observation is submitted without its origin (what produced it, when, in which environment), **When** it is processed, **Then** it is rejected, recorded as a rejected attempt, and never used to justify a verdict.
4. **Given** two observations of the same experiment were produced in two genuinely different environments, **When** both are recorded, **Then** they remain distinguishable from each other rather than being merged.

---

### User Story 4 - Known contradictions between documentation, code, and running reality surface automatically (Priority: P2)

The system automatically compares what the project's documentation and configuration claim against what the code and the running system actually show, and reports every mismatch it finds — a control referenced everywhere but absent from the code, a document pointing to a detail that was never written down, a mechanism running differently than described. Each contradiction becomes a trackable item until someone resolves it.

**Why this priority**: This is a low-risk, highly mechanical starting exercise that trains and validates the rest of the loop, and it directly cleans up the "ground truth" (docs, gates, pointers) that every other capability in this feature relies on being accurate.

**Independent Test**: Introduce a known, already-confirmed contradiction (a control referenced in configuration but absent from the code) into a test environment. Confirm the system reports it, unassisted, without being told where to look.

**Acceptance Scenarios**:

1. **Given** a control is injected into every session's configuration but no longer exists in the code, **When** the detector runs, **Then** it reports this specific mismatch as a trackable item.
2. **Given** a piece of documentation points to a location for further detail, **When** that location does not actually contain the promised detail, **Then** the detector reports the mismatch.
3. **Given** the same contradiction is detected again on a later pass while still unresolved, **When** the detector runs, **Then** it does not create a duplicate trackable item for the same contradiction.
4. **Given** a previously reported contradiction has been fixed, **When** the detector runs again, **Then** it no longer reports that contradiction.

---

### User Story 5 - Security hypotheses must prove exhaustive search before claiming safety (Priority: P3)

For a specific, high-stakes safety question (for example, "can this capability be triggered from a path that shouldn't allow it"), the system runs a structured investigation that starts by listing every path that would need to be ruled out to honestly claim safety, then works through that list. It can only conclude "safe" once every path on that list has actually been checked — finding nothing after only a partial search is reported honestly as "still don't know," never as "safe."

**Why this priority**: This is the highest-stakes application of the loop (financial and access-control questions) but depends on the discovery, judgment, and evidence capabilities above already existing — it's the first real "customer" of the rest of the feature, not a separate foundation.

**Independent Test**: Run an investigation whose path list is deliberately left partially unchecked. Confirm the outcome is "still unknown," never "safe." Complete every path on the list with none finding a problem, and confirm the outcome becomes "safe" only then.

**Acceptance Scenarios**:

1. **Given** an investigation's list of paths to rule out is only partially checked, **When** its outcome is requested, **Then** it reports "still unknown," never "safe."
2. **Given** every path on an investigation's list has been checked and none revealed a problem, **When** its outcome is requested, **Then** it reports "safe," together with the proof that the list was exhausted.
3. **Given** any single path on the list reveals the capability actually working when it shouldn't, **When** this is found, **Then** the investigation immediately reports "unsafe" with the specific path that worked.

---

### User Story 6 - Proposed safety rules and fixes always wait for a human decision (Priority: P3)

Every fix, safety rule, or standing invariant the system proposes is presented to the operator for an explicit yes/no — the system never enacts a new safety rule on its own, and no proposed fix reaches the live system without that decision. When the system identifies something worth ruling on (a control that should always hold, or a risky capability with no protection around it), it packages the proposal clearly enough for a non-technical decision.

**Why this priority**: This is the governance backstop for every other story — without it, a system that becomes very good at finding problems could also become a system that changes its own rules, which is the one outcome this whole feature must never produce. Ships last because it packages the output of every earlier story into a decision the operator makes.

**Independent Test**: Have the system propose a new safety rule based on a repeated finding. Confirm the rule has no effect on any live behavior until an operator explicitly approves it, and confirm a rejected proposal has zero effect.

**Acceptance Scenarios**:

1. **Given** the system proposes a new safety rule, **When** it is proposed, **Then** it has no binding effect on any live check or decision until explicitly approved.
2. **Given** an operator rejects a proposed rule, **When** the rejection is recorded, **Then** the proposal has no further effect and is not silently retried.
3. **Given** a capability is found to have no protective control around it at all, **When** this is discovered, **Then** it is reported as a priority open question, never silently left out of the record.
4. **Given** the system's continuous observation is unavailable (its host has been stopped), **When** anyone asks for the current safety status, **Then** the answer is that safety observation itself is unavailable — never a "safe" answer produced during that gap.

### Edge Cases

- What happens when the same underlying problem is detected by two independent checks at nearly the same time? (Must not create two contradictory tracked records for the same root cause.)
- How does the system behave when its own checking mechanism might itself be compromised or unreliable — does it ever trust its own instrument uncritically?
- What happens when a surface that was previously proven safe simply stops being checked (the checking mechanism is quietly removed)? It must fall to "unobserved," not silently keep its last "safe" status forever.
- What happens when an investigation's list of paths to rule out was itself incomplete from the start (a path nobody thought to include)? The system must be able to distinguish "exhausted the known list" from "the list itself was proven complete," and never claim the second without evidence.
- How does the system handle being asked for a verdict on a surface it has never encountered before? (Must be "unobserved"/"unknown," never a default "safe.")
- What happens if two people/sessions try to record a proposal decision on the same finding at the same time?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST inventory every actively running process on the production host (not a static/declared list) on a recurring basis, and classify each as production, development, test, residual, or unresolved.
- **FR-002**: System MUST report an active production process as unresolved whenever it lacks a dependency lockfile, lacks a completed scan, has only a stale scan, or comes from an unrecognized source — such a process must never be reported safe by default.
- **FR-003**: System MUST retain any process it cannot classify in its inventory indefinitely until the process is explicitly resolved (fixed, removed with a recorded reason, or reclassified) — it must never silently drop from view.
- **FR-004**: System MUST distinguish four coverage properties for every surface it knows about — whether it was discovered, whether an observation mechanism covers it, whether that mechanism actually verified it, and whether the verification is still fresh — and derive the reported status only from these properties, never as an independently-set label.
- **FR-005**: System MUST report a surface as "unobserved" whenever it is known to exist but no observation mechanism currently covers it, without requiring any component to explicitly declare that state.
- **FR-006**: System MUST require every safety check to pass through a structured self-critique, naming what was and was not verified, before a final verdict can be produced.
- **FR-007**: System MUST derive every final verdict (safe / unsafe / unknown / stale) independently from raw observed facts and the self-critique — never from a conclusion supplied by the component that ran the check, even if that component's output happens to contain one.
- **FR-008**: System MUST return "unknown" — never "safe" — whenever a check's self-critique is incomplete or missing.
- **FR-009**: System MUST persist every observation permanently and immutably, each tagged with when it was made, what produced it, and in which environment.
- **FR-010**: System MUST support querying the state of any surface as of a specific past point in time, returning the answer that was actually true at that time.
- **FR-011**: System MUST reject any observation submitted without verifiable information about its origin, and MUST record the rejection itself (separately from valid evidence) rather than silently discarding it.
- **FR-012**: System MUST distinguish "proof exists but has aged past its trusted freshness window" from both "never proven" and "actively violated," and report it as its own distinct state.
- **FR-013**: System MUST automatically detect and report, without manual guidance, mismatches between what documentation/configuration declares and what the code and running system actually show (including but not limited to: a control referenced but not implemented; a document pointing to detail that isn't actually there; a mechanism running differently than documented).
- **FR-014**: System MUST avoid creating duplicate trackable records for a contradiction or anomaly that is still open and previously reported, and MUST recognize when a previously reported issue is resolved.
- **FR-015**: System MUST support structured, high-stakes safety investigations that begin by defining the complete list of paths that must be ruled out to honestly claim safety, and MUST only report "safe" once every path on that list has been checked, with the proof of that exhaustiveness kept alongside the verdict.
- **FR-016**: System MUST report a high-stakes investigation as "still unknown" (never "safe") whenever its path list is only partially checked.
- **FR-017**: System MUST present any proposed new safety rule or standing invariant to the operator for an explicit decision, and MUST NOT let a proposal take binding effect on any live behavior until approved.
- **FR-018**: System MUST NOT automatically apply any fix, patch, or configuration change to the live production system in this version — every remediation stops at a prepared, human-reviewable proposal.
- **FR-019**: System MUST continue its discovery and observation activity independently of whether ARIA's own trading/capital processes are running or have been stopped (including by the capital kill-switch) — stopping ARIA must never be able to produce a "safe" security status by starving the observation itself; the correct report in that case is that security observation is unavailable.
- **FR-020**: System MUST record every rejected or incomplete verification attempt as a countable event distinct from a successful one, so that the number of times a "safe" conclusion was correctly refused for insufficient evidence can be measured over time.
- **FR-021**: System MUST make available, for any surface, a running count of how many previously unresolved/unproven surfaces have since become proven, so that overall progress in resolving uncertainty can be measured over time.

### Key Entities

- **Surface**: Anything the system can hold a safety opinion about — a running process, a dependency, a documented control, a capability. Carries the four coverage properties (discovered / observed / verified / evidence freshness) and, derived from them, a current status.
- **Observation**: A single, immutable, timestamped record of a raw measurement against a surface — carries its own origin (what produced it, when, in which environment) and never carries a conclusion.
- **Self-Critique**: A structured accompaniment to an observation or experiment, naming specific aspects that were or were not verified (coverage completeness, whether the running identity matched what was measured, whether the measurement could see its own future outcome prematurely, whether the check depended on the very thing it was testing, whether its own instrument could itself be compromised, whether its scope was too narrow, whether it could be reproduced).
- **Verdict/Evaluation**: The independently-derived outcome (safe / unsafe / unknown / stale) for a surface at a point in time, derived only from observations and self-critiques, never from a producer's own conclusion.
- **Rejected Evidence Attempt**: A record of an observation that could not be accepted (e.g., missing origin information) — kept for audit, never usable to justify a verdict.
- **Contradiction**: A detected mismatch between documented/configured intent and actual code or runtime reality, tracked until resolved.
- **Security Investigation (Hypothesis)**: A high-stakes, falsification-oriented question with an explicit, exhaustive list of paths to rule out before "safe" can be claimed.
- **Proposed Safety Rule (Constitution Invariant)**: A standing rule the system proposes based on repeated evidence, inert until an operator explicitly approves it.
- **Capability-Barrier Mapping**: A record of a system capability, its entry points, and what (if anything) actually prevents its misuse, used to prioritize where investigations are most needed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of actively running production processes are represented in the inventory at all times — none can remain running in production while being completely absent from it.
- **SC-002**: For any surface, the state that was reported true at any past point since this feature shipped can be retrieved and matches what was actually recorded at that time.
- **SC-003**: In a test suite of ten adversarial attempts specifically designed to obtain a "safe" verdict without any genuine new proof, zero succeed.
- **SC-004**: When run against a set of already-confirmed, known documentation/code/runtime contradictions, the contradiction detector finds 100% of them unassisted, in a single pass.
- **SC-005**: Zero automatic changes reach the live production system as a result of this feature's findings during V1 — every remediation requires a recorded human approval first.
- **SC-006**: When the system's own observation capability is deliberately made unavailable for a short window, 100% of status queries during that window report the observation as unavailable rather than reporting "safe."
- **SC-007**: The number of surfaces moved from unresolved/unproven to a proven status, and the number of times a "safe" conclusion was correctly refused for insufficient evidence, are both retrievable as running counts at any time.

## Assumptions

- The feature integrates with, and extends, existing project mechanisms rather than replacing them: the existing PASS/FAIL/UNKNOWN/STALE safety-status contract, the existing centralized findings registry used for session attention, the existing transitions-only state-history discipline, the existing falsification-experiment workflow/directory, and the existing static-invariant test suite. None of these are rebuilt from scratch.
- This version (V1) operates at a bounded autonomy level: it observes, diagnoses, and prepares remediation proposals (including in an isolated, disposable test environment when the real runtime must be exercised), but never applies a change to the live production system automatically. A higher autonomy level is an explicit future decision, not part of this feature.
- The system wakes its reasoning/investigation capability only when warranted (a significant anomaly, or a periodic review cycle) rather than running as a permanent, always-on reasoning process — continuous, lightweight observation is assumed to remain cheap enough to run constantly, while deeper reasoning is assumed to be reserved for when it is actually needed.
- Any proposed standing safety rule requires an explicit operator decision before taking effect; the system is assumed to never gain the ability to bind itself to a new rule unilaterally.
- Real production credentials/secrets are out of scope for this feature to read, display, or manage — it works with metadata about their presence and configuration state only.
- The host's limited memory, disk, and concurrent-session characteristics are treated as fixed constraints the design must fit within, not requirements this feature is meant to change.
- Discovery is prioritized ahead of risk severity when deciding what to look at first (unknown surfaces before known contradictions before financial capabilities), but severity still governs how open findings are finally triaged once discovered — an unresolved financial-capability question always outranks an unresolved non-financial one in that final triage, even though it wasn't necessarily looked at first.
