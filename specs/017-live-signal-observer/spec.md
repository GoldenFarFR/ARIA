# Feature Specification: Live Signal Observer

**Feature Branch**: `017-live-signal-observer`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "A dedicated, execution-free service that discovers momentum candidates continuously, evaluates them through the existing momentum signal pipeline, persists each observation through the already-deployed observation layer (specs/016), and sends a dedicated, clearly non-executional Telegram signal exposing the on-chain, chart and social families separately with a convergence status — generated even while paper-trading is paused. Operator's single objective: put an ARIA on-chain + social -> Telegram signal into production. No auto-trading, no global score."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Candidates keep being evaluated while paper-trading is paused (Priority: P1)

The operator has deliberately paused paper-trading (`/offpaper`, since 2026-08-24). Today that pause also silently stops every candidate evaluation, because the only evaluation path is fused with position opening. After this feature, fresh candidates surfaced by the existing discovery source are evaluated through the existing signal pipeline — producing an observation row and forward-tracking rows exactly as specs/016 designed — without any position, paper or real, ever being opened, and without touching the paused execution path.

**Why this priority**: This is the whole reason the feature exists. specs/016's observation layer was deployed and verified but captured zero observations, because its only callers are paused/disabled. Without a decoupled evaluation path, no observation, no forward performance, and no Telegram signal can ever exist while execution stays paused — which the operator wants to keep paused. Everything else in this feature builds on this story.

**Independent Test**: With paper-trading confirmed paused, run the service for one discovery interval; confirm new observation rows appear in the observation table for freshly-discovered candidates, and confirm zero rows were added to any paper-position or limit-order table during the same window.

**Acceptance Scenarios**:

1. **Given** paper-trading is paused and a fresh candidate is discovered, **When** the service processes it, **Then** an observation row for that candidate exists with its decision and signal families captured, and no position or pending order was created anywhere.
2. **Given** paper-trading is paused, **When** the service runs for a full hour, **Then** the number of candidates it evaluates never exceeds the existing pipeline's hourly evaluation ceiling (the same cap the execution path honors), so this feature adds no new load class on shared external providers.
3. **Given** a candidate that was already evaluated within the existing rescan cooldown and whose price has not moved beyond the existing threshold, **When** it is rediscovered, **Then** it is NOT re-evaluated — the same dedup/cooldown discipline as the execution path applies, never a looser one.

---

### User Story 2 - A dedicated live-signal message reaches Telegram, never confusable with a trade (Priority: P2)

For an evaluated candidate whose observation is informative enough, the operator receives a Telegram message in a dedicated live-signal format: the three signal families shown separately (each with a 0-100 readability figure and a data-quality label), one convergence status, the contract, and a timestamp — using vocabulary that can never be mistaken for an executed trade.

**Why this priority**: The operator's stated objective is a *reliable Telegram signal* — "Telegram devient notre écran de contrôle du signal live, pas encore notre moteur de décision". The message is how the observed signals become visible in real time, so that convergence/divergence can be watched against what the market does next, before any scoring is ever automated.

**Independent Test**: Trigger evaluation of a candidate with a known full set of signals; confirm a Telegram message is sent to the configured signal chat in the dedicated format, that it contains none of the banned execution words, and that it is not routed through the trade-notification path.

**Acceptance Scenarios**:

