# Quickstart: Validate the Robinhood day-zero liquidity fix

Run these checks after implementation, in order.

## Prerequisites

- Working tree at repo root, `robinhood_pump_shadow.py` and `robinhood_pump_v2_shadow.py`
  edited, `shadow_persistent.py` updated (out-of-repo, `/opt/aria-data/solana-robinhood-shadow/
  shadow_persistent.py`), service restarted.

## 1. DexPaprika fallback path unchanged (SC-002)

```bash
grep -n "MIN_LIQUIDITY_USD = 4000.0" packages/aria-core/src/aria_core/robinhood_pump_shadow.py
```

Expected: still present, unchanged value — the new day-zero constant is additive, never a
replacement.

## 2. Both filter sites agree per entry mode

```bash
grep -n "MIN_LIQUIDITY_USD_DAY_ZERO\|entry_mode" packages/aria-core/src/aria_core/robinhood_pump_shadow.py packages/aria-core/src/aria_core/robinhood_pump_v2_shadow.py /opt/aria-data/solana-robinhood-shadow/shadow_persistent.py
```

Expected: the new constant and an `entry_mode == "day_zero"` branch appear in both pocket
modules AND in the `shadow_persistent.py` call site that invokes `check_candidates()`.

## 3. Sourcing resumes (SC-001) — run this ~1-24h after deploy

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*), MAX(decided_at) FROM robinhood_pump_regime_candidates_log WHERE decided_at > '2026-08-26T14:00:00';"
```

Expected: non-zero count, `MAX(decided_at)` recent (not stuck at the pre-fix silence point).

## 4. No regression on the pre-23/08 defect (SC-004)

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*) FROM robinhood_pump_shadow_log WHERE entry_price IS NOT NULL AND reserve_usd < 50;"
```

Expected: zero (or explain any hit — a position must never open on a pool with near-zero
reserve, floor or no floor).

## 5. Full test suite, zero regression

```bash
cd packages/aria-core && .venv/bin/python -m pytest tests/test_robinhood_pump_shadow.py tests/test_robinhood_pump_v2_shadow.py tests/test_onchain_pool_discovery.py -q
cd packages/aria-core && .venv/bin/python -m pytest tests/test_coherence.py -q
```

Expected: all green.

## 6. Recalibration readiness check (7+ days after deploy, User Story 3)

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*) FROM robinhood_pump_shadow_log WHERE exit_reason IS NOT NULL AND detected_at > '2026-08-26';"
```

Expected: either ≥100 (run `pocket_entry_sweep` for real, per research.md Decision 4), or
<100 with an explicit note on why (Robinhood Chain's real day-zero volume), never silently
ignored.
