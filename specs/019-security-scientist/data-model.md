# Phase 1 Data Model: ARIA Security Scientist V1

Entities extracted from `spec.md`'s "Key Entities" section, formalized against `plan.md`/`research.md`'s technical decisions and the review's fourth pass (five named invariants, three-state path coverage, observer-as-surface with integrity/independence).

## Five invariants (formal, reconciled with the approved plan's I2)

Named here once so no later document re-derives them piecemeal. `I2` is kept EXACTLY as the approved plan (`/root/.claude/plans/abundant-giggling-cloud.md`) already names it, to avoid two different things sharing one label; the review's other four get the `SS-` prefix (Security Scientist).

| ID | Statement | Where it becomes a mechanical test |
|---|---|---|
| **SS-I1** — Reality precedes declaration | Running reality is authoritative for discovery; a declared/documented architecture is never treated as the ground truth for what exists. | `test_security_scientist_observe.py`: a disposable process outside any declared architecture is discovered anyway. |
| **SS-I2** — Evidence precedes verdict | No producer (Collector) can emit an authoritative `SAFE`/`status` field; only the Judge, reading raw facts + self-critique, derives one. | `test_security_scientist_judge.py`: a fabricated pre-formed conclusion in a Collector-shaped input is structurally unrepresentable (see Surface/Evaluation below) and, where it could leak in via a free-text field, is ignored (meta-adversarial case B). |
| **SS-I3** — Unknown is sticky | Missing, partial, or unverifiable coverage can never collapse into `PASS`; it collapses into `UNKNOWN`/`UNOBSERVED` at worst-wins aggregation. | Already proven for the existing contract by `test_security_posture.py`'s 18 tests; extended by the new `test_security_scientist_judge.py` for the Collector/Critic/Judge chain specifically. |
| **SS-I4** — Exhaustion != completeness | Having checked every currently known path does not prove no other path exists; a `PASS` from an investigation requires proof of the latter, not just the former. | `security_hypothesis_check.py` tests: `PASS` is unreachable while `path_set_completeness_proven` is unset, even with `paths_exhausted=true`. |
| **I2** (approved plan's own numbering, unchanged) — Observer independence | ARIA cannot stop, alter, suppress, or influence observation, evidence history, self-critiques, or verdict derivation; ARIA stopped => `ARIA = UNOBSERVABLE`, never `SECURITY = PASS`. | Constitution Check ("Independence from ARIA"); the combined founding adversarial test in `quickstart.md`. |

`SS-I2` is the formal name for what `plan.md`'s Refinements #2/#4 and `research.md` point 8 already implement; `SS-I4` is the formal name for Refinement #3; `SS-I1`/`SS-I3` restate what the existing `security_posture.py` contract and the new discovery collector already guarantee. Naming them here is bookkeeping, not new design — but every one of them must resolve to a real, already-planned test (right column), never live only as prose.

## Entities

### Surface

Anything the system can hold a safety opinion about (a running process, a dependency, a documented control, an investigation's target capability, or the observer itself — see "Observer as a Surface" below).

```text
Surface
  surface_id            TEXT, stable identifier
  kind                  ENUM(process, dependency, control, investigation, observer)
  discovered            BOOL   -- is this surface known to the system at all
  covered               BOOL   -- does any mechanism currently observe it
  # verified and fresh are NOT stored fields -- see "Status is never stored" below
```

**Status is never stored.** `verified`/`fresh` and the final reported status (`PASS`/`FAIL`/`UNOBSERVED`/`UNKNOWN`/`STALE`) are never columns on `Surface` or any other row. They are always the output of:

```text
status(surface_id, at) = judge(
    observations = security_observations WHERE surface_id = ? AND observed_at <= at,
    critique     = latest self-critique for those observations,
    rules        = sp.surface_coverage / sp.measured / sp.aggregate / sp.apply_freshness
)
```

i.e. `security_posture.surface_coverage()`/`aggregate()`/`apply_freshness()`, called fresh on every query, never cached as an authoritative field. This is `SS-I2`/review-refinement-#2 made structural: there is no column named `status` anywhere for an application to `UPDATE`, so "a stored SAFE that stops reflecting reality" is not a bug class that can exist in this schema.

### Observation

A single, immutable, timestamped raw measurement (`security_observations` table, `research.md` point 6). Carries `observed_at`/`recorded_at` (kept distinct — the gap is itself data, makes a TOCTOU visible), `observer_version`/`environment_identity` (G2 provenance), and `payload_json` (raw facts only — schema-rejects any key named `status`/`verdict`/`safe`/`unsafe`/`pass`/`fail`, mechanically enforcing that a Collector cannot smuggle a conclusion in even informally).

**Measurement completeness is itself a fact, not a conclusion.** Per the review's point on "conserving the loss of information": `payload_json` always carries explicit completeness flags alongside whatever it did measure — e.g. `{"stdout_complete": false, "exit_code": 0, "pid": 4821, ...}` — so a partial measurement is visible as a partial measurement. It is the Critic's job (next) to turn an incomplete-flagged payload into a blocking failure code, never the Collector's job to pre-interpret it.

### Self-Critique

Authored by a role structurally independent from the Observation's producer (`research.md` point 8). One of eight named failure codes, or none (full confidence), each mapped to the concrete failure classes raised across this review:

| Code | Meaning | Concrete scenario it catches |
|---|---|---|
| `COVERAGE_UNKNOWN` | The measurement's scope was incomplete or unproven | Truncated stdout; scan timed out mid-way; collector process killed before finishing |
| `IDENTITY_MISMATCH` | What was measured isn't provably what was claimed | PID reused since the measurement started; binary hash differs from the one that was actually running |
| `STALE_SOURCE` | The thing measured no longer exists / evidence outlived its subject | File deleted between read and evaluation; process exited during the check |
| `WRONG_EXECUTABLE` | `PATH`/resolution picked a different binary than intended | The exact `cli-version` PATH bug from the founding incident, generalized |
| `UNOBSERVABLE` | No mechanism could reach the surface at all | Permission denied on `/proc/<pid>`; surface outside any collector's reach |
| `SELF_DEPENDENCY` | The measurement's own correctness depends on the thing being measured | A check reads a version string emitted by the very process being audited for tampering |
| `SCOPE_TOO_NARROW` | The investigation's surface list is provably incomplete for its claim | Path list covers known integrations but not indirect credential paths (see `H-SEC-042`) |
| `NON_REPRODUCIBLE` | Re-running the same check on the same subject does not reproduce the result | Process state changed mid-scan (TOCTOU); scanner output differs run to run with no environmental change |

Any single code present => the Judge cannot return `PASS`/`SAFE` for that observation (`SS-I3`), regardless of how clean the raw payload otherwise looks.

### Evaluation (the Judge's output — an `Evidence`, `security_posture.py`'s existing type)

Derived-only, append-only (`security_evaluations`, `research.md` point 6). `status ∈ {PASS, FAIL, UNOBSERVED, UNKNOWN, STALE}` — reusing the existing five-value contract rather than adding a sixth (`UNAVAILABLE`) or a seventh (`UNSAFE`); see "State vocabulary" below for why.

### Rejected Evidence Attempt

An Observation submitted without verifiable provenance (`observer_version`/`environment_identity` missing) — recorded in `security_rejected_evidence`, never usable to derive a verdict, itself the numerator of SC-007's "false certainty prevented" counter (G2).

### Contradiction

A detected mismatch between declared (doc/config) and actual (code/runtime) reality — one `system_issues` row, `source='security-scientist'`, `dedup_key` = a stable hash of `(kind, location)` so re-detection while still open never duplicates (FR-014).

### Security Investigation (Hypothesis) — three-state path coverage

Refines Refinement #3's two-state `PATH_COVERAGE`/`MODEL_COVERAGE` into three explicit, independently-recorded booleans (review's fourth pass, more precise than the third):

```text
SecurityInvestigation
  investigation_id         TEXT  (e.g. "H-SEC-042")
  paths_identified          [TEXT]   -- the enumerated list of paths to rule out (frozen at creation)
  paths_exhausted           BOOL     -- every entry in paths_identified has been checked
  path_set_completeness_proven  BOOL -- there is a positive argument/proof that paths_identified
                                      -- cannot be missing a relevant path (not just "none found")
  paths_with_findings       [TEXT]   -- any path that revealed the capability working
```

Derivation rule (mirrors `SS-I4`):

```text
verdict =
    FAIL     if paths_with_findings is non-empty
    UNKNOWN  if not paths_exhausted
    UNKNOWN  if paths_exhausted and not path_set_completeness_proven
             (labeled explicitly: "path-exhausted, model-unproven" -- a distinct,
              weaker, honest outcome, never silently promoted to PASS)
    PASS     only if paths_exhausted AND path_set_completeness_proven
             AND paths_with_findings is empty AND evidence still fresh
             AND self-critique complete
```

`paths_identified` existing at all (the list was written down) is distinct from `paths_exhausted` (the list was worked through) which is distinct again from `path_set_completeness_proven` (there is a reason to believe the list itself is not missing an entry) — collapsing any two of these three into one flag is exactly how "I checked everything I thought of" quietly becomes "therefore it's safe," the review's central objection to a naive reading of FR-015.

### Proposed Safety Rule (Constitution Invariant)

A proposal row (`docs/security-constitution-proposal.md`, Step 6) — inert text until an operator decision is recorded; never auto-binding (FR-017/018).

### Capability-Barrier Mapping

`capability, entry_points, enforcement_point, barrier_type ∈ {EXPLICIT_GATE, ON_CHAIN_GUARD, CODE_INVARIANT, PERMISSION_BOUNDARY, NETWORK_BOUNDARY, OPERATIONAL_ONLY, NONE}, fail_mode, evidence` — Step 7's output, feeding candidate invariants into Step 6 and priority `UNKNOWN`s into the campaign queue.

## Observer as a Surface (review point #5, refined)

The Security Scientist's own operation is a `Surface` (`kind=observer`, `surface_id="security-scientist-observer"`, `research.md` point 12) carrying SIX properties, not the generic four — the review's fourth pass adds two the third pass's version didn't have:

```text
discovered     -- always true by construction (it's a known, named surface)
covered        -- always true by construction (run.sh writes its own heartbeat)
verified       -- did the LAST invocation actually complete (not just start)
fresh          -- is the last heartbeat within its own freshness window
integrity      -- does the currently-running collector's observer_version/hash match
                  what was last known-good (a silent, unexpected code change is itself
                  a finding -- reuses the exact hash-drift detection pattern already
                  proven in .claude/hooks/cognitive-runtime.sh for a different subsystem)
independence   -- structural, checked by the Constitution-Check test (killing aria-api
                  and confirming the collector still runs), not runtime-measured per pass
```

`verified`/`fresh`/`integrity` are computed the same derived-never-stored way as any other surface's status. `independence` is the one property that is a per-commit structural test result (does the current code have any forbidden import/dependency), not a per-pass runtime measurement — recorded as the outcome of the Constitution-Check test, timestamped, and itself expressible as an `Evidence` row so it participates in the same freshness/staleness machinery rather than living outside it.

## State vocabulary: five existing values are enough, mapped explicitly

The review's fourth pass proposes a six-value vocabulary (`SAFE`/`UNSAFE`/`UNOBSERVED`/`UNKNOWN`/`STALE`/`UNAVAILABLE`) and stresses `UNAVAILABLE != UNKNOWN` ("I looked and couldn't prove enough" vs. "I can't even guarantee I looked"). This project's existing, 18-negative-test-proven contract (`security_posture.PASS/FAIL/UNOBSERVED/UNKNOWN/STALE`) already draws exactly that line — mapped explicitly rather than adding a sixth/seventh value (which would fork the contract every other check in the project already relies on):

| Review's concept | This project's existing value | Why it's the same distinction |
|---|---|---|
| `SAFE` | `PASS` | complete, fresh proof |
| `UNSAFE` | `FAIL` | proof of an actual problem |
| `UNKNOWN` ("looked, not enough proof") | `UNKNOWN` | observed + covered, but not verified |
| `UNAVAILABLE` ("can't guarantee I looked") | `UNOBSERVED` | not discovered, OR discovered but no mechanism covers it -- `security_posture.surface_coverage()`'s own docstring already states this is "the surface is unknown, or nothing is looking at it," which is precisely "I can't even guarantee I looked" |
| `STALE` | `STALE` | proof existed, too old to trust now |

Concretely for the observer-availability case the review raises: if `run.sh` stops running entirely, `security-scientist-observer`'s own surface has no fresh heartbeat, so per `apply_freshness()` its evaluation decays from `PASS` to `STALE` (or `UNOBSERVED` if it never ran at all) through the SAME generic mechanism as any other surface -- no sixth state and no special-cased branch is needed, because `STALE`/`UNOBSERVED` already ARE this project's "the system that says it observes might not actually be observing" states. Adding `UNAVAILABLE` as a literal seventh/sixth value would duplicate `UNOBSERVED`/`STALE` rather than add a distinction the existing contract lacks.

## The combined founding adversarial test (design target for `quickstart.md`)

The review's fourth pass proposes merging the two decisive experiments (`research.md` point 9's disposable-process test, and the Judge-ignores-a-lying-SAFE test) into one scenario that also exercises independence simultaneously: spawn an undeclared process while `aria-api` is stopped, confirm `UNKNOWN`/`UNOBSERVED` classification and a continuing, non-`STALE` `security-scientist-observer` surface throughout; remove the process and confirm the removal is itself recorded; separately feed the Judge a fabricated observation whose payload implies confidence but whose critique is incomplete, and confirm `UNKNOWN` regardless. This becomes `quickstart.md`'s single end-to-end validation scenario rather than two disconnected unit tests, since running it with ARIA stopped is what actually proves `I2`/independence rather than merely asserting it.