1. **Given** an observation with all three families available, **When** the message is built, **Then** it shows ON-CHAIN, SOCIAL and CHART each with their own figure and data-quality label, plus exactly one status from the four allowed, and never a single combined score.
2. **Given** any signal message, **When** its text is inspected, **Then** it contains none of the words `BUY`, `ENTRY`, `OPENED`, `FILLED` (case-insensitive), and its header identifies it as a live signal, not a trade.
3. **Given** the signal chat identifier is not configured, **When** a message would be sent, **Then** it falls back to the existing operator channel (the operator's explicit choice for the first version) — never silently dropped, never sent to an unrelated chat.

---

### User Story 3 - Data quality is never mistaken for a weak signal (Priority: P3)

When a family's inputs are mostly unavailable (provider outage, price absent, source stale, quota reached, or a sub-signal the pipeline never computed for this candidate), the message shows that family's data quality as LOW and the overall status as DATA INCOMPLETE — it never shows a low figure that a reader would interpret as "the signal is bad".

**Why this priority**: Operator's explicit rule: "On ne doit jamais transformer API indisponible / prix absent / source stale / cap atteint en `score = 0`... Sinon on confondra signal négatif et absence de signal." specs/016 already guarantees this at the stored sub-signal level; this story carries the same guarantee up into the per-family figures and the human-readable message, so the Telegram screen cannot lie by omission.

**Independent Test**: Build the message for an observation where the on-chain composite was never computed (an early-rejected candidate) and where a social source has a value older than its freshness threshold; confirm on-chain data quality reads LOW with no on-chain figure presented as a real score, the stale social sub-signal is labeled stale rather than counted as fresh, and the status is DATA INCOMPLETE.

**Acceptance Scenarios**:

1. **Given** an observation whose on-chain composite sub-signals are all `not available`, **When** the message is built, **Then** the ON-CHAIN block shows data quality LOW and no numeric figure is presented as if it were a measured score.
2. **Given** a social sub-signal whose recorded data timestamp is older than that source's freshness threshold, **When** the family figure is computed, **Then** that sub-signal counts as STALE (excluded from the favorable/unfavorable tally and flagged), never as a fresh favorable or unfavorable reading.
3. **Given** a family whose available (fresh) sub-signals fall below the minimum needed to judge it, **When** the status is computed, **Then** the status is DATA INCOMPLETE regardless of what the other families say.

---

### Edge Cases

- What happens when the kill-switch (`/stop`) is armed while the service runs? → Resolved in planning from the kill-switch's real scope; until then the service treats an armed kill-switch as "do not send" (fail-closed for the outbound message), while evaluation/observation may continue since they execute nothing — the exact posture is a Phase 0 decision, never a guess.
- What happens when the same token would trigger a message repeatedly (re-evaluated after a price move, or evaluated on several drains)? → A per-token notification cooldown applies; a token already notified within the cooldown is observed (row still written) but not re-notified.
- What happens when a candidate is evaluated but its status is MIXED or DATA INCOMPLETE? → Observed (row written, forward tracking created) but NOT sent as a continuous-stream message; only statuses above the sending threshold chosen in planning are sent, so Telegram never becomes a firehose.
- What happens when the Telegram send fails (network, rate limit)? → The observation is already persisted before sending; the send failure is logged and never retried in a tight loop, and never causes the evaluation of other candidates to stop.
- What happens when the existing discovery source returns a candidate on a chain outside the pipeline's active chain set? → Ignored, same filter as the execution path (today Base only), so no chain enters the signal that the pipeline does not already evaluate.
- What happens when the pipeline's evaluation itself raises for one candidate? → That candidate is skipped and logged; the service continues with the next candidate and the next drain — one bad candidate never stalls discovery.
- What happens if the observation layer's capture fails for a candidate (its own best-effort try/except swallows the error)? → No observation row means no message for that candidate this drain; the service does not build a message from a decision that was not persisted, so the Telegram screen and the observation table never disagree about what was seen.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The service MUST discover fresh candidates from the same source the execution path uses, and MUST apply the same chain filter, reference-token exclusion, deduplication window, adaptive rescan cooldown, per-drain batch size, and hourly evaluation ceiling as the execution path — never looser, so it adds no new load pattern on shared external providers.
- **FR-002**: The service MUST evaluate each accepted candidate through the existing momentum signal pipeline using the same portfolio-wide evaluation parameters the execution path would use at that moment (trading mode, current regime), so the observed signals are identical to what the execution path would have computed — never a second, diverging decision path.
- **FR-003**: The service MUST keep evaluating and observing while paper-trading is paused (`/offpaper` active). Paper-trading's pause state MUST have no effect on discovery, evaluation, or observation.
- **FR-004**: The service MUST NOT open a position (paper or real), place or process a pending order, or call any function whose purpose is execution. This is a structural property, verified by a test that asserts zero new rows in position and order tables after a full evaluation.
- **FR-005**: The service MUST NOT modify the existing discovery service, the paper-trading module, the momentum pipeline's decision logic, any guardrail file, or the kill-switch mechanism. It reuses their public building blocks by import, never by restating their values.
- **FR-006**: Observation persistence MUST come from the already-deployed observation layer (specs/016) via the existing evaluation wrapper — this feature adds no second capture path and no second observation schema.
- **FR-007**: For each family, the service MUST derive a data-quality label (HIGH / MEDIUM / LOW) from the share of that family's sub-signals that are available AND fresh, and MUST classify a sub-signal as STALE when its recorded data timestamp is older than a per-source freshness threshold — computed at presentation time from the existing stored timestamp, never stored as a new state that would fork the specs/016 schema.
- **FR-008**: The per-family 0-100 figure MUST be presented only when that family's data quality is at least MEDIUM; a family at LOW quality MUST show no figure presented as a score. The on-chain figure MUST reuse the pipeline's existing composite when present; chart and social figures are explicitly-uncalibrated readability heuristics, labeled as such, and MUST NOT be used to gate anything.
- **FR-009**: The service MUST classify each observation into exactly one status — CONVERGENCE, MIXED, DIVERGENCE, or DATA INCOMPLETE — where DATA INCOMPLETE takes precedence whenever any family's quality is LOW, and the other three are derived from the agreement of the families that are at least MEDIUM quality. No status MUST ever be derived from a single combined cross-family number.
- **FR-010**: The Telegram message MUST use a dedicated live-signal format, sent through the low-level message primitive to a configurable signal chat (with the existing operator channel as the fallback when unconfigured), and MUST NEVER go through the trade-notification path. Its text MUST NOT contain `BUY`, `ENTRY`, `OPENED`, or `FILLED`.
- **FR-011**: The service MUST send a message only for observations whose status meets the sending threshold chosen in planning, and MUST honor a per-token notification cooldown — observations below the threshold or within the cooldown are persisted but not sent.
- **FR-012**: The service MUST be gated by its own dedicated enable flag, OFF by default in code, and MUST honor the kill-switch's real scope as determined in planning (fail-closed for the outbound message if the scope is ambiguous).
- **FR-013**: The service MUST be resilient per candidate and per drain: one candidate's evaluation failure, one send failure, or one discovery-source outage MUST NOT stop the service or the other candidates.

### Key Entities *(include if feature involves data)*

- **Live Signal Observation** (not a new stored entity): the specs/016 observation row plus its forward-performance rows, produced through the existing wrapper. This feature reads it back to build the message.
- **Family Presentation**: derived, not stored — for each of the three families: the fresh/available sub-signal tally, a data-quality label (HIGH/MEDIUM/LOW), and an optional 0-100 figure (present only at MEDIUM or above).
- **Signal Status**: derived, not stored — one of CONVERGENCE / MIXED / DIVERGENCE / DATA INCOMPLETE.
- **Notification State**: the minimal record needed to enforce the per-token cooldown (last notified time per token/chain) — its exact placement is a planning decision.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With paper-trading paused for the entire window, at least one new observation row is produced within the first two discovery intervals after the service starts, and the observation count keeps growing over a subsequent 24-hour window — proving the decoupling works in production, not only in a test.
- **SC-002**: Over any 24-hour window with the service running, the count of new position rows and pending-order rows attributable to this service is exactly zero (verified against the position and order tables).
- **SC-003**: 100% of sent signal messages contain none of the four banned execution words, are routed to the configured signal chat (or the operator channel fallback), and never through the trade-notification path.
- **SC-004**: The hourly number of candidates the service evaluates never exceeds the execution path's existing hourly ceiling, measured over the same 24-hour window.
- **SC-005**: For every observation where a family has all sub-signals unavailable, that family reads data quality LOW and status DATA INCOMPLETE in the message — zero cases of a LOW-quality family presented with a numeric score.
- **SC-006**: No single token is notified more than once within the notification cooldown, verified over a 24-hour window.
- **SC-007**: An operator reading a day of messages can, for each one, later match it to its observation row and its forward-performance rows by contract and timestamp — the message and the table never disagree about what was observed.

## Assumptions

- The existing discovery source (the same DexScreener-based feed the execution path uses) remains the single candidate source for this feature; no new sourcing provider is added.
- The active chain set stays what the pipeline currently evaluates (Base only today). Consequently the open critical issues on Robinhood Chain's RPC budget (#289) and Solana's DexPaprika outage (#278) are out of this feature's scope; if the chain set is later widened, they become relevant then, not now.
- Whether the on-chain signal computation depends on the CoinGecko WETH/USD price (#271) is to be VERIFIED during planning, never assumed — if it does, the affected sub-signal surfaces as unavailable (data quality), never as a wrong value, and the fix itself stays out of this feature's scope.
- CI status (Dependabot/OSV) is out of scope unless it blocks deployment.
- The first version sends to the existing operator channel; a separate Telegram channel is a configuration change later, not a code change.
- The per-family figures for chart and social are first-pass readability heuristics only; their calibration against forward performance is precisely the future work the observation layer exists to enable, not part of this feature.
- The kill-switch's real scope, the notification anti-spam values (cooldown, sending threshold), the process shape (background service vs. periodic cycle), and the exact mechanism to enable the new gate in the running production container are Phase 0 research items, resolved before any implementation.
- Everything on the operator's pause list (DCE reconstruction, getLogs rebuild, InsightX, Bubblemaps, historical price enrichment, DCE winner/loser analysis, auto-trading) stays untouched.
