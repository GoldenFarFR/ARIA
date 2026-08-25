# Feature Specification: On-chain discovery vs Chainstack budgets

**Feature Branch**: `005-discovery-budget`

**Created**: 2026-08-25

**Status**: Closed 25/08 -- all 3 tasks resolved (T001 caps calibrated+alerted, T002 measured and deliberately not built, T003 negative cache), no further bug/improvement found. Reopen (new T-number) if a future finding surfaces.

**Input**: Operator-directed chantier (25/08), grown out of the Chainstack RU per-chain calibration conversation: "il faut filtrer la decouverte on peut ? volume minimum, liquidite mini, et tous ?" -> "oui commence un spec sur la decouverte pour respecter les budget".

## Scope

Cross-chain: how ARIA's shadow pockets DISCOVER new pools/tokens (Base, Solana, Robinhood Chain), evaluated specifically against the newly-calibrated per-chain Chainstack RU budgets (`chainstack_ru_budget.py`, 25/08: base=25k, solana=175k, robinhood=400k/day). Distinct from the 002/003/004 specs (which tune each chain's PnL/exit rules) -- this one is about the COST of finding a candidate in the first place, never the trading logic itself.

## Background (verified facts, 25/08, never assumed)

- **Base/Robinhood discovery today**: `dexpaprika.get_trending_pools(...)` (free, own independent budget, zero Chainstack RU) every `ROBINHOOD_CADENCE_SECONDS`/`BASE_CADENCE_SECONDS`. Real logs show liquidity is missing from DexPaprika for almost every freshly-detected pool ("liquidite completee sur X/X pool(s) non indexe(s) par DexPaprika", X often equal to the total) -- a fallback (`_fill_missing_liquidity`) backfills it from another source, never blocking. Separately, a handful of specific Solana pool addresses return a repeated 404 from DexPaprika's own `/pools/{address}` endpoint across MULTIPLE DAYS (23/08->25/08) -- no negative cache, the same dead pool gets re-queried every cycle. Free (no direct cost) but real wasted latency/requests -- worth a fix, not urgent.
- **Robinhood's real cost driver identified same day, already fixed**: the 25/08 keepalive bug (`evm_swap_ws.py`'s `_check_idle_newheads`/`_check_budget_circuit_breaker`) -- the newHeads WS keepalive, billed 1 RU/push, ran unthrottled while zero pools were tracked, on a chain with a ~100ms block time (measured peak 36 213 RU/hour = ~869k/day projected, matching the theoretical worst case documented in the module itself). Confirmed via real data: zero open Robinhood positions between 00h-08h UTC on 25/08, yet ~288k RU consumed in that exact window. Already fixed same day (proactive idle-close + budget-circuit-breaker firing even at zero tracked pools) -- verified live: usage collapsed to ~1.2k RU/hour after 08h.
- **Solana's discovery cost, structurally different, already funnel-optimized**: `pumpfun_curve_tracker.py` batch-polls (`getMultipleAccounts`, 1 credit/call for up to 100 accounts) because no free third-party aggregator indexes a pump.fun bonding curve's progress before migration (verified via web search: DexScreener/DexPaprika only index a pool AFTER it migrates to a real DEX pool, 1-5min lag on top). Banded cadence (60s/20s/10s by progress) is a deliberate funnel, not brute-force. Real measured rate: ~73 RU/day per mint tracked, ~755 mints tracked today -> ~55k/day projected, well under its new 175k cap (~2 740-mint headroom at this rate).
- **Operator's open question, not yet answered**: does subscribing directly to `PoolCreated` (Uniswap v2/v3 Factory)/`Initialize` (v4 PoolManager) on Base/Robinhood beat DexPaprika's polling for LATENCY (detect the instant a pool is created, not after DexPaprika's own indexing delay)? Verified via web search: yes in principle ("polling will always lag, subscribe instead"), and `evm_swap_ws.py` already has the exact `eth_subscribe("logs", ...)` machinery this would reuse -- but no filter can restrict this by volume/liquidity AT SUBSCRIPTION TIME (a freshly created pool has zero liquidity by definition; that data only exists after a following `Mint`/first-liquidity transaction). Real, unanswered risk: subscribing to EVERY pool creation on the whole chain (not just already-tracked pools) is architecturally the same "program-wide firehose" shape that cost Solana 74GB/day before being replaced (21/08 incident, see CLAUDE.md's "Solana sourcing & RPC split") -- the real creation rate on Base/Robinhood has never been measured.

## User Scenarios & Testing

### User Story 1 - Measure the real PoolCreated/Initialize volume before building anything (Priority: P1)

**Why this priority**: CLAUDE.md's own resource-engineering doctrine ("jamais de solution brute... existe-t-il une manire de diviser par 10 le cout") forbids guessing a throughput number -- the exact mistake that cost Solana 74GB/day the first time. Before writing a single line of new subscription code, the real creation rate on Base and Robinhood must be measured empirically.

**Independent Test**: a short-lived, observation-only subscription (`eth_subscribe("logs", {"topics": [PoolCreated/Initialize topic0]})`, no address filter -- the whole factory/PoolManager) run for a bounded window (a few minutes), counting events and estimated RU cost, never opening a position, never replacing the existing discovery path.

**Acceptance Scenarios**:
1. **Given** a short observation window on Base, **When** the real PoolCreated/Initialize rate is measured, **Then** report the real events/hour and the projected RU/day cost, compared honestly against the new 25k/day Base cap.
2. **Given** the same measurement on Robinhood, **When** compared against its 400k/day cap, **Then** report whether headroom exists.
3. **Given** the measured rate is too high relative to the relevant chain's cap, **When** reported, **Then** the direct-subscription idea is shelved (DexPaprika polling stays as-is) rather than built anyway.

### User Story 2 - Fix the DexPaprika repeated-404 waste (Priority: P3, no direct cost but real waste)

**Why this priority**: free (DexPaprika has no RU cost), so lower priority than the budget question -- but a dead pool re-queried across multiple days is a real, fixable waste under the project's "never brute-force" doctrine.

**Independent Test**: identify the caller (`solana_pump_shadow.py`, `get_pool_reserve_usd` fallback) re-querying the same 404'ing pool address across 23/08->25/08, add a negative cache (or stop retrying once a position tied to that pool has been closed for a while).

### User Story 3 - Per-chain Chainstack cap calibration (Priority: P1) -- DONE 25/08

Base=25k, Solana=175k, Robinhood=400k/day (from the old shared 200k x 3), plus a cap-reached Telegram alert (`chainstack_ru_budget.pop_unsent_cap_alerts()`, drained by `shadow_persistent.py`'s new `chainstack_cap_alert_loop`). Shipped, tested (150 tests), deployed live same day. See commit `d5f6fdeb`.

## Success Criteria

- **SC-001**: A real, measured (never assumed) PoolCreated/Initialize event rate for Base and Robinhood, compared against their calibrated daily caps, before any direct-subscription discovery is built.
- **SC-002**: If built, the new discovery path must stay within its chain's cap with real headroom (same "pic x 2" safety-margin doctrine already applied to the cap calibration itself), never a guessed budget.
- **SC-003**: No pool address is ever re-queried indefinitely against a source that has already 404'd it repeatedly (DexPaprika waste, User Story 2).

## Assumptions

- Shadow/paper parameters here are ephemeral scaffolding (project doctrine) -- the per-chain caps and any future discovery-cost parameter are revisited freely as real usage data accumulates.
- Real-money guardrails (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) stay outside this session's autonomous scope, unaffected by this spec (shadow/paper discovery cost only, never a trading-logic change).
