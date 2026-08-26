# Feature Specification: Recalibrate the Solana late-bonding shadow pocket's regime gate

**Feature Branch**: `011-solana-regime-recalibration`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Recalibrer le seuil du régime gate de la poche shadow Solana (solana_late_bonding_shadow.py), suite au diagnostic du 26/08 : REGIME_MIN_MEDIAN_PEAK_PCT a été relevé de 25.0 à 40.0 le 24/08 (décision opérateur, logique de capture-gap). Mesuré depuis (2980 lignes, 30x le seuil n>=100) : médiane de peak globale 17.8%, moyenne 34%. Simulation de la médiane glissante sur 30 candidats contre plusieurs seuils : 10%->85.9% du temps ouvert ... 40% (actuel)->5.6%. Zéro nouvelle clôture depuis 15h+ malgré un curve tracker actif. Tension à trancher : le seuil 40% protège contre un piège de capture mesuré, mais bloque presque totalement l'accumulation de données. Hors Fast-Track (paramètre de stratégie) -- cycle spec-kit complet requis. Shadow/simulation uniquement, kill-switch /stop reste armé par l'opérateur."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The pocket resumes producing closures without reopening the capture-gap defect (Priority: P1) 🎯 MVP

The Solana late-bonding shadow pocket needs to close enough new positions to keep validating or invalidating its own strategy. Since the regime gate's threshold was raised to 40% on 2026-08-24, the gate has been open only ~5.6% of the time, producing zero new closures for 15h+ despite the curve tracker actively finding in-band candidates. At the same time, the reason the threshold was raised in the first place was real and measured: a 20% threshold let positions open that only captured -11.74% on average against a real +16.23% average peak (the trailing-stop math ate the difference). Any recalibration must resume meaningful closure volume without silently reopening that exact defect.

**Why this priority**: Without this, the pocket cannot accumulate the sample it needs to ever validate or invalidate the +25%/trade target — the whole reason the pocket exists.

**Independent Test**: query `solana_late_bonding_shadow_log` after deployment — new rows with `detected_at`/`exit_reason` after the fix ships, at a rate consistent with the recalibrated threshold's simulated open-time.

**Acceptance Scenarios**:

1. **Given** the regime gate closed at 40% for 15h+, **When** the threshold is recalibrated to the value chosen in the plan/research phase, **Then** new candidates start clearing the gate and opening shadow positions within a timeframe consistent with the recalibrated open-time percentage.
2. **Given** a recalibrated, lower threshold, **When** a position closes, **Then** its captured return is tracked against the same trailing-stop capture-gap metric that motivated the original 24/08 raise, so a regression toward the pre-24/08 defect is visible, not hidden.

---

### User Story 2 - The recalibration is traceable and derived from real measurement, not guessed (Priority: P2)

The chosen threshold must be an evidence-based compromise between "gate open often enough to learn" and "gate strict enough to avoid the pre-24/08 capture-gap defect" — not an arbitrary pick. The 26/08 diagnostic already measured simulated open-time at several candidate thresholds (10% through 40%) against the real rolling-median distribution; the plan phase must build on this real data, and the final choice (with its rationale) must be recorded exactly like every other pocket-parameter change in this project (`docs/pocket-parameters.json`, a HANDOFF entry).

**Why this priority**: An unrecorded or unreasoned threshold change would repeat the exact failure this project's "verify before asserting" doctrine exists to prevent — the next session would have to re-derive the same reasoning from scratch, or worse, trust a stale number.

**Independent Test**: `docs/pocket-parameters.json` and the relevant HANDOFF entry cite the new value, the prior value (40.0, raised 24/08), and the measured trade-off table (open-time vs. threshold) that justified the change.

**Acceptance Scenarios**:

1. **Given** the plan phase has measured the trade-off at several threshold candidates, **When** a final value is chosen, **Then** the rationale (why this value over the others measured) is written down alongside the change, not just the number.
2. **Given** the new threshold is deployed, **When** `pocket_entry_sweep` or an equivalent audit is run later, **Then** the recorded rationale lets a future session verify whether the recalibration achieved its intended trade-off.

---

### User Story 3 - A recalibration protocol exists toward the +25%/trade target, gated on a real sample (Priority: P3)

