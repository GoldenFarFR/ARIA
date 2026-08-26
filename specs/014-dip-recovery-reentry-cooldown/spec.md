# Feature Specification: dip_recovery_v2_reentry_cooldown

**Feature Branch**: `014-dip-recovery-reentry-cooldown`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "dip_recovery_v2_reentry_cooldown -- add a minimum cooldown period after a position closes before dip_recovery_v2_shadow.py will reopen a position on the SAME (contract, chain), to prevent rapid buy-sell-rebuy cycling on a token whose entry signal (DexPaprika's var_24h_pct) is unstable."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A token cannot be immediately rebought right after its own position closes (Priority: P1)

The pocket closes a position (take-profit or timeout). Moments later, the same candidate reappears in discovery with a qualifying signal, at essentially the same price the previous position just closed at. The pocket must not open a brand-new position on this token again until a minimum cooldown period has elapsed since the previous close — otherwise the same token can be bought, sold, and rebought in short succession on what may be measurement noise rather than a genuine new dip.

**Why this priority**: This is the exact incident that surfaced the gap — position id=15 closed at +40% on take-profit, and position id=16 opened on the SAME contract 15 minutes later at essentially the same price, with DexPaprika's 24h-change reading swinging ~8 points despite the price barely moving. Each such cycle inflates this pocket's own win-rate statistics without representing an independently earned trading opportunity.

**Independent Test**: Close a position on a contract, then feed the pocket a new qualifying candidate for the same contract moments later; confirm no new position opens. Feed the same candidate again after the cooldown window has elapsed; confirm a position can open normally.

**Acceptance Scenarios**:

1. **Given** a position on (contract, chain) closed less than the cooldown duration ago, **When** a new qualifying candidate for the same (contract, chain) is evaluated, **Then** no new position opens.
2. **Given** a position on (contract, chain) closed more than the cooldown duration ago, **When** a new qualifying candidate for the same (contract, chain) is evaluated, **Then** the existing entry filters apply exactly as before and a position can open.
3. **Given** a contract that has never had a position before, **When** it qualifies as a candidate, **Then** the cooldown has no effect (nothing to cool down from).

---

### User Story 2 - The cooldown does not treat every close reason identically without an explicit decision (Priority: P2)

A timeout close (7 days elapsed, thesis never played out) reflects a different market situation than a take-profit close occurring shortly after entry on a token whose price has barely moved. The feature must make an explicit, documented choice about whether the cooldown applies uniformly to every close reason or is scoped to the reason(s) that actually motivated it, rather than silently assuming symmetry.

**Why this priority**: Applying an unexamined blanket rule risks either being too permissive (missing the real risk) or too restrictive (blocking a token from ever being reconsidered after a stale timeout, where the underlying concern does not actually apply). Getting this right the first time avoids a second recalibration pass.

**Independent Test**: Review the plan/research decision on which close reasons trigger the cooldown, and confirm the implementation matches that decision exactly (not "cooldown after any close" by unexamined default).

**Acceptance Scenarios**:

1. **Given** the close-reason scoping decided in the implementation plan, **When** a position closes for a reason the plan says should NOT trigger the cooldown, **Then** a same-contract candidate can reopen immediately (subject to all other existing filters), unaffected by this feature.

---

### User Story 3 - Every candidate blocked by this cooldown is visible, not silently absorbed (Priority: P3)

When the cooldown blocks a candidate, the reason is distinguishable from every other rejection reason this pocket already produces (market cap, liquidity, pool age, the specs/013 entry-sanity guard), so this pocket's own re-entry-blocked rate can be reviewed later and the cooldown duration recalibrated once enough real examples accumulate.

**Why this priority**: Same standing doctrine already applied in specs/013 — a filter whose rejections are invisible can only be trusted, never checked, and this cooldown's exact duration is a placeholder pending real data (see Assumptions).

**Independent Test**: Trigger a cooldown-blocked candidate and confirm the rejection reason recorded names this specific guard, distinct from every other rejection reason.

**Acceptance Scenarios**:

