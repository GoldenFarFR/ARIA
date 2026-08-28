---

description: "Task list for feature 015-robinhood-chainstack-only"
---

# Tasks: Robinhood Chainstack-Only Sourcing

**Input**: Design documents from `/specs/015-robinhood-chainstack-only/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md (all present, mutually consistent)

**Tests**: Included -- this dome's standing norm requires every capability shipped with a test wired into CI (CLAUDE.md "Testability" permanent norm).

**Organization**: Tasks follow spec.md's own User Story priorities (P1 discover+price via Chainstack only, P2 never fabricate a price for a not-yet-priceable pool, P3 threshold quantity review). P2's skip/defer logic is a safety property that must land alongside P1's wiring, not a separately-deployable increment -- both phases are needed before this feature can be considered shippable, but P2's tasks are still isolated below so its acceptance criteria stay individually checkable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3, per spec.md
- All file paths are relative to `packages/aria-core/` unless marked as the standalone process file outside the repo.

---

## Phase 1: Setup

**Purpose**: No new project/dependency needed -- existing monorepo, existing test tooling. This phase only confirms the ground truth research.md already established, so later phases build on verified line numbers rather than assumed ones.

- [X] T001 Re-verify against the live files that the call sites named in research.md still exist at the same locations (`services/onchain_pool_discovery.py`'s 3 DexPaprika calls, `robinhood_pump_shadow.py`'s `_snapshot_with_fallback` cascade and `evaluate_open_signals`'s missing `ws_feed` argument) -- if any file changed since this session's audit, note the drift before proceeding rather than trusting the line numbers blindly.

**Checkpoint**: Ground truth confirmed current.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The targeted on-chain resolver every later phase depends on. No user story work can begin until this exists and is tested.

**⚠️ CRITICAL**: Phases 3-5 all call into this resolver -- it must be correct and tested first.

- [X] T002 In `src/aria_core/services/evm_swap_ws.py`, add `tx_hash: str | None = None` and `block_number: int | None = None` fields to `EVMSwapSnapshot` (near line 217), populated from the already-received event payload at zero extra RPC cost when a snapshot is built from a decoded Sync/Swap event; both fields MUST stay `None` when a snapshot is built from a cold on-chain read (T003) rather than a decoded event -- this null/non-null distinction is the mechanism data-model.md defines for SC-002, do not populate them from anything other than a real decoded event.
- [X] T003 In `src/aria_core/services/evm_swap_ws.py`, add a targeted on-chain reserve/price resolver function (e.g. `resolve_pool_reserve_and_price(pool_address, *, chain, dex_id) -> EVMSwapSnapshot`) that performs a direct `eth_call` against the pool contract: `getReserves()` for Uniswap v2 / Aerodrome's classic pool (mirroring the existing `Sync` event decoder's math, same file), `slot0()` + active liquidity for v3/v4 (mirroring the existing `Swap` event decoder's math). Returns `available=False` (never a fabricated value) if either the reserve read OR the price computation fails -- **both must succeed together or the result is not priceable**, per the operator's explicit two-state requirement. `tx_hash`/`block_number` stay `None` on this path (T002's rule).
- [X] T004 In `src/aria_core/services/evm_swap_ws.py`, add a plain ERC-20 symbol resolver (`resolve_token_symbol(token_address, *, chain) -> str | None`) via a direct `symbol()` eth_call, `None` on any failure (never fabricated) -- this replaces DexPaprika's `_resolve_base_token`'s cosmetic role only, never gates qualification.
- [X] T005 [P] Add `chainstack_ru_budget` throughput coordination to T003/T004's new `eth_call`s -- reuse the existing shared budget point (`services/chainstack_ru_budget.py`), never a second independent throttle, per CLAUDE.md's "single throughput-coordination point" norm.
- [X] T006 [P] `tests/test_evm_swap_ws.py`: test T002's new fields (non-null only when built from a decoded event, always null from a cold read), test T003's resolver returns `available=False` on a partial read (reserve resolves, price doesn't, or vice versa -- both directions must be tested), test T003's resolver on a full successful read (both reserve and price resolve), test T004's symbol resolver (success and failure-to-None cases).

**Checkpoint**: Resolver exists, is tested, and its all-or-nothing availability contract is proven -- Phases 3-5 can now wire it in.

---

## Phase 3: User Story 1 - Discover and price with only Chainstack (P1) 🎯 MVP

**Goal**: The pocket detects and prices new Robinhood Chain pools using only Chainstack, with zero live calls to GeckoTerminal/DexPaprika anywhere in the discovery/pricing path.

**Independent Test**: With DexPaprika still unreachable (today's real state, system_issues #269), run one discovery cycle and confirm at least one real pool is detected and priced end-to-end using only the T003 resolver / the existing websocket -- per quickstart.md's SC-001 scenario.

- [X] T007 [US1] In `src/aria_core/services/onchain_pool_discovery.py`'s `OnChainPoolDiscoveryFeed.check_candidates` (~line 347-450), replace the `dexpaprika.get_pool_reserve_usd(key, network=self.chain)` call (line ~421) with T003's resolver -- called ONLY when the websocket snapshot (`self._ws_feed.get_snapshot(key)`, tried first, unchanged) has no `reserve_usd` yet. If T003 returns `available=False`, the candidate is NOT qualified this cycle (see Phase 4's skip/defer semantics, wired in together with this task since the current code's `continue` on missing reserve already implements the right shape -- verify it still does after this change).
- [X] T008 [US1] In the same function, remove the `dexpaprika._get_json(f"/networks/{self.chain}/pools/{key}", ...)` price-detail fallback (line ~426) -- price now comes exclusively from either the websocket snapshot or T003's resolver (both already resolved together as a pair per T003's all-or-nothing contract), never a separate price-only lookup.
- [X] T009 [P] [US1] In the same function, replace `dexpaprika._resolve_base_token(self.chain, key)` (line ~443, symbol resolution for already-qualified candidates only) with T004's resolver.
- [X] T010 [US1] Remove the `dexpaprika` import from `services/onchain_pool_discovery.py` entirely once T007-T009 land (confirm no remaining reference with `grep -n "dexpaprika" services/onchain_pool_discovery.py`).
- [X] T011 [US1] In `src/aria_core/robinhood_pump_shadow.py`'s `_snapshot_with_fallback` (~line 1056-1143): remove the GeckoTerminal reserve-only backfill (the `client.get_pool_snapshot` call inside the `if reserve_usd is None:` branch, ~line 1117) and the DexPaprika reserve-only backfill that follows it (~line 1133) -- both currently fire when DexScreener reports `liquidity_unknown`; replace with a call to T003's resolver in the same position (still only attempted after DexScreener's own price is already accepted, same funnel order: ws_feed -> DexScreener -> T003 resolver as the new backfill).
- [X] T012 [US1] In the same function, remove the final `return await client.get_pool_snapshot(pool_address, network=chain)` GeckoTerminal-sole-fallback (~line 1143, the case where `token_address` is falsy) -- replace with `return PoolSnapshot(available=False, ...)` (never fabricate a price when there is no way to even attempt a DexScreener lookup) unless T003's resolver can still be attempted with only a pool address (verify: T003 is keyed on `pool_address`, not `token_address`, so it CAN still run here -- use it rather than returning unavailable outright).
- [X] T013 [US1] Remove the `GeckoTerminalClient`/`geckoterminal_client` runtime parameter threading that is no longer needed once T011-T012 land -- but KEEP the `TrendingPool`/`PoolSnapshot`/`OHLCVResult` type imports from `aria_core.services.geckoterminal` (confirmed type-only elsewhere in the file, per research.md) -- do not remove type imports that are still load-bearing for function signatures.
- [X] T014 [US1] In `src/aria_core/robinhood_pump_shadow.py`'s `evaluate_open_signals` (~line 1199-1201), pass `ws_feed=ws_feed` to its `_snapshot_with_fallback` call (currently missing, unlike `advance_exit_simulation`'s equivalent call at ~line 1384-1387) -- add a `ws_feed: EVMSwapWebSocketFeed | None = None` parameter to `evaluate_open_signals`'s own signature and thread it through from the caller in `/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py` (same `_ROBINHOOD_EVM_WS_FEED` instance already used for `advance_exit_simulation`).
- [X] T015 [US1] In `/opt/aria-data/solana-robinhood-shadow/shadow_persistent.py`'s `robinhood_discovery_loop`, remove the `dexpaprika.get_trending_pools("robinhood", limit=25)` fallback branch entirely (the `else` when `_ROBINHOOD_DISCOVERY_FEED is None`) -- confirmed dead in prod today since the feed IS configured (T001's re-verification should reconfirm this before deleting), but per FR-001 the branch itself must go, not just stay unreachable. If `_ROBINHOOD_DISCOVERY_FEED` is somehow `None` after this change, the loop should skip the cycle and log, never silently fall back to the removed provider.
- [X] T016 [P] [US1] `tests/test_onchain_pool_discovery.py`: test T007-T010 -- a candidate with no websocket snapshot and a successful T003 resolver read qualifies; a candidate with no websocket snapshot and a failed/partial T003 read does NOT qualify (this cycle) and remains eligible later; zero `dexpaprika` references anywhere in the module after the change (source-text assertion, same pattern as this dome's existing call-site-wiring tests, e.g. `test_the_two_active_pockets_share_the_same_exit_guardrails` in `test_coherence.py`).
- [X] T017 [P] [US1] `tests/test_robinhood_pump_shadow.py`: test T011-T012's new `_snapshot_with_fallback` cascade (ws_feed -> DexScreener -> T003 resolver -> unavailable, never GeckoTerminal/DexPaprika), test T014's `evaluate_open_signals` now receiving and using `ws_feed`.
- [X] T018 [US1] Source-text verification per SC-003: `grep -rn "dexpaprika\.\|geckoterminal_client\.\|GeckoTerminalClient\." src/aria_core/robinhood_pump_shadow.py src/aria_core/robinhood_pump_v2_shadow.py src/aria_core/services/onchain_pool_discovery.py` returns zero live-call matches (type-only imports acceptable, confirm none remain unused after T013).

**Checkpoint**: User Story 1 independently testable and complete -- discovery/pricing no longer touches GeckoTerminal or DexPaprika.

---

## Phase 4: User Story 2 - Never fabricate a price for a not-yet-priceable pool (P2)

**Goal**: A pool detected but not yet priceable is skipped, never faked, and the subscription/RPC rate this enables is explicitly capped.

**Independent Test**: Feed the pocket a pool address with zero observed on-chain swap/sync events and a failing/partial T003 read; confirm it is neither opened as a position nor logged with a fabricated price -- it is simply skipped and retried next cycle. Per quickstart.md's FR-009 scenario, confirm the cap's skip/defer path is reachable and logged under load.

- [X] T019 [US2] In `src/aria_core/services/onchain_pool_discovery.py`, add an explicit `not_yet_priceable` outcome (as a named state, not an implicit `continue` -- e.g. a counted stat alongside the existing `expired_keys` tracking) for the case T007/T008 leave unresolved: no websocket snapshot AND T003's resolver returned `available=False`. This must be distinguishable in logs/counters from "expired past the observation window" and from "qualified" -- the operator's explicit ask is a real two-state machine, not a bare `continue`.
- [X] T020 [US2] Add the subscription/concurrency cap: before `add_pool`/subscribing a newly-notified candidate, check the count of currently-tracked candidates (`len(self._candidates)` or equivalent) against a new `MAX_CONCURRENT_TRACKED_POOLS = 150` constant (documented per research.md as an initial guardrail derived from the measured ~66 concurrent baseline, 2.3x headroom -- NOT a tuned optimum, comment must say so explicitly). Over the cap: skip/defer (log the drop, never silently discard, never queue unbounded) -- a dropped notification for a pool that re-notifies before its own window would have expired anyway can still be picked up later.
- [X] T021 [P] [US2] `tests/test_onchain_pool_discovery.py`: test T019's `not_yet_priceable` state is reached (not silently conflated with expiry or qualification) when both the websocket and T003's resolver fail/partial-fail; test T020's cap -- at capacity, a new notification is skipped and logged, never opens a subscription, and the skip is observable (counter or log line) per quickstart.md's FR-009 scenario.
- [X] T022 [US2] Confirm (source-text or behavioral test) that no code path introduced in Phase 3 can qualify a candidate from a PARTIAL T003 read (reserve resolved, price not, or vice versa) -- this is the operator's explicit "no subtler fabricated-price failure class" requirement; add a regression test if T006 didn't already cover this exact partial-read-at-the-qualification-call-site case (T006 tested the resolver in isolation, this task confirms the caller in `check_candidates` actually respects `available=False` on a partial read).

**Checkpoint**: User Story 2 independently testable and complete -- the skip/defer state machine and the subscription cap are both explicit and tested, closing the "subtler fabrication" and "unbounded burst" risks named in spec.md's Edge Cases.

---

## Phase 5: User Story 3 - Threshold quantity review (P3)

**Goal**: Confirm `MIN_LIQUIDITY_USD`/`MIN_LIQUIDITY_USD_DAY_ZERO` still measure the same real-world quantity under Chainstack-sourced data, without changing either value inside this feature.

**Independent Test**: Produce one worked example (old DexPaprika-era reading vs. new Chainstack reading, same real pool) per quickstart.md's SC-004 scenario.

- [X] T023 [US3] Pick one real Robinhood Chain pool with a `reserve_usd` value already logged (pre-this-feature) in `fresh_launch_pretrade_gate_log` or `robinhood_pump_shadow_log`, and re-resolve its current `reserve_usd` via T003's resolver (or the live websocket if it still ticks). Document both figures side by side.
- [X] T024 [US3] Compare the two figures: if they agree within normal indexing-latency noise (a few percent, consistent with DexPaprika's documented ~15s free-tier staleness), document this as confirmation that `MIN_LIQUIDITY_USD`/`MIN_LIQUIDITY_USD_DAY_ZERO` keep their current values unchanged (per the operator's explicit instruction: quantity match first, value change never bundled into this feature). If they diverge beyond latency noise, STOP -- do not change the threshold value; write up the finding and flag it to the operator as a separate follow-up decision.
- [X] T025 [P] [US3] Record T023/T024's worked example and verdict in `docs/HANDOFF_PIPELINE_MOMENTUM.md` (new entry, `[CODE]` status until deployed) so the quantity-match finding is not lost to this session's context alone.

**Checkpoint**: User Story 3 independently testable and complete -- SC-004's gate is satisfied by evidence, not assertion.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full-suite validation, deployment, and the standing recalibration follow-up the operator explicitly asked not to bundle into this feature's Definition of Done.

- [X] T026 Full targeted suite: `.venv/bin/python -m pytest tests/test_robinhood_pump_shadow.py tests/test_robinhood_pump_v2_shadow.py tests/test_evm_swap_ws.py tests/test_onchain_pool_discovery.py tests/test_coherence.py -q` -- confirm green, confirm `test_robinhood_pump_v2_shadow.py` needed no new DexPaprika/GeckoTerminal-removal-specific tests (per research.md's finding that v2 has no independent fix).
- [X] T027 Full suite: `.venv/bin/python -m pytest -q -n auto` (verify no other pytest process already running first, per this session's standing pytest-xdist norm) -- confirm no regression dome-wide.
- [X] T028 [P] Update `docs/pocket-parameters.json` (`python -m aria_core.pocket_parameters --write`) if T020's new `MAX_CONCURRENT_TRACKED_POOLS` constant or any other new UPPER_CASE module-level constant should be tracked in the registry -- verify the diff is exactly the expected new entries, nothing else drifted.
- [ ] T029 Deploy per this dome's standing Zero-Permission Policy: commit (dual co-author, Claude + GoldenFarFR), push, `./vanguard/deploy.sh`, verify the REAL served commit via health check against `git rev-parse main` (never the script's own text output), update `.claude/last-deployed-ref`.
- [ ] T030 Complete `docs/HANDOFF_PIPELINE_MOMENTUM.md`'s entry for this feature (extending T025's if already started) with the final `[DEPLOYE]` status, the real commit hash, and a one-line pointer back to `specs/015-robinhood-chainstack-only/` for anyone wanting the full trace.
- [ ] T031 **Explicit follow-up, NOT part of this feature's Definition of Done** (operator's own instruction): once DexPaprika is fully removed and qualification is genuinely restored in production (T029 deployed, `qualified_this_cycle` no longer stuck at 0), re-measure real concurrent-subscription demand the same way research.md measured the ~66 baseline (`raw_notifications_seen` delta over a real time window from `shadow_persistent.log`). Compare against T020's 150 cap. If the real post-fix demand is materially different, open a SEPARATE follow-up (new backlog item or a small dedicated spec, per this dome's routeur) to recalibrate the cap value -- do not fold that recalibration into this feature's own commit.

**Checkpoint**: Feature complete, deployed, verified, and its own follow-up correctly deferred rather than silently expanding scope.

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** -- no dependencies, run first.
- **Phase 2 (Foundational)** -- depends on Phase 1's re-verification; BLOCKS all of Phase 3/4/5 (every later task calls into T002-T004's resolver).
- **Phase 3 (US1)** -- depends on Phase 2. T007→T008→T009→T010 are sequential (same function, same file, ordered edits); T011→T012→T013 sequential (same function); T014, T015 can run in parallel with T007-T013 (different functions/files) once Phase 2 is done; T016/T017 (tests) after their respective implementation tasks; T018 last (verifies the whole story).
- **Phase 4 (US2)** -- depends on Phase 2 and on T007/T008 (US1) existing, since T019's `not_yet_priceable` state lives in the same function US1 modified. Should land together with US1 before either is considered done, per this feature's own framing (P2 is a safety property of P1, not independently deployable).
- **Phase 5 (US3)** -- depends on Phase 2/3 (needs the new resolver live to produce a real comparison reading) but is otherwise independent of US2; can run in parallel with Phase 4.
- **Phase 6 (Polish)** -- depends on Phases 3, 4, and 5 all complete.

## Parallel Execution Examples

- Phase 2: T005 can run alongside T002-T004 (different concern, same file -- coordinate on the file, not blocking logically) once the resolver's shape is agreed; T006 after T002-T004 land.
- Phase 3: T009 (symbol resolver wiring) and T014 (evaluate_open_signals ws_feed) and T015 (remove the shadow_persistent.py fallback) can all run in parallel with the T007→T008 sequential edit chain -- different functions/files, no shared state.
- Phase 5: T023-T025 can run fully in parallel with Phase 4, once Phase 3's resolver is live.

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1) + the safety-critical parts of Phase 4 (T019/T020)** -- this is the minimum that actually fixes the production outage (system_issues #269) without reintroducing a subtler fabrication risk. Phase 5 (US3, threshold review) and the remainder of Phase 6 (deployment polish, HANDOFF) can follow immediately after in the same session, but US1+US2's safety property together are the real Definition of Done the operator described ("the problem is the DexPaprika fallback in the qualification path" + "never let an incomplete eth_call fall through as priceable").
