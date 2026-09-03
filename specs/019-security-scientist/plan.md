# Implementation Plan: ARIA Security Scientist V1

**Branch**: `019-security-scientist` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/019-security-scientist/spec.md`, technical foundation from the operator-approved design at `/root/.claude/plans/abundant-giggling-cloud.md`, sharpened by a second architecture review (see "Refinements from review" below).

## Summary

Extend the existing, already-proven `security_posture.py` evidence contract (`Evidence`, `aggregate`, worst-wins, `UNOBSERVED`/`UNKNOWN`/`STALE`/`FAIL`/`PASS`, 18 negative tests) with three things it currently lacks: (1) a runtime-discovery collector that finds every actively running process from `/proc`+`systemd`, not a declared list; (2) durable, provenance-complete, queryable-by-time persistence of every observation, instead of the current overwrite-on-every-run `posture.json`; (3) a structural separation — Collector (raw facts only) -> independent Critic (named failure modes) -> independent Judge (derives the verdict, reusing the existing `Evidence` contract as its output shape) — so that no component can certify its own measurement. The four existing `security_posture_collect.py` checks (dependabot/osv/cli-version/session-guardrails) are simple, single-fact, low-stakes measurements and are left untouched; the new Collector/Critic/Judge discipline applies only to the new, higher-stakes Security Scientist surfaces (runtime inventory, doc/code/runtime contradictions, falsification-style security hypotheses) where a producer certifying its own coverage is the actual risk this feature exists to close.

This plan covers the technical design for the full V1 (all six user stories / seven build steps of the approved plan). The first shippable increment — sequenced first in `tasks.md` — is Step 1 (DISCOVER) plus the evidence-persistence foundation and the Collector/Critic/Judge separation, gated by two decisive adversarial experiments before any higher layer is built.

## Refinements from review (encoded here, not in spec.md)

Spec.md is frozen (6 user stories, 21 functional requirements, quality checklist PASS) and is not re-opened. The following architectural refinements from a second review pass are binding on this plan and on the negative/adversarial test suite, not on the spec's WHAT/WHY:

1. **`observation_gap`, not "at all times"**: the design reports how long ago the last successful discovery pass completed and treats a gap as reduced coverage confidence, never as proof of absence. No component may treat "not currently observed" as "not currently real."
2. **Verdict is always a computed projection, never a stored ground-truth field**: no table row anywhere carries a mutable `status`/`verdict` column that is treated as authoritative. `security_evaluations` rows are themselves derived, append-only, and superseded by later evaluations recomputed from the observation chain — never updated in place.
3. **`PATH_COVERAGE` != `MODEL_COVERAGE`**: a falsification-style investigation (`H-SEC-*`) tracks two distinct booleans. "Every currently known path checked" alone yields, at best, a labeled `path-exhausted, model-unproven` outcome — never an unqualified `PASS`.
4. **Collector -> independent Critic -> independent Judge**: the self-critique of a Security-Scientist-produced observation is authored by a role/module structurally distinct from the one that produced the observation (separate module, pure function of the observation's data only, no shared mutable state, enforced by a static import-graph test). The critic emits one of eight named failure codes (`COVERAGE_UNKNOWN`, `IDENTITY_MISMATCH`, `STALE_SOURCE`, `WRONG_EXECUTABLE`, `UNOBSERVABLE`, `SELF_DEPENDENCY`, `SCOPE_TOO_NARROW`, `NON_REPRODUCIBLE`) when it cannot certify the observation; any one of them blocks `PASS`/`SAFE` at the Judge.
5. **First build increment is the model-free instrument, not reasoning**: `/proc` + systemd discovery producing ONLY a raw `RuntimeObservation` (no safe/unsafe/unknown field of any kind), gated by two decisive adversarial tests before step 2 onward is built (see Quickstart).
6. **`T_detect`, a provable bound, not just a reported gap**: `observation_gap` (refinement #1 above) tells you HOW stale the last pass is; it does not by itself prove a MAXIMUM staleness. The design additionally fixes the discovery cadence and proves `T_detect = 2x cadence` (worst case: a process appears one instant after a pass completes) as the maximum time any actively-running process can remain absent from the inventory, with a test that spawns a process and asserts it is captured within `T_detect` of a fixed cadence.
7. **Observer availability is itself a Surface, not a hardcoded exception**: FR-019 (kill-switch can't produce a false SAFE) must be a property of the generic surface model, not an `if aria_stopped: return UNAVAILABLE` special case. A reserved `surface_id='security-scientist-observer'` is written to by the `run.sh` wrapper itself (outside the Python collector) on every invocation; if the wrapper stops running (cron dead, host down), that surface's own evidence goes stale/absent through the SAME generic `state_at`/freshness machinery every other surface uses — no special-cased code path exists to get this wrong.
8. **Anti-loop invariant: the observer never mutates what it observes**: the Collector/Critic/Judge chain has no write path to anything outside its own evidence ledger (`security_scientist.db`) in this increment — never a lockfile, a process, a config file, or any artifact that could feed back into its own next measurement. Enforced by a static test over the AST of `security_scientist_observe.py`/`_critic.py`/`_judge.py`: any file-write or subprocess call targeting a path outside an explicit allowlist (the ledger DB, its own report output) fails the test.
9. **`T_detect` alone cannot cover a process shorter-lived than the discovery cadence**: a third review pass identified a real blind spot in refinement #6 -- a process that starts AND exits entirely between two discovery passes is never captured by any pass, regardless of how small `T_detect` is (100% of passes can be individually correct while `missed_surface_rate` for sub-cadence-lifetime processes is not provably zero). This is a fundamentally different property from staleness and must be reported as a distinct, explicit residual limit, never silently implied to be covered by `T_detect`: `missed_surface_rate` is tracked and reported SEPARATELY, and `T_detect`'s guarantee is explicitly scoped to "any process whose lifetime spans at least one full cadence window," never to all processes unconditionally. Closing this gap fully (e.g. real-time process-creation events via a kernel-level `proc_events`/`fanotify` listener) is banked as a future increment, not V1 -- V1's obligation is to state the limit honestly (per FR-002/SC-001's own "never claim more than proven" spirit) rather than to close it.
10. **Five invariants, formalized and reconciled with the approved plan's I2**: a fourth review pass distilled the discussion into five named invariants, restated here to avoid re-deriving them piecemeal across future documents (full definitions and their mapping to concrete tests: `data-model.md`).

## Technical Context

**Language/Version**: Python 3.12 (existing `aria_core` / `scripts/` stack), stdlib-only for the new collector (no new dependency — `psutil` is not currently used anywhere in the project and is not introduced here; `/proc` is read directly, matching `security_posture_collect.py`'s existing "shell out or read the filesystem, no framework" style).

**Primary Dependencies**: `aiosqlite` (existing, for the new persistence tables), stdlib `os`/`pathlib`/`hashlib`/`subprocess` (one batched `systemctl list-units` call per pass for declared-unit cross-reference, not a per-process subprocess spawn), existing `security_posture.py` (`Evidence`, `measured`, `unknown`, `surface_coverage`, `apply_freshness`, `aggregate` — imported, never re-implemented), existing `aria_core.system_issues` (finding registry), existing `aria_core.paths.aria_db_path`, existing `research/` state machine (`CONSTITUTION.md`/`PROTOCOL.md`/`hypotheses/`) for `H-SEC-*`.

**Storage**: three new SQLite tables (`security_observations` immutable, `security_rejected_evidence` append-only audit, `security_evaluations` derived/append-only) in a DEDICATED file, `DATA_DIR/security_scientist.db` — physically separate from `aria.db`, reusing `gate_audit_log.py`'s query/history discipline (`state_at(gate, at)` -> `state_at(surface_id, at)`) but NOT its file. Revised from an earlier draft that would have shared `aria.db`: the founding incident review's independence requirement (#4, see "Refinements from review") means the Security Scientist must keep working even if `aria-api`'s own database file is locked, corrupted, or the container is stopped — sharing the file both undermines that independence and reproduces the exact class of incident `shadow_db_path()` was created to close (17/08: two long-running processes writing the same SQLite file, even in WAL mode, produced sustained "database is locked" failures on unrelated prod tasks). A dedicated file makes "does the Scientist depend on anything ARIA owns" a testable no by construction, not a claim.

**Testing**: pytest (existing `packages/aria-core/tests/`); new `test_security_scientist_observe.py` (collector, stdlib-only, disposable-venv negative tests), `test_security_scientist_judge.py` (Collector/Critic/Judge separation, the eight failure codes, the meta-adversarial 10-case suite A-J from the approved plan), extends `test_security_posture.py` (four coverage properties, `state_at`); GitHub Actions `security-mutation.yml` (new workflow, CI-tier mutation testing per the approved plan's step 5 table).

**Target Platform**: the VPS host directly (not the `aria-api` container) — the whole point of this feature is to observe the host's real running processes, including ones outside Docker; invoked by a thin `run.sh` outside the repo (same pattern as every other `*-watch` mechanism in `docs/registre-automatisations.md`), never the in-container heartbeat (300s per-task ceiling, dies with ARIA, would violate invariant I2).

**Project Type**: single backend feature, CLI-invoked scripts + a small `aria_core` persistence module; no frontend, no new service process.

**Performance Goals**: not latency-sensitive (event-driven + one weekly campaign per the approved plan's cadence decision). The discovery pass itself must stay well under the host's 3.8GB RAM / ~1.5GB available budget and complete in low seconds for the current process count (~dozens, not thousands) — measured with `time`, not assumed, before being wired into any cron cadence.

**Constraints**: LEVEL 3 only (no automatic remediation reaches production); no permanent process (Observer is a bounded CLI pass, not a daemon); `TMPDIR=/opt/aria-data/tmp-trivy` for any future scanner step (steps 5+, not this increment); nothing added to `CLAUDE.md` (already over its CI cap); secrets metadata-only, never read/displayed (environment variables are recorded as a set of present *names*, never values); idempotent writes (up to 4 concurrent Claude sessions on the VPS).

**Scale/Scope**: this plan designs all six user stories; the first implementation increment is User Story 1 (runtime discovery) plus the shared Collector/Critic/Judge/persistence foundation that User Story 2 and 3 also depend on. User Stories 4-6 (contradiction detector, falsification hypotheses, constitution proposal + capability graph) reuse the same foundation and are sequenced after in `tasks.md`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Guardrail files untouched**: PASS — no change to `permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`.
- **Real capital**: PASS — this feature reads process/filesystem/systemd metadata only; it never touches a wallet, a trade, or a transfer path. Invariant I2 (kill-switch can never stop security observation) is honored by construction: nothing in this design imports or depends on `outgoing_pause`/`wallet_guard`.
- **Destructive git operations**: PASS — no such operation in scope.
- **Architectural coherence (CLAUDE.md 1bis)**: PASS — extends `security_posture.py`'s existing `Evidence`/`aggregate` contract rather than inventing a parallel status vocabulary; reuses `aria_core.system_issues` (not a new findings registry), `gate_audit_log.py`'s `state_at()` query/history PATTERN (not a new history mechanism — see Technical Context/Storage for the one deliberate deviation: its own dedicated DB file, not `aria_db_path()`, justified by the independence requirement below), and `research/`'s existing hypothesis state machine (not a new one).
- **LEVEL 3 autonomy (operator-frozen)**: PASS — nothing in this plan applies a fix, patch, or config change automatically; every remediation this feature can produce is a proposal artifact (a file under `research/` or a `system_issues` row) awaiting an explicit operator decision.
- **No new permanent process (operator-frozen)**: PASS — the collector is a bounded CLI invocation, cadenced by a `run.sh` cron entry, not a daemon; reasoning roles (Critic/Judge beyond the deterministic parts) run only when invoked, never as a standing loop.
- **Fail-safe / never fabricate**: PASS — an unclassifiable runtime is `UNKNOWN` and stays in the inventory forever, never silently dropped; a `SELF_ATTACK_INCOMPLETE` critique forces `UNKNOWN` regardless of how clean the observation looks (this is the mechanism, not a policy statement).
- **Secrets discipline**: PASS — environment metadata is recorded as variable *names* present/absent only; `/proc/<pid>/environ` values are never read into any persisted record. Enforced by a negative test asserting no persisted observation payload contains a value matched against a loaded `.env` value.
- **Independence from ARIA (review point #4)**: PASS — the Security Scientist's persistence is a dedicated file (`security_scientist.db`), its collector runs as a direct host-level CLI invocation (never through `aria-api`'s HTTP surface), and it imports no `aria_core` module that itself depends on a running ARIA process (only pure/data modules: `security_posture`, `aria_core.paths`, `aria_core.system_issues` for write-only reporting). Testable by killing `aria-api` and confirming the collector still runs and writes evidence.
- **Anti-loop (review point #6)**: PASS — no write path from the Collector/Critic/Judge to anything outside their own evidence ledger; mechanically enforced (see Refinements #8).
- **"Verify before asserting"**: PASS — the two decisive adversarial experiments (Quickstart) are a go/no-go gate on the foundation before any higher layer is built, per the approved plan's own step-5 discipline applied one step earlier.
- **Testability**: PASS — negative-test-first discipline (write the test that tries to manufacture an illegitimate PASS, confirm it fails against absent code, then implement the minimum that makes it pass), same pattern as `test_security_posture.py`'s existing 18 tests.

Post-design re-check: no violations (see Phase 1 outputs below).

## Project Structure

### Documentation (this feature)

```text
specs/019-security-scientist/
|- plan.md                         # this file
|- research.md                     # Phase 0 output
|- data-model.md                   # Phase 1 output
|- quickstart.md                   # Phase 1 output
|- contracts/security-scientist.md # Phase 1 output
|- checklists/requirements.md      # already produced by /speckit-specify
`- tasks.md                        # /speckit-tasks (not this command)
```

