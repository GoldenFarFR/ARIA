# Feature Specification: Shadow pockets tuning -- Base

**Feature Branch**: `002-shadow-base`

**Created**: 2026-08-25

**Status**: In progress (closes when no further bug/improvement is found, not on a fixed date -- same doctrine as 001-audit-code-sans)

**Input**: Operator directive (25/08): "objectif des pnl de chacun +25% minimum pour 1000 trade sur chacun d'eux" + "vérifie surtout que les résultats des trades achat et vente soit avec un vrai chiffre réel... rug, frais, FOMO" + "construis et branche pour obtenir +25% en moyenne sur 1000 trade sur chacune des blockchains" + "tu as carte blanche pour faire des recherches et des tests"

## Scope

Base-chain shadow pockets: `wallet_copy_shadow.py` (8 tracked wallets), `dip_recovery_shadow.py` (-30%/24h dip signal), `candle_staleness_shadow.py` (observational, never a trading signal itself), `base_momentum_shadow.py` (owned/actively tuned by a separate concurrent session as of 25/08 -- read-only unless a real bug is found, never duplicate its calibration work).

## User Scenarios & Testing

### User Story 1 - Realistic trade simulation (Priority: P1) -- DONE 25/08

Every pocket's PnL must reflect real trading conditions (DEX swap fee, price impact/slippage, rug/dry-pool risk) instead of an optimistic spot-price simulation.

**Acceptance Scenarios**:
1. **Given** a wallet_copy_shadow buy/sell, **When** a spot price is resolved, **Then** the stored price is `risk_guard.simulated_fill_price`/`simulated_exit_price` (fee + impact), never the raw spot.
2. **Given** a wallet_copy_shadow sell whose spot is unresolvable (dry pool by scan time), **When** the position closes, **Then** an honest RPC-only fallback (on-chain WETH Deposit/Withdrawal leg, `chainstack_ru_budget`-metered) is tried before falling back to an honestly-unknown exit price -- never a fabricated one.
3. **Given** a dip_recovery_shadow entry/exit, **When** a candle close is used, **Then** the stored price already includes `risk_guard.DEX_SWAP_FEE_PCT` on both legs.

**Status**: shipped -- commits `9e64c5fc`/`5927c718` (dip_recovery), `85e5ee70` (wallet_copy RPC fallback), all pushed to `origin/main`.

---

### User Story 2 - dip_recovery_shadow: find a signal that survives the outlier test (Priority: P1)

The live gate has never run (0 rows, `ARIA_DIP_RECOVERY_SHADOW_ENABLED` never set) -- a retroactive backtest against already-collected `candle_history` (1H timeframe, 97k candles/391 tokens) replaces weeks of waiting for live volume.

**Why this priority**: without a validated parameter set, activating the live gate would silently accumulate a losing paper track record for weeks before anyone notices -- exactly the wallet-scoring lesson this whole chantier was seeded from.

**Independent Test**: run the retroactive replay harness (`_FrozenDatetime`-based, freezes `datetime.now()` to each candle's own timestamp so `opened_at`/`closed_at`/timeout all reflect the real historical moment) against `candle_history`, compare PnL/winrate across parameter sweeps.

**Acceptance Scenarios**:
1. **Given** the current config (dip -30%/24h, fixed stop -5%), **When** replayed on 72 real historical closures, **Then** the result is honestly reported even if negative (-2.80% raw, -14.45% without top5).
2. **Given** a sweep of stop levels (-5% to -30%) and a no-stop variant, **When** compared, **Then** the sweep must include the outlier-resistant figure (avg without top5), never the raw average alone.
3. **Given** a validated parameter set (if one survives the outlier test on a large-enough sample), **When** proposed, **Then** the live gate activation is proposed to the operator (env-file edit is outside this session's tool permissions) rather than silently assumed.

**Findings so far (25/08, all on real historical replays, never assumed)**:
- Fixed stop -5% to -30%: PnL without top5 stays negative and gets WORSE as the stop widens (-14.45% at -5% down to -19.82% at -30%) -- no fixed-percent stop survives.
- No stop at all (timeout 7d only): 36 closures, +31.33% raw, +19.17% without top2, **+6.94% without top5** -- the only config found so far that survives the outlier test, but the sample stays small and tail risk (a rug sitting for 7 days) is untested.
- Stop at -60% (extreme safety net): 44 closures, +11.34% raw, +0.54% without top2, -10.61% without top5 -- worse than no stop at all on the outlier-resistant figure, though it does catch a few true collapses the no-stop variant leaves open indefinitely.
- Timeframe/lookback sweep (4H/15M candles, 24h/48h/6h lookback windows): launched 25/08, results pending at the time this spec was opened -- read `research.md` for the up-to-date table before citing a figure.

---

### User Story 3 - wallet_copy_shadow: reduce the unknown-exit ratio (Priority: P2)

Two tracked wallets (`gmgn_antpositions` 40%, `serial_frontrunner` 89%) already exceed `CONFIDENCE_MAX_UNKNOWN_EXIT_RATIO=0.20` -- at the current pace they will never pass the confidence bar even at 1000 trades unless this improves.

**Findings (25/08)**: 2 real unknown_exit transactions diagnosed via RPC receipt decoding -- neither matched a standard Uniswap V2/V3/V4/Balancer/Curve/0x/1inch Swap event topic (tracked wallets route through GMGN's own proprietary router, undocumented ABI). One did wrap/unwrap native ETH mid-route -- covered by the new `onchain_weth_leg` fallback (User Story 1). The other had zero decodable price signal at all.

**Independent Test**: `wallet_copy_shadow_position.exit_price_source` column lets any future session measure the real resolution rate per tier (spot / onchain_weth_leg / unknown) without re-diagnosing from scratch.

---

### User Story 4 - candle_staleness_shadow: forward-validation (Priority: P3) -- largely DONE

`forward_validation_report()` links a staleness flag to the real downstream paper-trading outcome. Current verdict (25/08): "no correlation confirmed" (flagged -6.59% vs clean -8.48%, outlier-resistant), but the flagged-observation rate has collapsed since the 21/08 Chainstack/RPC split fix (431 flags/day on 16/08 down to ~0-10/day since 20/08) -- this pocket's own diagnostic already did its job, the underlying cause was fixed elsewhere, and it will likely never accumulate much more linked volume.

## Success Criteria

- **SC-001**: For each Base pocket the operator asked about, PnL average >= +25% on >= 1000 closed positions, measured with the outlier-resistant figure (without top2/top5), never the raw average alone.
- **SC-002**: Every simulated price (entry/exit) reflects real fee + slippage, honestly degrades to "unknown" rather than fabricating a price when unresolvable.
- **SC-003**: Any parameter change proposed for the live gate is backed by a retroactive replay on real historical data (`candle_history`/`shadow.db`), never a guess.

## Assumptions

- `base_momentum_shadow.py` stays out of scope for active tuning (owned by a separate concurrent session as of 25/08, confirmed stopped later same day -- re-check before assuming this boundary still applies to a future session).
- Shadow/paper parameters here are ephemeral scaffolding (project doctrine) -- never gravated as permanent rules, revisited freely as data accumulates.
- Real-money guardrails (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) and the `.env` gate activation stay outside this session's autonomous scope (tooling permission block, not a governance one -- confirmed 25/08).
