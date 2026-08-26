# Quickstart: Validate the dip-recovery v2 shadow pocket

Run these checks after implementation, in order.

## Prerequisites

- Working tree at repo root, `dip_recovery_v2_shadow.py` updated (Decision 1/2/3 fixes applied),
  `heartbeat.py` wiring confirmed, deployed via the normal blue-green `deploy.sh` cycle.
- Gate `ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED` set to `true` in prod `.env` — operator's own action,
  not something this session can do directly (Bash writes to `.env` are structurally blocked).

## 1. Full test suite for the pocket, zero regression

```bash
cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py -q
cd packages/aria-core && .venv/bin/python -m pytest tests/test_coherence.py -q
```

Expected: all green, including the previously-red `test_discover_rearms_after_recovery_above_threshold`
(now passing under the Decision 1 open-position dedup) and `test_pocket_parameter_registry_matches_the_code`
after `docs/pocket-parameters.json` is regenerated.

## 2. Dual-chain wiring confirmed (FR-001, SC-001)

```bash
sqlite3 -readonly /opt/aria-data/aria.db "SELECT chain, COUNT(*) FROM dip_recovery_v2_shadow GROUP BY chain;"
```

Run a few heartbeat passages after the gate is enabled. Expected: both `base` and `robinhood`
rows appear within the same observation window — never one chain silent while the other runs.

## 3. Entry filters hold on every logged row (FR-002, SC-002)

```bash
sqlite3 -readonly /opt/aria-data/aria.db "SELECT COUNT(*) FROM dip_recovery_v2_shadow WHERE entry_market_cap_usd NOT BETWEEN 50000 AND 1000000 OR entry_liquidity_usd < 25000 OR entry_pool_age_days < 14 OR entry_var_24h_pct > -30.0;"
```

Expected: `0`. Any non-zero count means a filter was bypassed somewhere and needs investigating
before trusting anything else in the table.

## 4. Dedup actually re-arms (FR-006, SC-003)

```bash
sqlite3 -readonly /opt/aria-data/aria.db "SELECT contract, chain, COUNT(*) FROM dip_recovery_v2_shadow GROUP BY contract, chain HAVING COUNT(*) > 1;"
```

Expected: over time, some (contract, chain) pairs show 2+ rows (a fresh dip re-entering after a
prior close) — evidence the Decision 1 fix works in practice, not just in the unit test. Absence
of any repeats after a long observation window would itself be worth a second look (either the
population rarely re-dips, or the dedup is still stuck).

## 5. Exit reasons match the spec's own rules (FR-004/005)

```bash
sqlite3 -readonly /opt/aria-data/aria.db "SELECT close_reason, COUNT(*) FROM dip_recovery_v2_shadow WHERE status='closed' GROUP BY close_reason;"
```

Expected: only `take_profit_25pct` and `timeout_max_hold` ever appear — never a stop-loss reason
(this pocket has none by design).

## 6. Provisional recalibration gate (SC-004, n≥100)

```bash
sqlite3 -readonly /opt/aria-data/aria.db "SELECT COUNT(*) FROM dip_recovery_v2_shadow WHERE status='closed';"
```

Expected: once this reaches ≥100, review per SC-004 — state plainly whether the +25%/trade thesis
holds and whether `timeout_max_hold` is deciding most outcomes (a sign the take-profit is rarely
reached). Below 100: note the sample is insufficient rather than drawing any conclusion.
