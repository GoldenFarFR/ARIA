# Feature Specification: Dip-recovery shadow pocket, v2 -- Base/Robinhood market-cap-bounded dip entry

**Feature Branch**: `012-dip-recovery-v2`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "sa serait interessant de lui brancher base et robinhood comme noeud mais sur des tokens entre 50k et 1 milly post bonding" / clarified: "oui la capitalisation, ducoup c'est sans bonding disons un truc simple comme un filtre de 50k a 1 milly de market cap, 25k de liquidite, et l'objectif c'est d'acheter tout se qui fait -30% sur 24h minimum et de le revendre avec 25% de benef, un truc simpliste pour tester" / follow-up, same session: "ouvre une spec je veut pas que tu me dise on a oublié de faire sa ou si, peut être ajouté le filtre pair age minimum 14 jours, et pense a le relier sur base et robinhood en même temp".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A market-cap-bounded dip-buying pocket runs on both Base and Robinhood at once (Priority: P1) 🎯 MVP

The operator wants a genuinely different shadow pocket from the existing `dip_recovery_shadow` (v1): instead of watching a fixed Base-only watchlist for any -30%/24h dip and stopping out at -5%, this variant hunts specifically for tokens that are past the bonding-curve stage (defined here by a market-cap floor, not a curve-completion flag), sit inside a bounded market-cap band, and are dropped hard enough to be worth a bet on recovery. The pocket must watch BOTH Base and Robinhood Chain in the same pass -- not one chain now and the other "later" -- since the operator explicitly does not want to discover afterward that one chain was forgotten.

**Why this priority**: Without both chains wired from day one, the very first sample this pocket collects is already biased toward whichever chain shipped first, undermining any later comparison between the two -- and re-adding the second chain after data has started accumulating would split the sample exactly the way the project's own "epoch" doctrine (see specs/011) treats as a reset.

**Independent Test**: query the pocket's own log table after a few heartbeat passages -- rows exist tagged `chain='base'` AND rows exist tagged `chain='robinhood'`, both populated within the same observation window, never one chain silent for days while the other runs.

**Acceptance Scenarios**:

1. **Given** the pocket is enabled, **When** a heartbeat cycle runs, **Then** it screens candidates on both Base and Robinhood in that same cycle, not on alternating cycles or only one chain.
2. **Given** a token on either chain shows a 24h price drop of at least 30%, sits inside the market-cap band, clears the liquidity floor, and clears the minimum pair-age filter, **Then** a new shadow position opens for it, tagged with its actual chain.

---

### User Story 2 - Entry is bounded to a plausible, already-de-risked population, never day-zero or micro-cap noise (Priority: P1)

"Sans bonding" per the operator's own clarification means this pocket should never touch a token still on a bonding curve or in its first hours of existence -- it targets tokens that have already survived long enough to carry a real market cap and real liquidity. Three independent filters enforce this: a market-cap band (50k-1M USD), a liquidity floor (25k USD), and (added this session) a minimum pair/pool age of 14 days. All three must hold before a candidate is even considered for the -30%/24h entry signal.

**Why this priority**: A token that clears the -30%/24h signal purely because it is three hours old and violently volatile is a different (and already-covered-elsewhere) risk profile from a token that has traded for weeks at a real market cap and just took a real hit -- conflating the two would make this pocket's data useless for judging the "buy the dip, sell +25%" thesis it exists to test.

**Independent Test**: for every opened position, its logged `entry_market_cap_usd` sits in [50000, 1000000], its logged `entry_liquidity_usd` is >= 25000, and its pair/pool age at entry is >= 14 days -- verifiable directly from the row without re-deriving anything.

**Acceptance Scenarios**:

1. **Given** a token drops 35% in 24h but its pair is only 3 days old, **When** the pocket screens it, **Then** no position opens.
2. **Given** a token drops 35% in 24h, sits at a 2M market cap, **When** the pocket screens it, **Then** no position opens (above the band).
3. **Given** a token drops 35% in 24h, sits at a 200k market cap, 40k liquidity, and its pair is 20 days old, **When** the pocket screens it, **Then** a position opens.

---

