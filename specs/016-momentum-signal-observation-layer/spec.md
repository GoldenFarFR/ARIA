# Feature Specification: Momentum Signal Observation Layer

**Feature Branch**: `016-momentum-signal-observation-layer`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "Build a live observation layer on top of the momentum pipeline's already-existing signal families (on-chain, chart, social), without fabricating a new decision score and without touching automated execution. Record every candidate evaluated by the momentum pipeline, bought or rejected, with each signal family kept separate (no arbitrary weighting, no global_score), a `signal_version` + `data_timestamp` on every observation, and forward price performance at +1m/+5m/+15m/+1h/+4h — to experimentally answer: when on-chain, social and chart signals converge or diverge in real time, what happens to price next?"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture every evaluated candidate, bought or rejected (Priority: P1)

Every time the momentum pipeline evaluates a candidate token (`momentum_entry.py`'s hard-gate and scoring path), one observation record is captured: the three signal families as they stood at that moment (on-chain, chart, social), the pipeline's actual decision (BUY / HOLD / REJECT + reason), and enough metadata (timestamp, signal version) to make the record independently interpretable later — without changing anything about how that decision was reached.

**Why this priority**: Without capturing the full population — not just the tokens ARIA actually bought — there is no way to later tell whether a signal is genuinely discriminant or just correlated with candidates that already passed unrelated filters. This is the same lesson the DCE research already re-confirmed this session: a conclusion drawn from a filtered subset is not a measurement. This story alone already delivers standalone value: a queryable historical record of what the pipeline saw and decided, even before any forward-performance measurement exists.

**Independent Test**: Run the momentum pipeline against a mix of candidates that pass and fail its existing gates. Confirm one observation row exists per evaluated candidate (rejected candidates included), each carrying the three signal families separately (never merged into one number) plus the real decision taken.

**Acceptance Scenarios**:

1. **Given** a candidate that clears every existing gate and results in a BUY, **When** the momentum pipeline finishes evaluating it, **Then** an observation is recorded capturing the on-chain, chart, and social signal values as computed at that moment, the BUY decision, and a `signal_version`/`data_timestamp`.
2. **Given** a candidate rejected early (e.g. failed honeypot check before chart signals were even computed), **When** the momentum pipeline finishes evaluating it, **Then** an observation is still recorded, with the never-computed signal families explicitly marked "not evaluated" rather than silently omitted or defaulted to a neutral value.
3. **Given** the same token evaluated twice at different times (e.g. re-scanned after a discovery re-poll), **When** both evaluations complete, **Then** two distinct observations are recorded, not one overwritten by the other.

---

### User Story 2 - Measure forward price performance per observation (Priority: P2)

For every recorded observation (bought and rejected alike), the system tracks what actually happened to the token's price afterward, at five fixed horizons (+1m, +5m, +15m, +1h, +4h) relative to the price at decision time.

**Why this priority**: Builds directly on User Story 1's captured population. Without forward performance attached to every observation — not only the bought subset that already gets exit tracking — the central experimental question ("when signals converge or diverge, what happens to price next?") cannot be answered for the rejected majority, which is precisely where a false-negative signal would be hiding.

**Independent Test**: Take a set of already-captured observations (including rejected candidates with no open position) and confirm that, as time passes, each of the five horizons gets a price reading (or an explicit "unavailable" status with a reason) without needing a position to have been opened.

**Acceptance Scenarios**:

1. **Given** an observation for a rejected candidate with a valid price reference at decision time, **When** 5 minutes have elapsed, **Then** the +5m forward price and its percentage delta versus the decision-time price are recorded.
2. **Given** an observation whose token becomes unpriceable shortly after (e.g. liquidity pulled, pool untracked), **When** a horizon's measurement is attempted, **Then** that horizon is recorded as explicitly unavailable with a reason, never a fabricated or last-known-stale price presented as current.
3. **Given** an observation less than 4 hours old, **When** its forward-performance record is queried, **Then** the horizons not yet reached show as "pending", distinguishable from "measured" and from "unavailable".

---

### User Story 3 - Make signal availability and staleness explicit, especially for social (Priority: P3)

Each recorded signal family (particularly social, since `signal_cascade_*`/`radar_x` run on independent periodic cycles rather than synchronously with the momentum decision) carries an explicit availability/freshness state, so that "this signal was not yet known for this token" is never confused with "this signal was checked and found neutral/absent".

**Why this priority**: Refines the fidelity of Story 1's captured data. On-chain and chart signals are already computed synchronously inside the momentum decision path, so their availability is close to guaranteed by construction. Social signals are structurally different — `signal_cascade_x/web/farcaster/github` and `radar_x` run on independent 15-60 minute cycles and may simply never have touched a given token by decision time. Without this distinction, an absent social signal would silently masquerade as "checked, nothing found" and quietly bias any later convergence analysis toward "social never matters", which would be a measurement artifact, not a finding.

**Independent Test**: Evaluate a freshly-discovered candidate that the social-signal cycles have not yet processed. Confirm its observation explicitly marks social sub-signals as "not available" (with the reason: never scanned) rather than as a neutral/zero value, and separately confirm a candidate whose social signal was computed 40 minutes prior is marked "available" with its own `data_timestamp`, distinct from the observation's own decision timestamp.

**Acceptance Scenarios**:

1. **Given** a token never touched by any social-signal cycle, **When** its momentum candidate observation is captured, **Then** every social sub-signal is recorded as "not available", not as a zero/neutral score.
2. **Given** a token whose `conviction_research` score was computed synchronously during this same decision, **When** the observation is captured, **Then** that sub-signal's `data_timestamp` equals the decision timestamp (fresh by construction).
3. **Given** a token whose `signal_cascade_x` convergence score was last computed 40 minutes before this decision, **When** the observation is captured, **Then** that sub-signal is recorded as "available" together with its own `data_timestamp` (not the decision timestamp), so staleness is computable later.

---

### Edge Cases

- What happens when a candidate is rejected before any price reference is ever established (e.g. failed a check that runs before the pipeline has fetched a liquidity/price snapshot)? → The observation is still captured; forward-performance tracking for that observation starts as "unavailable, no reference price", never a fabricated horizon.
- How does the system handle a token that stops being trackable entirely (rug, delisting, pool removed) before some forward horizons are reached? → Each unreached horizon is marked "unavailable" with a reason, never silently dropped from the record and never backfilled with a guessed value.
- What happens if the momentum pipeline itself changes (a threshold, a new gate, a new signal source) between two observations? → Both observations remain valid on their own terms; each carries the `signal_version` that was active when it was captured, so a later analysis can group or exclude observations by version rather than silently mixing incomparable pipeline states.
- What happens when a social sub-signal source is entirely disabled at the time of the decision (its `ARIA_*_ENABLED` gate is off)? → Recorded as "not available", with the reason distinguishing "gate disabled" from "not yet scanned" — both are non-availability, but the reason differs and both matter for later analysis.
- What happens when two of the three signal families disagree strongly (e.g. chart says strong entry, on-chain score is weak) on a candidate that still results in a BUY (because chart alone already gates the decision today)? → The observation still records all three families as measured, plus the real decision; the analysis of convergence/divergence happens later, out of scope for this feature, using this raw data.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record exactly one observation for every candidate evaluated by the momentum decision pipeline, with no sampling and no exclusion based on the outcome (bought, rejected at any gate, or held).
- **FR-002**: Each observation MUST capture the on-chain, chart, and social signal families as three separate records — the system MUST NOT compute or store any combined/weighted score across families in this feature.
- **FR-003**: Each observation MUST record the pipeline's actual decision (BUY / HOLD / REJECT) together with the reason already produced by the existing decision logic, verbatim — never a decision the observation layer infers or recomputes independently.
- **FR-004**: Each observation MUST carry a `signal_version` identifying the state of the pipeline's signal-computation logic at capture time, and a `data_timestamp` per signal family (not just one observation-level timestamp), so that a signal computed asynchronously before the decision is distinguishable in time from the decision itself.
- **FR-005**: For every signal family and sub-signal, the system MUST distinguish "not available" (never computed / gate disabled / not yet scanned) from any computed value, including a computed value that happens to be neutral or zero — a missing signal MUST NEVER be silently defaulted to a neutral or zero value.
- **FR-006**: The system MUST track forward price performance for every observation, not only for observations tied to an opened position, at five fixed horizons after the decision timestamp: +1 minute, +5 minutes, +15 minutes, +1 hour, +4 hours.
- **FR-007**: Each forward-performance horizon MUST resolve to exactly one of three states: measured (with a price and a percentage delta versus the decision-time reference price), pending (horizon not yet reached), or unavailable (with a reason — e.g. no reference price at decision time, token no longer priceable). The system MUST NEVER report a fabricated or stale-but-unlabeled price as a measured horizon.
- **FR-008**: Capturing an observation MUST occur strictly after the momentum pipeline's real decision has been made, and MUST NOT alter, delay, or gate that decision in any way — the observation layer is a read-after-decide side effect, never a precondition.
- **FR-009**: This feature MUST NOT modify any existing threshold, gate, or decision logic in `momentum_entry.py`, `paper_trader.py`, `dex_composite_score.py`, `conviction_research.py`, any `signal_cascade_*` module, or `radar_x.py` — it only reads values these modules already compute.
- **FR-010**: Every signal value captured in an observation MUST be the real value as it existed at decision time (or, for asynchronous social signals, the most recent value already computed by that time) — the system MUST NEVER recompute a signal after the fact using information that was not yet available at decision time (no look-ahead).
- **FR-011**: Observations MUST remain independently queryable and interpretable after the underlying pipeline changes in the future — a future change to a threshold or signal source MUST NOT require rewriting or invalidating past observations, only produce new observations under a new `signal_version`.

### Key Entities *(include if feature involves data)*

- **Signal Observation**: One row per candidate evaluation. Attributes: the evaluated token/pool identity, the decision timestamp, the pipeline's real decision (BUY/HOLD/REJECT + reason), `signal_version`, and three signal-family blocks (on-chain, chart, social) — each block holding its constituent sub-signals' raw values, an availability state per sub-signal, and a `data_timestamp` per sub-signal.
- **Forward Performance Record**: Tied to one Signal Observation. Attributes: the five fixed horizons, each with a state (measured / pending / unavailable), a measured price and percentage delta when measured, and a reason when unavailable. The reference price used for all deltas is the price at the observation's decision timestamp.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of candidates evaluated by the momentum pipeline during any measurement window produce exactly one observation record — verified by comparing the observation count against the pipeline's own decision-log count over the same window, with zero unexplained gap.
- **SC-002**: For any given observation, an analyst can determine, for each of the three signal families independently, whether it was available at decision time and its raw value if so — with zero ambiguity between "not available" and "available and neutral" (verified by inspecting a sample that includes at least one genuinely neutral value and one genuinely unavailable value per family).
- **SC-003**: At least 90% of observations older than 4 hours have a resolved (measured or explicitly unavailable, never missing) state for all five forward-performance horizons.
- **SC-004**: Zero change in the momentum pipeline's actual BUY/HOLD/REJECT decisions attributable to this feature, verified by comparing decision outcomes on an identical candidate set before and after the observation layer is wired in.
- **SC-005**: An observation captured before a future pipeline change remains fully interpretable afterward — verified by successfully querying and correctly labeling observations spanning at least two different `signal_version` values.
- **SC-006**: Using only the data captured by this feature, an analyst can, for a given sample of observations, identify cases where the three signal families agreed or disagreed and compare their associated forward performance — directly answering the motivating question ("when signals converge or diverge, what happens to price next?") without needing any additional instrumentation.

## Assumptions

- Persistence reuses this repo's existing storage conventions (the shared SQLite store under `DATA_DIR`), consistent with every other pocket/shadow module — the exact schema and storage location are a Phase 1 (`/speckit-plan`) design decision, not part of this specification.
- Forward-performance tracking requires a price-following mechanism for candidates that are REJECTED (no position ever opened), which today only exists for bought positions via each pocket's own exit tracking — this feature introduces that capability for the observation population as a whole; the specific source used to read forward prices is a Phase 1 decision, and MUST follow this repo's existing "never fabricate a price" doctrine (an unpriceable token yields "unavailable", never an invented multiplier).
- This feature ships no dashboard or UI. Analysis of convergence/divergence against forward performance (Success Criterion SC-006) is expected to happen via ad hoc SQL queries against the persisted observations, consistent with this repo's standing aggregation-over-sampling doctrine — a dedicated analysis tool, if useful later, is out of scope here.
- Scope is limited to the momentum pipeline (`momentum_entry.py` and its direct callers `paper_trader.py`/momentum-driven shadow pockets). The VC/thesis pipeline (`screened_pool`/`token_absorber`) and non-momentum shadow pockets with independent entry logic are out of scope — they were not covered by this session's audit and have their own decision paths.
- The five forward-performance horizons (+1m/+5m/+15m/+1h/+4h) are fixed as specified by the operator; no additional horizons are added in this feature.
- No automatic decision-making, sizing change, or execution change of any kind is introduced by this feature — it is observation-only, matching the operator's explicit instruction not to touch automated execution.
