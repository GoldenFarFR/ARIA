# Feature Specification: dip_recovery_v2_entry_sanity_guard

**Feature Branch**: `013-dip-recovery-entry-sanity`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "dip_recovery_v2_entry_sanity_guard -- add a cross-provider plausibility guard on the ENTRY var_24h_pct signal for dip_recovery_v2_shadow.py, symmetric to the existing EXIT_PRICE_SANITY_MULTIPLE guard on the exit side (specs/012-dip-recovery-v2, Decision 2)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A dip candidate whose two providers disagree on direction never opens a position (Priority: P1)

The pocket discovers a candidate via DexPaprika's worst-24h-performers feed reporting a large negative 24h change. Before opening a shadow position, the pocket also has DexScreener's own independently-computed 24h change for the same candidate (already fetched for market cap/liquidity, no extra cost). When the two providers flatly disagree on the DIRECTION of the move (one says a large dip, the other says a large gain), the pocket must treat this as an implausible/unreliable signal and refuse the entry, the same way the exit side already refuses an implausible take-profit price.

**Why this priority**: This is the exact incident that surfaced the gap — a position opened on a -31.9% DexPaprika reading that, minutes later, both DexScreener and DexPaprika's own live lookup agreed was actually a +29% reading for the same token. An entry signal with zero independent cross-check can open positions on bad data with no way to tell after the fact whether the reasoning was ever sound.

**Independent Test**: Feed the pocket a synthetic candidate where DexPaprika reports -31.9% and the paired DexScreener snapshot reports +29% for the same contract; confirm no position is opened and no fabricated data is stored.

**Acceptance Scenarios**:

1. **Given** a candidate whose DexPaprika 24h reading is a large negative value and whose DexScreener 24h reading is a large positive value for the same contract, **When** the pocket evaluates the candidate, **Then** no shadow position is opened for it this cycle.
2. **Given** a candidate whose DexPaprika and DexScreener 24h readings agree in direction (both negative, roughly comparable magnitude), **When** the pocket evaluates the candidate, **Then** the existing market-cap/liquidity filters apply exactly as before and a position can still open.

---

### User Story 2 - A missing or unavailable cross-check reading never blocks or fabricates a signal (Priority: P2)

DexScreener's `price_change_24h` field can be legitimately absent or zero-as-default for a freshly-indexed pool. The pocket must never treat an unavailable reading as either "confirms the dip" or "proves the dip is fake" — it must fall back to today's existing behavior (rely on the market-cap/liquidity filters alone) rather than silently rejecting every candidate whenever this one extra field happens to be missing.

**Why this priority**: Same never-fabricate doctrine already applied to every other field in this pocket (e.g. an unknown pool age is never treated as "old enough"). Without this, the new guard could accidentally become a second, stricter, undocumented liquidity filter that quietly reduces this pocket's own discovery volume.

**Independent Test**: Feed the pocket a candidate whose DexScreener snapshot has no usable 24h reading; confirm the entry decision is identical to what it would have been before this feature existed.

**Acceptance Scenarios**:

1. **Given** a DexScreener snapshot with no usable 24h reading for a candidate that otherwise clears every existing filter, **When** the pocket evaluates the candidate, **Then** the position opens exactly as it would have before this guard existed.

---

### User Story 3 - Every refusal from this guard is visible, not silently absorbed (Priority: P3)

When this guard blocks a candidate, the reason is distinguishable from every other rejection reason (market cap out of band, liquidity too low, pool too young) so a future review of this pocket's own discovery funnel can tell how often this specific guard fires, and recalibrate its threshold once enough real examples accumulate.

**Why this priority**: This dome's own standing doctrine ("a filter whose rejects are invisible can only be trusted, never checked") already governs every other shadow pocket's rejection logging. Without this, nobody can tell later whether the threshold chosen here is too strict, too loose, or exactly right.

