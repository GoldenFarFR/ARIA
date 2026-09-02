# Hypothesis register -- trajectory research (on-chain replay)

> Operator rule (02/09): *"garder un registre des hypothèses mortes ... dans trois
> mois, on ne revient pas accidentellement à une hypothèse déjà réfutée."*
> One row per hypothesis. A rejected hypothesis stays here with its reason; it is
> never deleted, and never re-tested under the same definition.

**Test protocol (frozen 02/09, applied identically to every hypothesis):**
1. Split the metric into terciles at each instant T; compare the forward return
   of the low tercile vs the high tercile.
2. Remove the 2 best points, then the 5 best points, from each group. The gap
   must keep its **sign** -- an effect that lives only in the extremes is an
   artefact (CLAUDE.md, 22/08 incident).
3. Stratify by drawdown and re-check inside each stratum: an effect that vanishes
   at comparable drawdown was mean reversion, not information.
4. A hypothesis discovered in-sample is **EXPLORATORY** until it survives the same
   calculation, same thresholds, zero adjustment, on tokens it has never seen.

**Datasets used so far** (both Robinhood Chain, reconstructed from raw Swap events
since `Initialize`, `onchain_replay_raw`):
- **AI** -- winner, +232M$ FDV, 151,193 swaps, 30.0 days, 106 snapshots at 6h step.
- **MEOW** -- dead, -91%, 3,162 swaps, 23.9h, 123 snapshots at 10min step.

Two tokens. The 229 points are NOT independent (strong temporal autocorrelation
within each trajectory); the effective n is closer to 2. Nothing below is proof.

| id | hypothesis | definition (frozen) | status | evidence | date |
|---|---|---|---|---|---|
| H1 | Position in sequence: low runup from prior low = better entry | `runup_from_low` tercile low vs high | **REJECTED** (this form) | Spectacular on extremes (+1271%, +1270% at 72h). Without top 5: AI high tercile **beats** low (+86% vs +58%); MEOW median already favoured high. Pure outlier artefact. Possible reformulation: runup relative to drawdown amplitude, not raw. | 2026-09-02 |
| H2 | Seller exhaustion at retest: same low, less sell volume than the prior low | not yet computed (needs prior-low matching) | **UNTESTED** | -- | -- |
| H3 | Participant widening: distinct wallets/unit time grow before a durable rise | `active_wallets`, `new_wallets` terciles | **REDUNDANT** | Same direction as H4/H5/swaps on both tokens -- all four measure global activity. One information, not three confirmations. Folded into H9. | 2026-09-02 |
| H4 | Liquidity holds: winners keep liquidity during the fall, losers see it leave | `liquidity_events` terciles | **REDUNDANT** | Moves with H3; needs signed delta (add vs remove) to mean anything, which the raw count does not carry. | 2026-09-02 |
| H5 | Net buy flow during the base: accumulation vs distribution | `flow0`/`flow1` terciles | **REDUNDANT / UNSTABLE** | flow0 unstable on AI; flow1 moves with activity. Needs buy/sell labelling by token side (available, not yet applied). | 2026-09-02 |
| H6 | Narrative/activity divergence: on-chain activity holds while social attention dies = distribution | needs dated social series | **UNTESTABLE** (data) | Signal cascade covers 244 Base tokens, 2 Robinhood, none of ours. No retroactive social trace exists. | 2026-09-02 |
| H6-bis | On-chain proxy of H6: renewal_ratio (new / active wallets) collapsing = dead narrative | `renewal_ratio` terciles | **NEEDS NORMALIZATION** | Decays structurally with age (1.00 at birth, ~0.4 week 1, ~0.2 week 4 on AI). Raw value measures youth, not narrative health. Must be compared to the token's own age-trend before testing. | 2026-09-02 |
| H7 | Social shock signature: a discontinuity in renewal marks an external event; its persistence separates catalyst from ephemeral pump | not yet computed (needs change-point on renewal) | **UNTESTED** | -- | -- |
| H8 | Entrainment wallets: some wallets' buys are followed by a measurable inflow within N seconds | not yet computed (needs pairwise lag analysis on `tx_sender`) | **UNTESTED** | Fully on-chain. Complementary to `wallet_copy_shadow` (which tests copying known wallets; H8 discovers who is followed and by how many). Blocks at 0.101s give the resolution. | 2026-09-02 |
| H9 | Calm precedes the rise: at comparable drawdown, low activity at T = better forward return | `swaps` tercile low vs high, **stratified by drawdown tercile** | **EXPLORATORY, in-sample, UNVALIDATED** | Survived on 6/6 strata across both tokens after removing top 3 (AI deep dd: calm +295% / agitated -22%; MEOW deep dd: calm -14% / agitated -27%). On MEOW it is a *smaller loss*, not a gain -- reads as an entry-**timing** signal, not a token-**selection** signal. Emerged from the data, so it carries the same suspicion as any clustering output. Definition to freeze before out-of-sample: `activity_relative(T) = activity over past window / token's usual activity at that life stage`. | 2026-09-02 |

## Open methodological findings (not hypotheses -- constraints)

- **The screener's candles cannot define `T_explosion`**: they begin after the token
  is already moving, so the pre-explosion base is structurally absent (base before
  T = 0.0h in 15/16 X/Y combinations). Only a reconstruction from `Initialize`
  contains the phase that matters. The screener is a diagnostic tool, not a truth
  source for this research.
- **Backfill cost follows activity, not duration**: AI = 90,948 RU for one token.
  A 40-token deep backfill is not viable; a birth-window triage pass ("does this
  token even have a pre-explosion phase?") must gate the deep pass.
- **Two regimes, not one pattern** (operator sketches, 02/09): slow accumulation
  (weeks of base) vs fast stampede (minutes). The same metric can carry opposite
  meaning across them -- a copy-trading cohort is a risk in regime 1 and the
  signal itself in regime 2. Any template must be extracted per regime, and a
  single aggregate score is ruled out.
- **Selection vs timing are two questions.** H9 answers "when", not "which". The
  target architecture keeps them separate: structure/template decides acceptable,
  relative calm decides entry.