### User Story 3 - Exit is a simple fixed take-profit, with a real dedup rule that can actually re-arm (Priority: P1)

Per the operator's own words, the exit is deliberately simple: a fixed +25% take-profit, no stop-loss (unlike v1's -5%), and a 7-day timeout purely as a technical safety net against a position sitting open forever with no exit signal. Separately, and found during this session's own build: because this pocket's discovery mechanism only ever surfaces a chain's current worst-24h performers, a v1-style "wait to observe recovery above threshold" dedup rule can never re-arm once a token stops appearing in that feed -- it would permanently block any future re-entry on that token. The pocket's real dedup rule must instead be "no second position opens for a (contract, chain) while one is already open" -- correct for how this pocket actually sources its candidates.

**Why this priority**: Without a working dedup rule, either the pocket never re-enters a token it should (data starves on that token, defeating the point of the test), or -- if the bug ships unfixed -- entire tokens get silently and permanently excluded from future consideration with no visible signal that anything is wrong.

**Independent Test**: after a position on a given (contract, chain) closes (take-profit or timeout), a fresh qualifying dip on that same (contract, chain) opens a new position. This is exercised directly by a unit test already written this session (`test_discover_rearms_after_recovery_above_threshold`'s originally-failing case, and its corrected replacement once the fix lands).

**Acceptance Scenarios**:

1. **Given** an open position on a token reaches +25%, **When** the next pass checks it, **Then** it closes with `close_reason='take_profit_25pct'`.
2. **Given** an open position sits for 7+ days without reaching +25%, **When** the next pass checks it, **Then** it closes with `close_reason='timeout_max_hold'` regardless of its current PnL (no stop-loss triggers this pocket).
3. **Given** a (contract, chain) pair's prior position has already closed, **When** that same token qualifies for a fresh dip entry later, **Then** a new position opens for it -- dedup blocks re-entry only while a position is genuinely still open, never permanently.

---

### Edge Cases

- What happens if a candidate's market cap can't be resolved (the price/market-cap provider call fails or returns nothing)? The candidate is skipped for this pass, never treated as qualifying by default (never-fabricate dome doctrine) -- retried on a future pass if it still appears in discovery.
- What happens if DexPaprika's own data lags behind the real chain state (its documented free-tier delay of up to 15 seconds)? Given the entry signal is measured over a 24h window, a 15-second lag is immaterial to whether the -30% threshold is crossed -- this is a plan-phase research item to confirm (which plan/tier ARIA's key actually runs on), not expected to change this spec's requirements.
- What happens if the same exit-price read that closes a position on "+25%" is itself a corrupted or implausible quote (the same failure class just found and fixed the same day in `base_momentum_shadow.py`, a false "+707006.8%" reading from a collapsed AMM reserve ratio)? The plan phase must decide whether this pocket needs an equivalent price-sanity guard before a take-profit close is trusted, given the same provider (DexScreener) feeds both pockets' price reads.
- What happens to a position already open when this pocket's parameters change in a future recalibration? Same rule as every other pocket in this project (see specs/011 FR-006): an open position is never retroactively re-judged under a changed parameter.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pocket MUST screen candidates on both Base and Robinhood Chain within the same heartbeat pass -- never only one chain, never one chain added on a later, separate pass.
- **FR-002**: The pocket MUST only consider a candidate for entry if ALL of the following hold simultaneously: 24h price change <= -30%, market cap in [50,000, 1,000,000] USD, liquidity >= 25,000 USD, and pair/pool age >= 14 days.
- **FR-003**: The pocket MUST use a cheap, server-side-filterable discovery call first (liquidity floor, worst-24h-performer ordering) and MUST only spend a paid/bounded market-cap-resolution call on candidates that already cleared that first, free filter -- the project's standing funnel/staging doctrine (CLAUDE.md, "Pense-Système").
- **FR-004**: The pocket MUST close an open position at a fixed +25% take-profit, and MUST NOT apply any stop-loss (explicit operator choice, distinct from v1's -5% stop).
- **FR-005**: The pocket MUST close a still-open position once it has been open for 7 days (168 hours), regardless of its current PnL, as a technical safety net distinct from any trading signal.
- **FR-006**: The pocket's dedup rule MUST prevent a second position opening on the same (contract, chain) while a position for it is already open, and MUST allow a fresh position to open on that same (contract, chain) once the prior one has actually closed -- the recovery-triggered episode-flag approach copied from v1 is REJECTED for this pocket (documented bug: it cannot re-arm given this pocket's own discovery mechanism, see User Story 3).
- **FR-007**: The pocket MUST persist to its own dedicated table, entirely separate from `dip_recovery_shadow` (v1) -- never a shared table, never a parameter bolted onto v1's module (same precedent as every other vN variant in this project).
- **FR-008**: The pocket MUST remain shadow/simulation-only -- it MUST NOT execute any real trade, touch any real-capital wallet, or alter any guardrail file (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`).
- **FR-009**: Every opened and closed position MUST log enough fields to independently verify FR-002 and FR-004/005 after the fact without re-deriving anything: entry price, entry 24h variation, entry market cap, entry liquidity, chain, open/close timestamps, exit price, close reason, realized PnL.
- **FR-010**: The plan phase MUST explicitly decide (accept or reject, not silently omit) each of the following candidates raised this session: (a) an exit-side price-sanity guard against an implausible/corrupted quote (see Edge Cases), (b) whether a cheap legitimacy/safety pre-check is worth adding given this pocket is shadow-only, (c) confirmation of which DexPaprika plan/tier ARIA's key runs on and whether its documented latency matters here, (d) confirmation that the discovery candidate limit per chain per pass is an adequate bound.

### Key Entities

- **Dip-recovery v2 shadow position**: one simulated trade -- contract, chain, pool/pair address, entry price/market cap/liquidity/24h-variation at entry, status (open/closed), exit price, close reason, realized PnL. Fully separate from v1's own position table.
- **Dedup state**: per-(contract, chain), whether a position is currently open -- determines whether a fresh qualifying dip is allowed to open a new position (see FR-006).
- **`dip_recovery_shadow` (v1)**: the existing, structurally different sibling pocket this spec's pocket must never share state, table, or module code with.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within a reasonable observation window after deployment, the pocket's own log table shows opened and/or closed positions tagged `chain='base'` AND positions tagged `chain='robinhood'` -- never one chain silent while the other produces data.
- **SC-002**: Every logged position satisfies FR-002's band/floor/age conditions at its own logged entry values -- spot-checkable directly from the row, no exceptions.
- **SC-003**: A token whose prior position has closed (take-profit or timeout) is observed re-entering the pocket on a later, fresh qualifying dip -- demonstrating the dedup fix (FR-006) actually re-arms in practice, not just in a unit test.
- **SC-004 (provisional recalibration gate, same n>=100 doctrine as specs/011)**: once >=100 closures accumulate, a review states plainly whether the +25%/trade thesis is holding, and whether the 7-day timeout is firing so often that it, rather than the take-profit, is deciding most outcomes (a sign the thesis or the window needs revisiting).
- **SC-005 (Closure criterion, same format as specs/010 and specs/011)**: this spec is marked "Closed" only once the average realized return across at least 1000 closed trades from the SAME epoch (starting at this pocket's first deployment) is measured and reported -- whatever that number turns out to be. Reaching 1000 trades closes the measurement question; it does not by itself mean the thesis was validated.

## Assumptions

- "Post bonding" in the operator's phrasing is implemented as the market-cap floor (50k) plus the new 14-day pair-age filter, not a literal per-launchpad bonding-curve-completion flag -- consistent with the operator's own clarification ("sans bonding... un filtre... de market cap").
- The exact wording/value of any price-sanity guard (FR-010a) is intentionally left to the plan/research phase, not fixed here -- this spec requires the decision to be made and recorded, not a specific multiplier.
- Real-capital trading remains fully out of scope; the operator's kill-switch (`/stop`) stays armed exactly as-is throughout, unaffected by this spec.
- Activating the pocket's gate (`ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED`) in the production `.env` requires the operator's own action -- direct `.env` writes are structurally blocked for this session, confirmed already on an unrelated gate this same day.
