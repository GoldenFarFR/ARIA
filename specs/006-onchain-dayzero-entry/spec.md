# Feature Specification: On-chain day-zero entry (Base + Robinhood + Solana)

**Feature Branch**: `006-onchain-dayzero-entry`

**Created**: 2026-08-25

**Status**: Closed 25/08 -- Phase 1 (EVM Base+Robinhood) built, tested, deployed, live (24h RU measurement window running). Phase 2 (Solana Raydium+Meteora) measured and decided NOT to build: no per-instruction log filter exists at the Solana RPC layer, so a direct subscription would reproduce the 21/08 programSubscribe-firehose incident this dome already replaced once.

**Input**: Operator-directed (25/08), following `005-discovery-budget`'s conclusion that a direct WS subscription brings zero gain to the CURRENT m5-surge pipeline: "moi je veut passer par le rpc et supprimer dexpaprika et toutes les autres merde, le rpc permet detre plus rapide quand le contrat apparait... on a vue que les budget le permette" -- confirmed scope via clarifying question: full replacement of Base+Robinhood discovery, not a parallel variant. Widened same day: "pour les 3 blockchains je veut le meme systeme par rpc avec les candidat dex pool bien branché" -- same PRINCIPLE (native RPC subscription, never a third-party aggregator) across Base, Robinhood AND Solana, but the IMPLEMENTATION necessarily differs per chain (Solana has no EVM-style topic0/factory logs).

## Scope note -- why Solana isn't a copy-paste of the EVM work

Solana already applies this exact principle for two of its three real sources: `pumpfun_curve_tracker.py` polls the chain directly (`getMultipleAccounts`, no third-party aggregator) for pre-migration bonding curves, and `services/pumpswap_ws.py` already subscribes natively (`PUMPSWAP_PROGRAM_ID`) for post-migration PumpSwap price ticks -- both already "RPC-direct", nothing to replace there. The real gap, verified live (grep across the whole package): **Raydium (classic AMM) and Meteora (DLMM) have zero discovery coverage today** -- neither program is referenced anywhere in the codebase (Raydium only appears via the unrelated, already-deprioritized `launchlab_curve.py`). Before subscribing to either program's pool-creation events, their real creation rate must be measured exactly like Base/Robinhood's factories were in 005 -- Solana's own history (`programSubscribe` at 74GB/day, replaced 21/08) is the concrete reason this dome never subscribes program-wide without measuring first. This spec treats EVM (Base+Robinhood) as Phase 1 (already measured, ready to build) and Solana (Raydium+Meteora discovery) as Phase 2 (measure first, same doctrine).

## Background (verified facts, carried over from 005)

- **Why 005 stopped short**: a pool detected at creation has, by definition, no 5-minute price history and no liquidity yet (liquidity arrives in a SEPARATE `Mint`/first-deposit transaction after pool creation) -- `record_signals`'s `M5_SURGE_THRESHOLD_PCT` gate cannot be satisfied at creation time regardless of detection speed. Wiring WS discovery under the EXISTING m5-surge criterion was therefore a dead end.
- **This spec changes the entry criterion itself**: instead of "wait for a 5-minute price surge on an already-indexed pool", enter as soon as a freshly-created pool crosses a minimum LIQUIDITY floor (day-zero, no surge required) -- the same architectural shift already banked in 005's tasks.md as "worth revisiting... for a day-zero entry variant".
- **Budget already verified, real headroom (005 T002)**: Base ~7.2k RU/day (29% of the 25k/day cap, 5 factories: Uniswap v2/v3/v4 + Aerodrome Slipstream + PancakeSwap V3), Robinhood ~11.1k RU/day (2.8% of the 400k/day cap, Uniswap v2/v3/v4). Both leave real margin even before accounting for this feature's own add_pool traffic (which is bounded by how many pools cross the liquidity floor, a small fraction of raw creation events).
- **`EVMSwapWebSocketFeed` (`evm_swap_ws.py`) already covers v2/v3 pool tracking (add_pool/get_snapshot, reserve_usd from the pool's own Sync event) -- reused verbatim, never duplicated.** v4 pools are NOT yet decoded for DISCOVERY purposes: the module tracks v4 Swap events on pools already known, but never decodes the `Initialize` event itself (currency0/currency1/poolId), which is required to detect a brand-new v4 pool. v4 accounted for 55-80% of raw creation events measured in 005 -- this is the one real new decoder this spec must add.
- **DexPaprika/DexScreener's role today**: `dexpaprika.get_trending_pools()` (discovery + m5 ranking) and `_fill_missing_liquidity` (DexScreener liquidity backfill, ~93% of pools need it per tonight's real measurement) are both replaced for Base+Robinhood discovery. Neither call disappears from the codebase (other pockets/paths may still use them) -- only these two pockets' discovery loop stops calling them.

## User Scenarios & Testing

### User Story 1 - Decode v4 `Initialize` for discovery, not just `Swap` (Priority: P1)

**Why this priority**: this is the one real gap -- v2/v3 pool-address extraction from `PairCreated`/`PoolCreated` is already fully specified (verified live against official/canonical factory ABIs in 005). v4 has no separate pool contract; `Initialize` is the only event carrying `poolId`/`currency0`/`currency1`, and it's the dominant share of creation volume.

**Independent Test**: subscribe to the PoolManager's `Initialize` topic0 alone, decode `poolId` (topics[1]) and `currency0`/`currency1`/`sqrtPriceX96` (data), and confirm the decoded pair matches a manually-verified real pool (cross-check against DexScreener for the same pool address/id).

**Acceptance Scenarios**:
1. **Given** a live `Initialize` event on Base or Robinhood's PoolManager, **When** decoded, **Then** `poolId`/`currency0`/`currency1` are extracted correctly (verified against an independent source), and the pool is immediately added to `EVMSwapWebSocketFeed` for price/reserve tracking.

