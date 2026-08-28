# Data Model: Robinhood Chainstack-Only Sourcing

No new database tables. This feature adds fields to two existing entities and introduces one new in-memory concept (the subscription cap counter); it does not introduce a new persisted entity.

## `EVMSwapSnapshot` (services/evm_swap_ws.py, existing dataclass, extended)

Extended, not replaced -- every existing field keeps its current meaning.

| Field | Type | Status | Notes |
|---|---|---|---|
| `available` | `bool` | existing | unchanged |
| `price_usd` | `float \| None` | existing | unchanged |
| `reserve_usd` | `float \| None` | existing | unchanged; on a v2/Aerodrome-classic pool, now also populated by a direct `eth_call` reserve read when no Sync event has landed yet, not only from a decoded event |
| `tx_hash` | `str \| None` | **new** | the transaction hash of the on-chain event (Sync/Swap) or, for a fresh pool with no event yet, of the `eth_call`'s queried block context is not applicable -- `None` in that case, since a direct read has no single originating transaction; only ever non-null when the value came from a real decoded event |
| `block_number` | `int \| None` | **new** | block number of the same event; `None` under the same eth_call-only condition as `tx_hash` |

**Validation rule**: `tx_hash`/`block_number` are non-null if and only if the snapshot's price/reserve came from a decoded Sync/Swap event (never from a cold `eth_call` reserve read) -- this is exactly what SC-002 checks: a price accepted into a shadow position must be traceable to a real on-chain event, not a point-in-time RPC read of current state. A cold `eth_call` reserve read is honest (never fabricated) but is state, not an event -- the two are kept distinguishable by this null/non-null rule rather than merged.

## `robinhood_pump_shadow_log` / `robinhood_pump_v2_shadow_log` (existing SQLite tables, extended)

Two new columns, added via the existing hot-`ALTER TABLE` migration pattern already used dome-wide (`PRAGMA table_info` check + conditional `ALTER TABLE`, per `test_the_two_active_pockets_share_the_same_exit_guardrails`'s own "hot ALTER" requirement -- this pocket already has that pattern wired, confirmed in research.md's call-chain trace).

| Column | Type | Status | Notes |
|---|---|---|---|
| `entry_tx_hash` | `TEXT` | **new** | copied from `EVMSwapSnapshot.tx_hash` at the moment `record_signals` logs the row; `NULL` if entry priced from a cold reserve read rather than a live event (never fabricated) |
| `entry_block_number` | `INTEGER` | **new** | copied from `EVMSwapSnapshot.block_number` at the same moment |

**Validation rule**: same as `EVMSwapSnapshot` above -- these columns being non-null is exactly SC-002's mechanical check (`SELECT COUNT(*) FROM robinhood_pump_shadow_log WHERE entry_tx_hash IS NULL` should be zero, or every non-zero row explained by a documented cold-read case).

## Subscription cap counter (in-memory, `OnChainPoolDiscoveryFeed`, no persistence)

Not a database entity -- a plain in-process counter (or reuse of the existing `self._candidates` dict's length) checked against the new cap constant before opening a new websocket subscription.

| Concept | Type | Notes |
|---|---|---|
| Concurrently tracked pools | `int` (derived, `len(self._candidates)`) | compared against the new cap (150, see research.md) before `add_pool` is called for a newly-notified pool |
| Cap-exceeded behavior | `skip/defer`, never burst | a raw notification arriving while at capacity is dropped for THIS pass and logged (never silently discarded, never queued unbounded) -- it can still qualify on a later independent notification of the same pool if one arrives before its own observation window would have expired anyway |

No schema, no migration -- this is a runtime guard, not a stored fact.
