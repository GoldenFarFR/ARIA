# Data Model: Recalibrate the Solana late-bonding shadow pocket's regime gate

No schema changes. This feature touches one existing constant and reads two existing tables;
no new tables, columns, or migrations.

## Entities (existing, unchanged)

### `REGIME_MIN_MEDIAN_PEAK_PCT` (constant, `solana_late_bonding_shadow.py`)
- **Type**: `float`
- **Current value**: 40.0 (raised from 25.0 on 2026-08-24)
- **New value**: 30.0 (this feature)
- **Semantics**: the regime gate opens (new candidates may be accepted) when the rolling
  median peak of the last `REGIME_WINDOW=30` screened candidates is ≥ this value. `None`
  disarms the gate entirely (always reads as open) — not used here, this feature keeps the
  gate armed.

### `solana_regime_candidates_log` (table, read-only for this feature)
- Existing schema, unchanged. Fields relevant to this feature: `decided_at` (chronological
  order), `entry_price`, `peak_price` (used to compute the per-candidate peak percentage this
  feature's research reads).
- This feature does not write to this table — `regime_state()`/`advance_regime_candidates_from_pools()`
  (unchanged) continue populating it exactly as today.

### `solana_late_bonding_shadow_log` (table, archived at deployment — same pattern as specs/010)
- Existing schema, unchanged. This feature archives the current live table to
  `solana_late_bonding_shadow_log_archive_reset_<deploy-date>` at deployment time (epoch
  boundary, per spec.md SC-004) — the table is recreated fresh (existing
  `CREATE TABLE IF NOT EXISTS` behavior) on the next write, identical to specs/010's T014b.

## State transitions

None new. The regime gate's open/closed state transition logic (`regime_state()`) is
unchanged — only the threshold value it compares against changes.
