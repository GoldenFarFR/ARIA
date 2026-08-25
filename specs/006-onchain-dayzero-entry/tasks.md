# Tasks: On-chain day-zero entry (Base + Robinhood + Solana)

**Input**: spec.md in this directory. No fixed end date -- closes when no further bug/improvement is found (same doctrine as `001-audit-code-sans`/`005-discovery-budget`).

## Phase 1 -- EVM (Base + Robinhood), measured and ready to build

- [x] T001 [P1] DONE 25/08 -- v4 `Initialize` decoded for discovery (poolId from `topics[1]`, currency0/currency1 from `data`), new `services/onchain_pool_discovery.py` (never duplicates evm_swap_ws.py's price decoding -- hands off to `EVMSwapWebSocketFeed.add_pool` immediately). Real bug fixed along the way: `evm_swap_ws.py` always subscribed to Base's own PoolManager address for v4 regardless of chain -- Robinhood's v4 price tracking was silently broken since it was wired (commit `f01660c8`).
- [x] T002 [P1] DONE 25/08 -- v2 (`PairCreated`)/v3-style (`PoolCreated`, covers Uniswap v3 + Aerodrome Slipstream + PancakeSwap V3 via the same/distinct topic0 already verified in 005) pool-address extraction wired in the same module.
- [x] T003 [P1] DONE 25/08 -- day-zero entry: `OnChainPoolDiscoveryFeed.check_candidates()` qualifies a candidate once its first real tick crosses `min_liquidity_usd` (exact for v2-stable via evm_swap_ws's own reserve_usd, WETH-quoted via a live `eth_usd_rate()` conversion, v3/v4 via one bounded, throttled `dexpaprika.get_pool_reserve_usd` fallback call -- never polled, never repeated inside `_RECHECK_INTERVAL_SECONDS`). `_OBSERVATION_WINDOW_SECONDS=600` TTL drops a candidate that never qualifies, `dropped_count` tracks it (never silent).
- [x] T004 [P1] DONE 25/08 -- `robinhood_pump_shadow.record_signals`/`robinhood_pump_v2_shadow.record_signals`/`base_momentum_shadow.record_signals` all gained an `entry_mode` param (default `"m5_surge"`, unchanged; `"day_zero"` bypasses the m5 gate). `shadow_persistent.py`'s Base+Robinhood discovery loops now call `OnChainPoolDiscoveryFeed.check_candidates()` first, falling back to the old DexPaprika path only if the WS feed isn't configured (fail-open, never a single point of failure) -- cadence dropped to 5s in day-zero mode (in-memory check, the old 30s cadence stays for the DexPaprika fallback path).
- [x] T005 [P2] DONE 25/08 -- 11 new tests (`test_onchain_pool_discovery.py`: v2/v3/v4 decode, quote-side detection, liquidity qualification, TTL drop, DexPaprika fallback + its throttle), 1 new regression test for the PoolManager fix, full suite green before deploy.
- [ ] T006 [P2] Deploy: restart `shadow_persistent.py`, verify live (real pool detected within seconds of creation, RU usage tracked in `chainstack_ru_budget.py`'s existing per-chain counters, no new parallel budget). **Explicit operator ask (25/08): let it run 24h to measure real RU consumption before drawing conclusions.**

## Phase 2 -- Solana (Raydium + Meteora discovery), measure first

- [ ] T007 [P2] Measure real Raydium (classic AMM) pool-creation event rate empirically (short observation window, same doctrine as 005's Base/Robinhood measurement) -- never guessed, given the 74GB/day `programSubscribe` incident this dome already lived through.
- [ ] T008 [P2] Measure real Meteora (DLMM) pool-creation event rate empirically, same doctrine.
- [ ] T009 [P2] Decide (never assumed) whether a bounded, address/discriminator-filtered subscription for either program leaves real headroom under Solana's calibrated 175k/day Chainstack cap -- build only if headroom is real, same "pic x2 margin" doctrine as everywhere else.
- [ ] T010 [P3] If built: same day-zero entry criterion as Phase 1, wired into the relevant Solana shadow pocket(s).

## Closure

Mark Status "Closed" in spec.md once every task above is either done or explicitly deprioritized with a reason -- same discipline as 004/005, HANDOFF entry (`docs/HANDOFF_PIPELINE_MOMENTUM.md`) in the same commit as the closing change.