### Source Code (repository root)

```text
scripts/
|- security_posture.py                  # UNCHANGED (Evidence/aggregate contract, reused as-is)
|- security_posture_collect.py          # UNCHANGED (4 existing checks stay simple, single-layer)
|- security_scientist_observe.py        # NEW: Step 1 collector -- /proc + systemd + cgroup,
|                                        #      RuntimeObservation dataclass, zero verdict fields
|- security_scientist_critic.py         # NEW: independent critic, pure functions of Observation
|                                        #      data only, emits the 8 named failure codes
|- security_scientist_judge.py          # NEW: independent judge, (Observation, Critique) -> Evidence,
|                                        #      never accepts a pre-formed conclusion (type-enforced)
|- security_scientist_contradictions.py # NEW (Step 3): doc/code/runtime contradiction detector
`- security_hypothesis_check.py         # NEW (Step 4): PATH_COVERAGE / MODEL_COVERAGE checker for
                                         #      research/hypotheses/H-SEC-*.md

packages/aria-core/src/aria_core/
`- security_evidence.py                 # NEW: persistence -- security_observations (immutable),
                                         #      security_rejected_evidence (audit), security_evaluations
                                         #      (derived, append-only), state_at(surface_id, at),
                                         #      last_discovery_pass_age(now) for observation_gap.
                                         #      Same MODULE discipline as gate_audit_log.py, but its
                                         #      OWN dedicated DB file (security_scientist.db, not
                                         #      aria.db) -- independence from ARIA's own database,
                                         #      never contends with aria-api for the same file.

packages/aria-core/tests/
|- test_security_posture.py             # EXTENDED: 4 coverage properties, state_at, freshness edges
|- test_security_scientist_observe.py   # NEW: disposable-venv negative tests (Step 1 gate)
|- test_security_scientist_judge.py     # NEW: Collector/Critic/Judge separation, 8 failure codes,
|                                        #      meta-adversarial suite A-J
`- test_security_scientist_mutations.py # NEW: CI-tier mutation table (feeds security-mutation.yml)

