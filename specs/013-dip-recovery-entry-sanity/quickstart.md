# Quickstart: dip_recovery_v2_entry_sanity_guard

## Prerequisites

- `packages/aria-core/.venv` (existing project venv).
- No new environment variables, no new gate — this guard is always active whenever
  `dip_recovery_v2_shadow`'s existing `ARIA_DIP_RECOVERY_V2_SHADOW_ENABLED` gate is on.

## Validation checks

### 1. Test suite

```bash
cd packages/aria-core && .venv/bin/python -m pytest tests/test_dip_recovery_v2_shadow.py tests/test_coherence.py -q -n auto
```

Expect all green, including the new guard tests (sign-disagreement rejection, ordinary
same-direction disagreement still opens, missing/zero DexScreener reading falls back to
pre-existing behavior).

### 2. The real incident, reproduced

The exact numbers that motivated this feature (position id=13, contract
`0x23acfab04106a21af0ae1643b74cfec3c9aac181`, chain=robinhood, 2026-08-26T20:08:51 UTC):
DexPaprika `var_24h_pct = -31.9487081644224`, a synthetic DexScreener `price_change_24h = +29.0`
for the same candidate. Feeding these two values into `_maybe_open_position` must result in NO
position opened and a distinguishable log line — this is the concrete case SC-001 checks.

### 3. Ordinary disagreement still opens (SC-002)

DexPaprika `var_24h_pct = -31.0`, DexScreener `price_change_24h = -22.0` (same direction, ordinary
provider drift) — a position opens exactly as it would have before this feature, with the
existing market-cap/liquidity/pool-age filters deciding as before.

### 4. Missing/zero DexScreener reading never blocks (FR-003)

DexPaprika `var_24h_pct = -35.0`, DexScreener `price_change_24h = 0.0` (provider default when the
field is absent, per research.md Decision 2) — a position opens exactly as it would have before
this feature.

### 5. Zero new network calls (SC-004)

Confirm no new call site is added to `dexpaprika`/`dexscreener` — the guard reads
`snapshot.price_change_24h` from the `PairSnapshot` already returned by the existing
`_resolve_market_cap_and_price` call inside `_maybe_open_position`. A code review of the diff is
sufficient to confirm this (no test needed beyond the existing mocked-call-count pattern already
used elsewhere in this test file, e.g. `_no_real_candle_archive_calls`).

### 6. Rejection is distinguishable (FR-005, SC-003)

Confirm the log line emitted on rejection names this guard specifically (e.g. contains "entry
sanity guard"), distinct from this module's other `logger.info` lines (discovery failure,
candidate advance failure).
