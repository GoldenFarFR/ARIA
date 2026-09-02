# FINDING-0004 -- Candle archive quality is per-pocket, and one pocket is blind

    question: are archived candles real OHLC extremes, or point samples?
    It decides whether FINDING-0003's "59% of stops never causally fire" is a
    severe defect or an artifact of the measuring instrument.
    answer: point samples on ONE pocket, real extremes on the four others.

## Measured, whole table

| module | candles | flat (high == low) | share flat |
|---|---|---|---|
| `solana_late_bonding` | 37,658 | 37,658 | **100.0%** |
| `solana_support_bounce` | 38,592 | 4,569 | 11.8% |
| `solana_support_bounce_v2` | 21,861 | 2,811 | 12.9% |
| `dip_recovery_v2` | 1,521 | 57 | 3.7% |
| `solana_fresh_launch` | 971 | 38 | 3.9% |

Not a limitation of `shadow_candle_archive` itself: four modules feed it real
OHLC. One does not. `solana_late_bonding`'s single archiving call builds candles
as `open=high=low=close=price` from the curve's progress history, which destroys
any notion of an extreme at the source.

## Consequences

**FINDING-0003's 59% is WITHDRAWN.** A dip that is not in the data cannot be
detected. That figure said more about the instrument than about the code.

**FINDING-0003's price-bias figures survive, as an UPPER bound.** Reconstructing
the causal peak from point samples UNDER-estimates it (intra-interval highs are
missed), and the bias measured is the gap between the code's peak and that
under-estimated one. So the medians of 0.00 and 0.24 points are ceilings; the
real bias is lower. Conservative, therefore usable.

**The blindness is bounded in time, unlike solana_pump's.** Median interval
between two samples is 4 seconds, minimum 1, and 94% of intervals are under 10
seconds. A dip that touches and fully reverses inside 4 seconds is possible on a
memecoin, not systematic. solana_pump's blind window was two hours.

## Unlocks H-HORIZON (B's hypothesis, same day)

B observed that return degrades monotonically with holding time on both chains
(Solana -15.5% at 15min to -16.6% at 2h; Robinhood -60.2% to -72.9%), and asked
whether the curve crosses zero below 15 minutes -- our floor, because
`forward_pct_m15` is the shortest column anyone recorded.

The 15-minute floor is a limitation of those COLUMNS, not of the data. At a
4-second median granularity the candles support one minute, thirty seconds, ten
seconds. The hypothesis is testable at zero RU.

Recommendation: run it on a module with REAL candles rather than on
`solana_late_bonding`. `solana_support_bounce` holds 38,592 candles, 88% carrying
genuine highs and lows. That pocket was retired on 28/08, but its data remains,
and for a question about market mechanics the pocket's identity matters less than
the quality of the price path.

## Method note kept for reuse

Two screens, applied before concluding rather than after:
- **hard**: a quantity that CANNOT take a given sign. A negative lookahead bias is
  structurally impossible; obtaining one exposed a join defect (FINDING-0003).
- **soft**: a quantity whose ORDER OF MAGNITUDE would be implausible. 59% of stops
  never firing on a mechanism running in production for weeks was too high to
  believe, and that implausibility is what triggered this check.