research/hypotheses/
`- H-SEC-042-wallet-cannot-sign-from-vps.md  # NEW: first negative mission, canonical form example
                                              #      from the approved plan

.github/workflows/
`- security-mutation.yml                # NEW: Step 5, CI mutation testing, never touches the VPS

docs/
`- security-constitution-proposal.md    # NEW: Step 6, explicitly a PROPOSAL, not linked from
                                         #      CLAUDE.md until the operator approves it

/opt/aria-data/security-scientist-watch/
`- run.sh                               # NEW (outside the repo, matches docs/registre-automatisations.md
                                         #      pattern): cadences scripts/security_scientist_observe.py,
                                         #      zero logic, writes system_issues on UNKNOWN/UNOBSERVED/FAIL
```

**Structure Decision**: extend the `scripts/` + `aria_core` split the project already uses for `security_posture.py` (deterministic, shells-out/reads-filesystem logic in `scripts/`, versioned persistence in `aria_core`). The new Collector/Critic/Judge trio are three separate files specifically so the "independent critic" requirement is enforced structurally (a static test asserts `security_scientist_critic.py` never imports `security_scientist_observe.py`'s internals, only the `RuntimeObservation` data shape) rather than just by convention. First implementation increment: `security_scientist_observe.py` + `security_evidence.py` + `security_scientist_critic.py` + `security_scientist_judge.py` + their tests, gated by the two decisive adversarial experiments in Quickstart. Steps 3-7 (contradiction detector, hypotheses, CI mutations, constitution proposal, capability graph) reuse this same foundation and are sequenced after in `tasks.md`.

## Complexity Tracking

*No unjustified Constitution Check violations.* One deliberate scope decision, not a violation, recorded here for traceability: the four existing `security_posture_collect.py` checks are NOT retrofitted into the Collector/Critic/Judge separation.

| Decision | Why | Simpler alternative rejected because |
|---|---|---|
| Existing 4 checks (dependabot/osv/cli-version/session-guardrails) keep their current single-layer `sp.measured(id, ok, ...)` shape instead of being split into Collector/Critic/Judge | They are single deterministic facts (an open-alert count, a version string compare) where "the measurer also certifies its own measurement" is not a real risk class — unlike runtime discovery or a security hypothesis, there is no plausible "I checked myself and it's fine" failure mode for `cli --version == npm view version`. Splitting them would be scope creep against the plan's explicit "no scope creep beyond seven steps" discipline. | Retrofitting all four now would touch already-battle-tested code (18 green negative tests) for no measured safety gain, and would delay the actual gap (runtime discovery) this feature exists to close. |