**Independent Test**: Trigger a rejection via this guard and confirm the rejection reason recorded (log line or rejection registry, per the plan's design) names this specific guard, distinct from every other rejection reason this pocket already produces.

**Acceptance Scenarios**:

1. **Given** a candidate rejected specifically by this guard, **When** the rejection is recorded, **Then** the recorded reason is distinguishable from a market-cap, liquidity, or pool-age rejection.

### Edge Cases

- What happens when DexScreener's 24h reading is a small, ordinary disagreement with DexPaprika's (e.g. -31% vs -22%, same direction, different magnitude) rather than a full sign flip? The guard's threshold must not reject an entry purely because two independent providers never agree to the decimal point — some tolerance for same-direction disagreement is expected and must be defined explicitly (see plan/research).
- What happens on the very first candidate evaluated after a fresh deploy, before any real-world example of a disagreement has ever been observed? The guard must still apply its documented threshold from day one — it is not something that "warms up," since a bad reading can occur on the very first cycle.
- What happens if DexPaprika's own reading is itself borderline (e.g. exactly -30.0%, the entry threshold) and DexScreener's reading is moderately different but same-direction? The guard should not be stricter than necessary near the boundary — this is exactly the "ordinary disagreement" case above, not a sign flip.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pocket MUST compare DexPaprika's entry-time 24h change reading against DexScreener's own 24h change reading for the same candidate before opening a position, using data already fetched by the existing market-cap/liquidity resolution call (no additional network call).
- **FR-002**: The pocket MUST refuse to open a position when the two readings disagree on direction beyond a documented, explicit tolerance (exact rule defined in the implementation plan's research phase, not guessed at spec time).
- **FR-003**: The pocket MUST NOT refuse an otherwise-qualifying candidate solely because DexScreener's 24h reading is unavailable/unusable — an unavailable reading falls back to pre-existing behavior (market-cap/liquidity filters alone decide).
- **FR-004**: The pocket MUST NOT fabricate a DexScreener 24h reading when the provider does not supply one.
- **FR-005**: Every position refused specifically by this guard MUST be recorded with a reason distinguishable from this pocket's other rejection reasons (market cap, liquidity, pool age).
- **FR-006**: This guard MUST apply only to real position-opening decisions in `dip_recovery_v2_shadow` (shadow/simulation only) — it MUST NOT touch real capital, the kill-switch, or any wallet-guard file.
- **FR-007**: The existing exit-side guard (`EXIT_PRICE_SANITY_MULTIPLE`) and this new entry-side guard MUST remain independent, separately named and separately testable — this feature does not merge or replace the exit guard.

### Key Entities

- **Entry candidate cross-check**: the pairing of DexPaprika's `var_24h_pct` (discovery-time) and DexScreener's `price_change_24h` (resolution-time) for the same contract/chain, evaluated once per candidate before a position is opened.
- **Rejection reason**: an identifier attached to a refused candidate distinguishing this guard's refusal from every other existing refusal reason in this pocket.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given the exact incident inputs recorded this session (DexPaprika -31.9%, DexScreener +29% for the same candidate), the pocket does not open a position.
- **SC-002**: A candidate with ordinary, same-direction disagreement between the two providers (e.g. both negative, within the documented tolerance) opens a position exactly as it would have before this feature, with zero change in this pocket's overall entry volume attributable to false rejections.
- **SC-003**: 100% of positions refused by this guard carry a distinguishable, reviewable reason — none are silently dropped with no trace.
- **SC-004**: Zero additional network calls are introduced by this feature (the cross-check reuses data already fetched today).

## Assumptions

- DexScreener's `price_change_24h` field, already parsed by the existing client (`services/dexscreener.py`), is a reliable enough independent read for this cross-check purpose — it is not itself guarded against the same class of provider glitch this feature is built to catch, but a second provider agreeing/disagreeing is still meaningfully more information than trusting one provider alone (same reasoning already accepted for the exit-side guard, which also trusts a single provider's plausibility rather than a second source).
- The two readings are not sampled at the exact same instant (DexPaprika's discovery call happens before DexScreener's resolution call) — some natural drift between them is expected and is not itself evidence of a bad reading; the exact tolerance for this drift is a research-phase decision, not decided in this spec.
- This guard is entry-side only. It does not change discovery volume reporting, does not touch the exit-side guard, and does not add a new external API dependency.
- No real capital, guardrail file, or kill-switch is affected — this is a pure shadow/simulation pocket, unchanged by this feature's governance classification (full spec-kit, per CLAUDE.md's routeur table, since it modifies a strategy/entry-filter parameter).