### User Story 2 - Day-zero entry criterion: liquidity floor, not price surge (Priority: P1)

**Why this priority**: this is the actual strategy change this spec exists for -- without it, User Story 1's faster detection has nowhere useful to plug into.

**Independent Test**: a freshly-detected pool (no price history) crosses `MIN_LIQUIDITY_USD` on its first real Sync/Swap tick -- logged as a new shadow position immediately, without waiting for any 5-minute window.

**Acceptance Scenarios**:
1. **Given** a newly created pool with zero liquidity, **When** its first Sync/Swap event reports `reserve_usd >= MIN_LIQUIDITY_USD`, **Then** a new position is logged with that tick's price as entry, dex_id, and pool_created_at.
2. **Given** a newly created pool that NEVER crosses the liquidity floor within a bounded observation window, **When** the window elapses, **Then** it is dropped (never tracked indefinitely) -- same "no silent cap without logging what's dropped" doctrine as everywhere else in this dome.

### User Story 3 - Replace DexPaprika/DexScreener in Base+Robinhood's discovery loop (Priority: P1)

**Why this priority**: the actual migration -- without it, User Stories 1-2 are built but unused.

**Independent Test**: `shadow_persistent.py`'s Base and Robinhood discovery loops stop calling `dexpaprika.get_trending_pools`/`_fill_missing_liquidity`; the RPC/WS-driven day-zero flow becomes the sole discovery input to `robinhood_pump_shadow.record_signals`/`robinhood_pump_v2_shadow.record_signals`/`base_momentum_shadow`'s equivalent.

**Acceptance Scenarios**:
1. **Given** the migrated loop running live, **When** a real pool crosses the liquidity floor, **Then** it appears in the shadow log within one WS tick (seconds), not the old 120s polling cadence.
2. **Given** the WS connection drops, **When** it reconnects, **Then** discovery resumes automatically (existing reconnect backoff in `EVMSwapWebSocketFeed`) -- no manual intervention, no silent permanent gap.

### Edge Cases

- A pool created moments before the WS reconnects (brief outage window) is missed -- accepted risk, same as any WS-based feed in this dome (evm_swap_ws.py's own exit-tracking has the identical exposure); not worth a DexPaprika fallback given the added complexity for a rare, bounded window.
- v4 pools with a hook contract that reverts/behaves non-standardly on the first swap -- decode failure must fail-open (`available=False`), never crash the discovery loop (same doctrine as every other decoder in `evm_swap_ws.py`).
- Two factories creating the exact same token pair (e.g. both Uniswap v3 and PancakeSwap V3 pools for the same tokens) -- both are legitimate, independent shadow entries, never deduplicated by token pair (same as DexPaprika-era behavior, which also allowed both).

## Requirements

### Functional Requirements

- **FR-001**: A new discovery-side decoder MUST extract a newly-created pool's address (v2/v3-style: `PairCreated`/`PoolCreated`, pool address in event data) or poolId+currencies (v4: `Initialize`, poolId indexed, currencies in data) from a fixed, permanent `eth_subscribe("logs")` filter on the known significant factories per chain (Base: Uniswap v2/v3/v4, Aerodrome Slipstream, PancakeSwap V3; Robinhood: Uniswap v2/v3/v4).
- **FR-002**: Every newly-decoded pool MUST be registered with `EVMSwapWebSocketFeed.add_pool()` immediately (reused, never duplicated) so its first real Sync/Swap tick is captured.
- **FR-003**: A pool crossing `MIN_LIQUIDITY_USD` (reused from `robinhood_pump_shadow.py`, no new constant for the same chain) on its first tick MUST be logged as a new shadow signal, entry price = that tick's price, no m5/price-history check.
- **FR-004**: A pool that never crosses the liquidity floor within a bounded window MUST be dropped and its count logged (never silently forgotten, never tracked forever).
- **FR-005**: Base and Robinhood's discovery loops in `shadow_persistent.py` MUST stop calling `dexpaprika.get_trending_pools`/`_fill_missing_liquidity` once migrated -- the RPC/WS flow is the sole discovery input.
- **FR-006**: RU usage from this new discovery path MUST stay observable in `chainstack_ru_budget.py`'s existing per-chain daily counters (no separate/parallel budget).

### Key Entities

- **On-chain discovery feed**: per-chain, fixed subscription to known factory addresses/topics, decodes creation events into `(pool_address_or_id, dex_id, token0, token1)`.
- **Day-zero candidate**: a decoded pool awaiting its first liquidity-crossing tick, bounded by an observation-window TTL.

## Success Criteria

- **SC-001**: A real pool detected at creation and crossing the liquidity floor is logged as a shadow signal within seconds, not the old 120s DexPaprika cadence -- measured live post-deploy.
- **SC-002**: RU usage for this new path stays within the safety margin already established in 005 (never above ~50% of a chain's daily cap for this subscription alone).
- **SC-003**: No pool is tracked indefinitely without ever crossing the liquidity floor -- a bounded TTL and an honest drop-count exist.

## Assumptions

- Shadow/paper parameters here (liquidity floor, observation window) are ephemeral scaffolding (project doctrine) -- revisited freely as real usage data accumulates, same as every other shadow pocket.
- Real-money guardrails (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) stay outside this session's autonomous scope, unaffected by this spec.
- `robinhood_pump_shadow.py`'s v1 sample stays under active dispute (specs/004) -- this migration changes ITS discovery source too (operator confirmed full replacement, not a parallel v3), so v1's ongoing sample is affected by this change; noted, not a blocker (shadow data is ephemeral scaffolding).
