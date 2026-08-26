# Quickstart: Validate the Solana regime-gate recalibration

Run these checks after implementation, in order.

## Prerequisites

- Working tree at repo root, `solana_late_bonding_shadow.py` edited
  (`REGIME_MIN_MEDIAN_PEAK_PCT` 40.0 → 30.0), out-of-repo `shadow_persistent.py` process
  restarted (`systemctl restart aria-shadow-persistent.service`).

## 1. Constant actually changed (SC-001 precondition)

```bash
grep -n "^REGIME_MIN_MEDIAN_PEAK_PCT" packages/aria-core/src/aria_core/solana_late_bonding_shadow.py
```

Expected: `REGIME_MIN_MEDIAN_PEAK_PCT: float | None = 30.0`.

## 2. Epoch archived at deployment (per SC-004 / tasks.md epoch-archive step)

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'solana_late_bonding_shadow_log_archive_reset_%';"
```

Expected: a new `_archive_reset_<deploy-date>` table alongside the existing 2026-08-25 one,
and the live `solana_late_bonding_shadow_log` empty (or absent, recreated fresh on next write).

## 3. Sourcing resumes (SC-001) — run this a few hours after deploy

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*), MAX(last_checked_at) FROM solana_late_bonding_shadow_log WHERE exit_reason IS NOT NULL;"
```

Expected: non-zero count, `MAX(last_checked_at)` recent — breaking the pre-deployment 15h+
silence.

## 4. Gate open-time roughly consistent with the recalibrated value

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*) FROM solana_regime_candidates_log WHERE decided_at > '<deploy-timestamp>';"
```

Cross-check against `regime_state()`'s own live reads (or a repeat of research.md's rolling-
median calculation over post-deploy rows) — open-time should trend toward research.md's
measured ~11.5% for threshold=30.0, not the pre-deploy ~4.0%.

## 5. No regression on already-open positions (FR-006)

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*) FROM solana_late_bonding_shadow_log_archive_reset_20260825 WHERE exit_reason IS NULL;"
```

Any position that was still open under the OLD threshold at deploy time must be found here
(archived, not silently dropped or re-judged) — expected: consistent with whatever was open at
archive time, never retroactively altered.

## 6. Full test suite, zero regression

```bash
cd packages/aria-core && .venv/bin/python -m pytest tests/test_solana_late_bonding_shadow.py -q
cd packages/aria-core && .venv/bin/python -m pytest tests/test_coherence.py -q
```

Expected: all green, including `test_pocket_parameter_registry_matches_the_code` after
`docs/pocket-parameters.json` is regenerated.

## 7. Recalibration readiness check (User Story 3, n≥100 gate)

```bash
sqlite3 -readonly /opt/aria-data/shadow.db "SELECT COUNT(*) FROM solana_late_bonding_shadow_log WHERE exit_reason IS NOT NULL;"
```

Expected: either ≥100 (run the capture-gap re-measurement from research.md Decision 2 for
real), or <100 with an explicit note on why (market simply cold), never silently ignored.