Same closure discipline as specs/010 (Robinhood): no number is forced today beyond what real data supports. The pocket needs a documented path for how and when the regime threshold (and the pocket's other parameters) get revisited as real closures accumulate, gated on the project's own n≥100 (provisional) and n≥1000 (validated) statistical bars — never a one-off guess left uncalibrated indefinitely.

**Why this priority**: Without this, the recalibration risks becoming another one-off tweak nobody revisits, the exact failure mode the Doctrine d'Ingestion and the n≥100/n≥1000 bars exist to prevent.

**Independent Test**: the protocol is written down, states the exact n≥100 and n≥1000 gates, the existing statistical safeguards to apply (outlier removal top-2/top-5, day-count coverage), and what happens if the sample stays insufficient for an extended period.

**Acceptance Scenarios**:

1. **Given** the pocket accumulates ≥100 closures under the new threshold, **When** a `pocket_entry_sweep`-style pass is run, **Then** it reports whether the capture-gap defect reopened and whether the average return trend supports keeping, raising, or lowering the threshold further.
2. **Given** fewer than 100 closures exist after a reasonable observation window, **When** the pocket is reviewed, **Then** the review explicitly states the sample is insufficient and why, rather than silently drawing a conclusion from too few trades.

---

### Edge Cases

- What happens if the recalibrated threshold reopens the pre-24/08 capture-gap defect (average captured return falls well below the average real peak again)? The recalibration must be revisited — this spec's closure bar (see Success Criteria) exists precisely to catch this rather than declare success from volume alone.
- What happens if, even at a much lower threshold, the real market stays calm enough that closure volume remains too low to learn from? The plan/research phase must consider whether the threshold is the only lever, or whether the observation window/candidate pool needs its own look — this spec's scope is the threshold, but a dead end here should be flagged, not silently micro-tweaked forever (per the project's own Auto-pivot doctrine).
- What happens to positions already open under the OLD 40% threshold at the moment the new value deploys? They must continue tracking under whatever rule they opened under — a threshold change must never retroactively alter or re-judge an already-open position's regime state.
- What happens if the curve tracker's candidate supply itself drops (fewer real bonding-curve tokens in band) at the same time the threshold changes? The independent test in User Story 1 must distinguish "gate still too strict" from "fewer real candidates exist right now" before concluding the recalibration failed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The regime gate MUST use a recalibrated `REGIME_MIN_MEDIAN_PEAK_PCT` value, chosen in the plan/research phase from the real measured trade-off (simulated open-time at each threshold candidate, 2026-08-26 diagnostic), not an arbitrary pick.
- **FR-002**: The chosen value MUST NOT regress the specific capture-gap defect that justified the 2026-08-24 raise to 40% (a 20% threshold's own measured -11.74% average captured return against a +16.23% average real peak) — the plan phase must state how the new value avoids or bounds this risk.
- **FR-003**: The system MUST continue logging every regime-gate decision (open/closed, per candidate) to `solana_regime_candidates_log` exactly as today, so the new threshold's real effect remains measurable the same way the current one was diagnosed.
- **FR-004**: The recalibration MUST be recorded in `docs/pocket-parameters.json` (regenerated, diff reviewed) and in a HANDOFF entry citing the prior value (40.0), the new value, and the measured rationale.
- **FR-005**: The pocket MUST remain shadow/simulation-only throughout — this spec MUST NOT enable, arm, or otherwise touch any real-capital trading path, kill-switch, or guardrail file.
- **FR-006**: Already-open positions at the moment of deployment MUST NOT be retroactively re-evaluated under the new threshold — the change applies to newly-screened candidates going forward only.
- **FR-007**: A recalibration protocol MUST be documented (per User Story 3): n≥100 provisional check, n≥1000 validated closure bar at the project's +25%/trade target, same statistical guardrails (outlier removal top-2/top-5, day-count coverage) as every other pocket calibration in this project.

### Key Entities

- **`REGIME_MIN_MEDIAN_PEAK_PCT`**: the pocket's own regime-gate threshold (currently 40.0, raised from 25.0 on 2026-08-24) — the parameter this spec recalibrates.
- **`solana_regime_candidates_log`**: the existing table recording every screened candidate's peak and whether the gate was open/closed for it at decision time — the data source both the 26/08 diagnostic and any future audit read from.
- **`solana_late_bonding_shadow_log`**: the pocket's own closed/open position log — the data source for measuring whether the capture-gap defect reopens under the new threshold.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within a reasonable observation window after deployment (hours to low single-digit days, consistent with the recalibrated threshold's simulated open-time), the pocket produces new closures in `solana_late_bonding_shadow_log`, breaking the current 15h+ silence.
- **SC-002**: Once ≥100 closures exist under the new threshold, a `pocket_entry_sweep`-style pass shows the average captured-return-vs-real-peak gap has not regressed to the pre-24/08 defect's magnitude (captured return averaging far below real peak).
- **SC-003 (provisional recalibration gate)**: at n≥100 closures under the new threshold, the pocket's own statistics (outlier-adjusted, day-count-covered) are reviewed and the threshold is confirmed, tightened, or loosened based on real data — never left uninspected once the sample exists.
- **SC-004 (Closure criterion — same format as specs/010's SC-005)**: This spec is marked "Closed" only once the average realized return across at least 1000 closed trades reaches +25% minimum, computed over trades belonging to the SAME epoch (the period since the last reset/archive triggered by a trading-style-affecting parameter change — this recalibration itself starts a new epoch for this pocket). The n≥100 provisional gate (SC-003) is an earlier sanity check within the same epoch, not a substitute for the 1000-trade closure bar. If a future parameter change starts a newer epoch before 1000 same-epoch closures accumulate, the count restarts there too — expected behavior, not a stall.

## Assumptions

- The curve tracker continues producing in-band candidates at a rate broadly consistent with the 26/08 diagnostic (2980 rows accumulated since 2026-08-25T14:39) — if real candidate supply itself drops sharply and independently of this change, that is a separate, distinguishable condition (see Edge Cases), not evidence the recalibration failed.
- The exact recalibrated value is intentionally left open here and determined in the plan/research phase from the measured open-time trade-off table already gathered (10%→85.9% ... 40%→5.6%) — this spec defines the goal and the guardrail (avoid reopening the capture-gap defect), not the precise number.
- No other pocket parameter (entry filters, trailing stop, exit logic) is in scope — this spec touches only `REGIME_MIN_MEDIAN_PEAK_PCT`.
- Real-capital trading remains fully out of scope; the operator's kill-switch (`/stop`) stays armed exactly as-is throughout, unaffected by this spec.
