# H-HORIZON -- net return vs holding time. Verdict: INCONCLUSIVE

    hypothesis by: Claude B, 2026-09-02, registered BEFORE the experiment
    run by: Claude A. Zero RU, read-only, seed 20260902.
    B's pre-registered prediction: INCONCLUSIVE, because of low statistical
    power and wide intervals. **Verified, mechanism included.**

## What was measured

Net return (market impact + fees, same function the pockets use) at $25, from
two start populations on `solana_support_bounce`'s archived price paths:
(a) the pocket's real entry instants, (b) instants drawn at random from the same
pools. Identical exclusion rules on both sides. Bootstrap, 10,000 resamples,
both populations resampled independently.

| horizon | n sel | n rnd | gap median | CI95 median | gap trimmed 10% | CI95 trimmed |
|---|---|---|---|---|---|---|
| 5min | 46 | 602 | -0.79 | [-2.43, +0.99] | -1.99 | [-5.70, +1.64] |
| 15min | 77 | 547 | -1.53 | [-5.40, +0.43] | -1.17 | [-4.69, +2.38] |
| 30min | 82 | 601 | -0.04 | [-3.22, +2.40] | +0.70 | [-3.04, +4.96] |
| 1h | 80 | 576 | -2.52 | [-7.39, +2.50] | -2.93 | [-8.29, +2.77] |
| 2h | 64 | 521 | -4.94 | [-16.03, +0.28] | -3.73 | [-13.11, +8.44] |

Every interval contains zero, on both metrics, at every horizon. Per B's
pre-defined verdicts: **INCONCLUSIVE**.

## The result this nearly was, and why it was wrong

The first pass produced CI intervals entirely NEGATIVE on the trimmed metric:
[-9.28, -1.65] at 5min, [-16.89, -4.22] at 1h, [-27.27, -2.74] at 2h. That is a
clean REJECTED, and it would have concluded that ARIA's selection performs
significantly WORSE than random.

It was an artifact of the measurement. The trim removed the top 5 from each
side: 5 of 46 is 10.9% of the selected population, 5 of 602 is 0.8% of the random
one. Amputating a tenth of one distribution's best and a hundredth of the other's
manufactures exactly the negative gap it claims to test.

B's protocol said outlier tests must be applied SYMMETRICALLY, and that trimming
one side alone would fabricate the gap. The error was reading "same number" where
real symmetry is "same proportion". Trimming 10% from both sides reverses the
conclusion completely.

**Both versions are kept here on purpose.** The distance between them is the
finding, not a footnote: a defensible-looking protocol step, applied literally,
produced a strong and entirely false result.

## What survives as observation, with no claim of significance

The selected population's median decays from -3.9% to -8.4% between 5 minutes and
2 hours. The random population's stays flat near -3%. B's monotonic decay
therefore reappears on a THIRD pocket, independent of the two it was found on,
which was his favourable case. But the gap against random is not significant, so
it cannot yet be attributed to selection rather than to the terrain.

## Why the horizons B asked for could not all be reached

| module | candles | median interval | quality |
|---|---|---|---|
| `solana_late_bonding` | 37,658 | 4 s | point samples (flat) |
| `solana_support_bounce` | 38,592 | 300 s | real OHLC |
| `solana_fresh_launch` | 971 | 60 s | real OHLC |
| `dip_recovery_v2` | 1,521 | 21,600 s | real OHLC |

No module has both real candles AND sub-minute granularity. The 10s and 30s
horizons exist only on `solana_late_bonding`, where the sample (6 to 25
positions) supports nothing. Correction to A's earlier advice: H-HORIZON needs
only `close` at T and T+X, never high/low, so the flat candles were in fact
usable for this question -- the recommendation to prefer real OHLC was beside the
point here.

## Reservations carried forward

Reserve for the impact model is the position's ENTRY reserve, applied also to
random starts in the same pool: depth moves, this is an approximation. Three
random draws per pool. Random starts are drawn inside pools ARIA already
selected, so this control measures the value of the chosen MOMENT, never the
choice of pool -- it is B's B0, not his B1.

## Consequence

The question is not settled, and the reason is sample size, not design. That is
itself the argument for the birth detector: it is now demonstrated rather than
assumed that the existing data cannot answer this class of question.