1. **Given** a candidate blocked specifically by the reentry cooldown, **When** the rejection is recorded, **Then** the recorded reason is distinguishable from a market-cap, liquidity, pool-age, or entry-sanity-guard rejection.

### Edge Cases

- What happens when a contract has MULTIPLE past closed positions (not just one)? The cooldown must be measured from the MOST RECENT close, not an older one — an old, long-resolved close must never block a candidate that a much more recent close already would have cleared.
- What happens exactly at the cooldown boundary (a candidate arriving at precisely the cooldown duration, neither clearly inside nor outside the window)? The exact boundary behavior (inclusive/exclusive) must be defined explicitly in the plan, not left ambiguous.
- What happens if the same (contract, chain) has a currently OPEN position AND a past closed one within the cooldown window? The existing open-position dedup (specs/012) already refuses this candidate first — the cooldown is a second, independent check that only matters once no position is open, and must not change the existing open-position dedup's own behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pocket MUST track, for each (contract, chain), how much time has elapsed since that pair's most recent closed position.
- **FR-002**: The pocket MUST refuse to open a new position on a (contract, chain) whose most recent close (of a close reason the plan designates as cooldown-triggering — see FR-006) occurred less than a documented cooldown duration ago.
- **FR-003**: The pocket MUST NOT apply the cooldown to a (contract, chain) that has never had a closed position.
- **FR-004**: The cooldown duration MUST be an explicit, named, documented value — not silently hardcoded without a stated rationale — and MUST be recalibratable later as real data accumulates (same posture as this pocket's other guard thresholds).
- **FR-005**: Every position refused specifically by this cooldown MUST be recorded with a reason distinguishable from this pocket's other rejection reasons.
- **FR-006**: The implementation plan MUST make an explicit decision (not a silent default) about which close reason(s) trigger the cooldown, and the code MUST match that decision exactly.
- **FR-007**: This feature MUST NOT change the existing open-position dedup (specs/012) or the existing entry-sanity guard (specs/013) — it is an additional, independent check.
- **FR-008**: This feature MUST NOT touch real capital, the kill-switch, or any wallet-guard file — pure shadow/simulation, same as every other change to this pocket.

### Key Entities

- **Most recent close per (contract, chain)**: the timestamp and close reason of the latest closed position for a given contract/chain pair, used to compute whether the cooldown is still in effect.
- **Cooldown rejection reason**: an identifier attached to a refused candidate distinguishing this guard's refusal from every other refusal reason in this pocket.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given the exact incident inputs recorded this session (a position closed 15 minutes before a new qualifying candidate on the same contract), the pocket does not open a new position.
- **SC-002**: Given a qualifying candidate on a contract whose most recent close happened well before the cooldown duration, a position opens exactly as it would have before this feature.
- **SC-003**: 100% of positions refused by this cooldown carry a distinguishable, reviewable reason — none are silently dropped with no trace.
- **SC-004**: Zero additional network calls are introduced by this feature (the most-recent-close lookup is a query against this pocket's own existing table).

## Assumptions

- The exact cooldown duration is a placeholder value pending real data, same posture as `ENTRY_SANITY_MIN_CONFLICT_PCT`/`EXIT_PRICE_SANITY_MULTIPLE` elsewhere in this pocket — the implementation plan documents a specific starting value with an explicit rationale, to be recalibrated once enough real candidates accumulate.
- Whether the cooldown applies to every close reason or only some is a plan-level decision, not pre-decided in this spec (see User Story 2).
- This pocket's own aggregate win-rate/PnL reporting (the Telegram "Cumul"/"Debit 1h" lines) is NOT retroactively corrected by this feature to distinguish "N independent tokens" from "N trade cycles, some clustered on the same token" — flagged as a candidate future improvement, out of scope here to avoid scope creep into this specific fix.
- No real capital, guardrail file, or kill-switch is affected — this is a pure shadow/simulation pocket, unchanged by this feature's governance classification (full spec-kit, per CLAUDE.md's routeur table, since it modifies a strategy/entry-filter parameter).
