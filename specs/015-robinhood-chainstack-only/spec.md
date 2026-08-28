# Feature Specification: Robinhood Chainstack-Only Sourcing

**Feature Branch**: `015-robinhood-chainstack-only`

**Created**: 2026-08-28

**Status**: Draft

**Input**: User description: "tout ce qui touche de pret ou de loin vers gecko, dexpaprika doit etre debrancher et cable sur chainstack avec les noeud et le websocket" -- then, scope confirmed explicitly: "non tu supprime toutes reference a gecko ou dexpaprika". Phase 1 of a dome-wide migration (16 modules currently depend on geckoterminal.py and/or dexpaprika.py); Robinhood chosen first because (a) `system_issues` #269: DexPaprika returned 402 Payment Required on ~99.8% of calls dome-wide since 2026-08-28T02:40:27Z (confirmed free-tier quota exhaustion, not code-fixable) and (b) `EVMSwapWebSocketFeed` (services/evm_swap_ws.py, built 24/08) already resolves on-chain price/reserve over a Chainstack websocket and is already wired into this exact pocket's exit-tracking loop -- only the discovery/entry path still depends on the broken provider.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The pocket keeps discovering and pricing new pools when DexPaprika is down (Priority: P1)

The `robinhood_pump` shadow pocket (v1 and v2) must be able to detect a newly created Robinhood Chain pool and resolve its price/liquidity using only Chainstack (on-chain RPC + websocket), so that an outage or quota exhaustion at GeckoTerminal/DexPaprika -- exactly what is happening right now (system_issues #269) -- no longer stops this pocket's sourcing.

**Why this priority**: This is the whole point of the feature. Without it, the pocket stays exactly as broken as it is today.

**Independent Test**: With `DEXPAPRIKA_API_KEY` and GeckoTerminal both made unreachable (or simply left broken, as they are today), run one discovery cycle and confirm at least one real Robinhood Chain pool is detected and priced end-to-end using only Chainstack-sourced data.

**Acceptance Scenarios**:

1. **Given** a new pool has just been created on Robinhood Chain and has at least one on-chain swap, **When** the pocket's discovery cycle runs, **Then** the pool is detected and its price/liquidity are resolved without any call to GeckoTerminal or DexPaprika.
2. **Given** GeckoTerminal and DexPaprika are both returning errors (current real state), **When** the pocket runs its normal cadence, **Then** discovery and pricing continue to work exactly as they would if those providers were healthy.

---

### User Story 2 - A pool detected but not yet priceable is never faked (Priority: P2)

When a pool is detected on-chain before any swap/sync event has been observed (e.g. the very first block after creation), the pocket must skip it for this cycle rather than invent a price -- consistent with this dome's existing "never fabricate a price" doctrine.

**Why this priority**: A fabricated entry price would corrupt every downstream measurement (PnL, the accepted-vs-rejected statistical work already under way this session) exactly the way a stale/corrupted price already has elsewhere in this dome. Second priority because it is a safety property of User Story 1, not a separate capability.

**Independent Test**: Feed the pocket a pool address with zero observed on-chain swap/sync events and confirm it is neither opened as a position nor logged with a fabricated price -- it is simply skipped and retried next cycle.

**Acceptance Scenarios**:

1. **Given** a pool has been detected on-chain but has produced no priceable swap/sync event yet, **When** the discovery cycle evaluates it, **Then** the candidate is skipped this cycle (no position opened, no price logged) and remains eligible for the next cycle.
2. **Given** a pool eventually produces its first priceable event, **When** a later cycle evaluates it, **Then** it is priced and can proceed through the pocket's normal filters.

---

### User Story 3 - Every liquidity/regime numeric threshold keeps meaning what it meant before (Priority: P3)

Some of this pocket's existing numeric floors (e.g. `MIN_LIQUIDITY_USD_DAY_ZERO`) were calibrated against DexPaprika-shaped data. After the provider swap, each such threshold must be explicitly confirmed to still measure the same real-world quantity (e.g. "USD value of the pool's reserves") under the new Chainstack-sourced data -- not silently compared against a differently-shaped number.

**Why this priority**: Lowest priority because it is a correctness check on numbers that already exist, not new behavior -- but it must not be skipped, since a silently mismeasured threshold would misfire exactly as ARIA's history of stale-parameter incidents warns against.

**Independent Test**: For each numeric floor inherited from the DexPaprika-era code, produce one worked example showing the old (DexPaprika) value and the new (Chainstack) value for the same real pool, and confirm they represent the same quantity.

**Acceptance Scenarios**:

1. **Given** a real Robinhood Chain pool with a known DexPaprika-reported liquidity figure, **When** the same pool's liquidity is resolved via Chainstack, **Then** the two figures agree closely enough that the existing threshold still discriminates the same way it did before.

---

### Edge Cases

- What happens when Chainstack's own websocket connection drops mid-cycle? (Existing dome-wide posture for other pockets' websocket feeds -- e.g. reconnect-with-backoff, never block the cycle -- must be reused here, not reinvented.)
- What happens to a pool on a DEX/AMM variant `EVMSwapWebSocketFeed` does not yet cover (only Uniswap v2/v3/v4 and Aerodrome's classic pool are confirmed today)? The pocket must skip it rather than guess, and this must be visible in the pocket's own rejection logging so it doesn't silently look like "no pools found."
- What happens to the `_ROBINHOOD_DISCOVERY_FEED is None` fallback path that today calls DexPaprika directly for whole-market trending discovery? This path must be removed, not left as a silent trap that reactivates if the primary on-chain feed's env var is ever unset.
- What happens to the two GeckoTerminal imports in `robinhood_pump_shadow.py`/`robinhood_pump_v2_shadow.py`? If they are live network calls, they must be replaced the same way as the DexPaprika calls. If they are only type/dataclass imports (e.g. a shared `TrendingPool`), the spec accepts keeping the import as long as it resolves to a neutral shared location rather than implying a live GeckoTerminal dependency -- this must be confirmed, not assumed, during planning.
- **Burst-of-subscriptions risk (found via a peer session's own real incident on the same class of change, 27/08).** DexPaprika's current 402 failure rate (~99.8%, system_issues #269) is today acting as an *accidental* throttle on how many pools this pocket ever finishes qualifying. Once discovery/pricing runs purely on Chainstack and that accidental throttle disappears, the rate of new Chainstack RPC calls / websocket subscriptions this pocket opens could jump sharply. Verified: no equivalent throttle (e.g. a cap on new subscriptions opened per cycle) exists anywhere in this pocket's code or `evm_swap_ws.py` today. This must be sized and added explicitly during planning, not discovered live the way the peer session found it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The pocket MUST detect new Robinhood Chain pools using only on-chain data reached via Chainstack (RPC + websocket) -- no call to GeckoTerminal or DexPaprika anywhere in the discovery path.
- **FR-002**: The pocket MUST resolve a detected pool's price and liquidity (in USD) using only on-chain data reached via Chainstack -- no call to GeckoTerminal or DexPaprika anywhere in the pricing path.
- **FR-003**: The pocket MUST NOT open a position, log a signal, or record any price for a pool that has not yet produced a priceable on-chain event -- it must skip the candidate for the current cycle and remain able to price it in a later cycle once data exists.
- **FR-004**: The removal MUST cover both `robinhood_pump_shadow.py` (v1) and `robinhood_pump_v2_shadow.py` (v2), since both currently depend on the providers being removed.
- **FR-005**: Every existing numeric threshold that was calibrated using DexPaprika-shaped data MUST be explicitly reviewed and confirmed (or re-derived) so it still measures the same real-world quantity under Chainstack-sourced data -- never left unexamined by default.
- **FR-006**: The feature MUST NOT touch `solana_fresh_launch_fast_discovery_shadow.py`/`solana_fresh_launch_ws_exit_shadow.py`'s circular coupling, `dip_recovery_v2_shadow.py`, `base_momentum_shadow.py`, `solana_pump_shadow.py`, `momentum_entry.py`, `paper_trader.py`, or any other of the remaining 14 GeckoTerminal/DexPaprika-dependent modules -- those are later phases of the same dome-wide migration, out of scope here.
- **FR-007**: The feature MUST NOT touch any guardrail file (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`), the kill-switch state, or any real-capital code path -- `robinhood_pump` is shadow/simulation only.
- **FR-008**: After the change, a source-code search for GeckoTerminal/DexPaprika references in the two target files MUST return zero live-call references; any remaining import must be a type-only import to a neutral shared location, and this must be explicitly justified in the implementation, not a leftover default.
- **FR-009**: The pocket MUST cap the rate of new Chainstack RPC calls / websocket subscriptions it opens per discovery cycle, so that removing DexPaprika's current (accidental) throttling effect cannot cause an uncontrolled burst -- sized explicitly during planning, not left unbounded by default.

### Key Entities

- **Robinhood pool candidate**: an on-chain liquidity pool detected via Chainstack, identified by its pool address, with a price/liquidity state that is either "priceable" (at least one observed swap/sync event) or "not yet priceable."
- **Chainstack price/liquidity reading**: a `price_usd`/`reserve_usd` pair resolved from an on-chain event, replacing the equivalent figures the pocket used to receive from DexPaprika/GeckoTerminal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With DexPaprika and GeckoTerminal both unreachable (today's real state), the pocket discovers and prices at least one real new Robinhood Chain pool per normal discovery cycle, using only Chainstack.
- **SC-002**: Zero fabricated prices are ever logged for a pool with no observed on-chain event -- verified by the absence of any position/signal row whose price cannot be traced to a real on-chain swap/sync event.
- **SC-003**: A source-code search for GeckoTerminal/DexPaprika references in `robinhood_pump_shadow.py` and `robinhood_pump_v2_shadow.py` returns zero live-call references.
- **SC-004**: Every numeric threshold inherited from the DexPaprika era has a documented worked example (old value vs. new value, same real pool) confirming it still measures the same quantity.

## Assumptions

- Chainstack already provides the RPC/websocket access this feature needs on Robinhood Chain -- the existing `_ROBINHOOD_EVM_WS_FEED`/`EVMSwapWebSocketFeed` and `_ROBINHOOD_DISCOVERY_FEED` (on-chain day-zero discovery, spec 006) are the building blocks to extend, not replaced wholesale.
- `EVMSwapWebSocketFeed`'s current DEX/AMM coverage (Uniswap v2/v3/v4, Aerodrome's classic pool) is assumed sufficient for the pools this pocket actually needs to price; a pool on an uncovered variant is explicitly out of scope (skipped, per Edge Cases) rather than a blocking requirement to add new AMM support in this feature.
- This is shadow/simulation only -- no real capital, no guardrail file, no kill-switch interaction. The `/stop` kill-switch currently being armed (since 2026-08-25T14:04) is unrelated and out of scope for this feature.
- The two GeckoTerminal imports in the target files may turn out to be type-only (e.g. a shared `TrendingPool` dataclass) rather than live calls -- planning must confirm this per import site rather than assume it.
- Verified live: `_ROBINHOOD_DISCOVERY_FEED` is already configured and active in production (`ARIA_ROBINHOOD_RPC_URL`/`ARIA_ROBINHOOD_RPC_WS` are set), so the DexPaprika `get_trending_pools` fallback path is presumed dead code today rather than the pocket's real discovery mechanism -- planning must confirm this rather than assume the fallback is load-bearing.
- A peer session (obv-ao-screener) working the same class of migration confirmed on 28/08 that its own equivalent price resolution uses a TARGETED on-chain read per already-qualified pool (an `eth_call`/account read, then a websocket subscription for that specific pool) rather than a feed that listens to every Swap/Sync event network-wide -- this pocket's design should follow the same targeted pattern (consistent with `_ROBINHOOD_EVM_WS_FEED`'s existing `add_pool`-style usage for exit tracking), not a network-wide listener.
