# Data Model: Dip-recovery shadow pocket, v2

## New table: `dip_recovery_v2_shadow`

One row per simulated position. Own table, fully separate from v1's `dip_recovery_shadow`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `contract` | TEXT NOT NULL | token address |
| `chain` | TEXT NOT NULL | `"base"` or `"robinhood"` (FR-001) |
| `pool_address` | TEXT | resolved pair address, from DexScreener |
| `symbol` | TEXT | best-effort, may be `NULL` |
| `status` | TEXT NOT NULL DEFAULT 'open' | `'open'` \| `'closed'` |
| `entry_price` | REAL NOT NULL | after realistic fill fee (`risk_guard.DEX_SWAP_FEE_PCT`) |
| `entry_var_24h_pct` | REAL | the dip signal at entry (<= -30.0, FR-002) |
| `entry_market_cap_usd` | REAL | must be in [50000, 1000000] at entry (FR-002) |
| `entry_liquidity_usd` | REAL | must be >= 25000 at entry (FR-002) |
| `entry_pool_age_days` | REAL | new column (Decision 3) — must be >= 14 at entry (FR-002) |
| `opened_at` | TEXT NOT NULL | ISO8601 UTC |
| `closed_at` | TEXT | ISO8601 UTC, `NULL` while open |
| `exit_price` | REAL | after realistic exit fee |
| `close_reason` | TEXT | `'take_profit_25pct'` \| `'timeout_max_hold'` (FR-004/005) — never `'stop_loss'`, this pocket has none |
| `pnl_pct` | REAL | realized, fee-adjusted both legs |

Index: `(contract, chain, status)` — supports the dedup check (Decision 1) and per-chain open-
position scans directly.

## Removed from the draft: `dip_recovery_v2_shadow_episode_state`

The draft's episode-state table is dropped entirely (Decision 1) — dedup is a direct query
against `dip_recovery_v2_shadow` (`SELECT 1 WHERE contract=? AND chain=? AND status='open'`), no
separate state table needed. Nothing has been deployed or committed yet, so this is a clean
removal, not a migration.

## State transitions

- `open` → `closed` via `close_reason='take_profit_25pct'`: `pnl_pct >= TAKE_PROFIT_PCT` (25.0) on
  a price accepted by the Decision 2 sanity guard.
- `open` → `closed` via `close_reason='timeout_max_hold'`: age >= `MAX_HOLD_HOURS` (168.0),
  regardless of PnL — no stop-loss transition exists for this pocket (FR-004).
- No transition re-opens a closed row. A fresh dip on the same (contract, chain) after closure
  inserts a NEW row (dedup only blocks while a row for that pair is `status='open'`).

## `shadow_candle_archive` (existing shared table, new consumer)

Per research.md Decision 7 (operator-added mid-implementation): every open/opened position also
writes to the dome's existing shared `shadow_candle_archive` table (owned by
`shadow_candle_archive.py`, its own separate SQLite file at `shadow_db_path()`) via
`store_candles(module="dip_recovery_v2", position_id=<id>, pool_address=..., chain=..., phase=
"before"|"after", candles=...)`. No schema change to that table — this pocket is simply a new
`module` value in its existing `module` discriminator column, same pattern as
`robinhood_pump`/`base_momentum`/every other wired shadow module.

## Relationship to `dip_recovery_shadow` (v1)

None at the data level — different table, different module, no foreign key, no shared episode
state. The only relationship is documentary (this spec/plan referencing v1's own known bug as the
reason v2's dedup design differs).
