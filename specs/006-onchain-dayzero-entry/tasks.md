# Tasks: On-chain day-zero entry (Base + Robinhood + Solana)

**Input**: spec.md in this directory. No fixed end date -- closes when no further bug/improvement is found (same doctrine as `001-audit-code-sans`/`005-discovery-budget`).

## Phase 1 -- EVM (Base + Robinhood), measured and ready to build

- [ ] T001 [P1] Decode v4 `Initialize` for discovery (poolId/currency0/currency1/sqrtPriceX96 from `topics[1]`+`data`), extend `evm_swap_ws.py` or a new sibling module -- reuses `add_pool`/`get_snapshot`, never duplicated.
- [ ] T002 [P1] v2/v3-style pool-address extraction from `PairCreated`/`PoolCreated` (Uniswap v2/v3 + Aerodrome Slipstream + PancakeSwap V3 on Base, Uniswap v2/v3 on Robinhood) -- signatures/indexed params already verified live in 005, wire the decoder.
- [ ] T003 [P1] Day-zero entry criterion: a newly-decoded pool crosses `MIN_LIQUIDITY_USD` on its first real Sync/Swap tick -> logged as a new shadow signal immediately, no m5/price-history check. Bounded observation-window TTL for pools that never cross the floor (dropped, count logged, never silent).
- [ ] T004 [P1] Migrate `shadow_persistent.py`'s Base+Robinhood discovery loops off `dexpaprika.get_trending_pools`/`_fill_missing_liquidity` -- the RPC/WS day-zero flow becomes the sole discovery input to `robinhood_pump_shadow.record_signals`/`robinhood_pump_v2_shadow.record_signals`/`base_momentum_shadow`'s equivalent.
- [ ] T005 [P2] Full test suite for the new decoder + day-zero criterion + migrated loop, before any deploy.
- [ ] T006 [P2] Deploy: restart `shadow_persistent.py`, verify live (real pool detected within seconds of creation, RU usage tracked in `chainstack_ru_budget.py`'s existing per-chain counters, no new parallel budget).

## Phase 2 -- Solana (Raydium + Meteora discovery), measure first

- [ ] T007 [P2] Measure real Raydium (classic AMM) pool-creation event rate empirically (short observation window, same doctrine as 005's Base/Robinhood measurement) -- never guessed, given the 74GB/day `programSubscribe` incident this dome already lived through.
- [ ] T008 [P2] Measure real Meteora (DLMM) pool-creation event rate empirically, same doctrine.
- [ ] T009 [P2] Decide (never assumed) whether a bounded, address/discriminator-filtered subscription for either program leaves real headroom under Solana's calibrated 175k/day Chainstack cap -- build only if headroom is real, same "pic x2 margin" doctrine as everywhere else.
- [ ] T010 [P3] If built: same day-zero entry criterion as Phase 1, wired into the relevant Solana shadow pocket(s).

## Closure

Mark Status "Closed" in spec.md once every task above is either done or explicitly deprioritized with a reason -- same discipline as 004/005, HANDOFF entry (`docs/HANDOFF_PIPELINE_MOMENTUM.md`) in the same commit as the closing change.
