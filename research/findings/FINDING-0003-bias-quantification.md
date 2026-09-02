# FINDING-0003 -- Residual bias quantified, and a worse defect found underneath

    requested by: Claude B, 2026-09-02 -- quantify the residual lookahead on a
    named late_bonding table before building anything on it.
    produced by: Claude A. Zero RU, read-only.

## The named table cannot answer the question

`solana_late_bonding_shadow_log_archive_floor3000_20260822` has **zero candles**
within its positions' lifetimes. It is the largest table (1622 closures) and the
one without a candle archive: the instrumentation landed during or after that
period. Volume turned out to be anticorrelated with coverage.

## Measured on the two tables that do have coverage

Tables chosen on DATA COVERAGE ALONE, never on their result -- picking a table
after seeing its PnL would be the multiple testing this project forbids.

Method: for each `trailing_stop` exit, walk the post-entry candles in real time
order and find the FIRST instant the stop would causally have fired, i.e. where a
candle low drops below (peak seen SO FAR) x (1 - 15%). Compare the peak available
at that instant with the peak the code used. The gap, in points of PnL, is the bias.

| table | measured | median | mean | p90 | max | >2pts | >5pts |
|---|---|---|---|---|---|---|---|
| `fixedstop5_20260823` | 41 | 0.00 | 0.89 | 2.17 | 17.2 | 12.2% | 4.9% |
| `reset_20260825` | 48 | 0.24 | 5.78 | 24.34 | 42.3 | 37.5% | 29.2% |

Against B's thresholds both medians clear the 2-point bar. But the tail diverges
violently from the median on `reset_20260825`: mean 5.78, and 29.2% of cases above
5 points. B's rule was that the median decides usage and the tail says whether a
few positions carry all of it. Answer: they do, and on that table it is nearly a
third of the sample. The two tables are not interchangeable.

## A defect found in this measurement BEFORE delivering it

The first pass joined candles to positions on `pool_address` alone. It produced a
NEGATIVE bias of -17.9 points on floor3000 -- structurally impossible, since the
code can only ever use a peak greater than or equal to the causal one. That
impossibility is what triggered the check: 32 pools carry more than one position,
and candles span 22-27/08 while a table may cover a single day, so another
position's path was being mixed in. Fixed by bounding candles between
`detected_at` and `last_checked_at`. The negative bias disappeared.

## The larger finding: the stop often never fires at all

Replayed causally, the stop is **never reached in 59 of 100** usable cases on
`fixedstop5`, and 25 of 73 on `reset_20260825`. The code closes positions that, in
the real order of events, should not have closed. That is not an exit price that
is too high; it is an exit that does not exist.

Not a coverage artifact: 106 of 111 positions have candles covering at least 95%
of their lifetime, verified before reporting.

**Limit that cannot be lifted from here.** The code reads
`price_high/low_since_last_read` from the websocket, which captures the extremes
BETWEEN two reads. If the archived candles are point samples rather than those
extremes, they are blind to the dip the websocket saw. So "the code closes
wrongly" and "these candles cannot see what it saw" remain indistinguishable, and
they have opposite consequences: the first is a severe defect, the second makes
the 59% meaningless. Settling it requires checking how these candles are produced.

## Second request: buy-and-hold control on robinhood_pump

B's wider question: does the memecoin terrain lose structurally, or was that
specific to solana_pump? Answer: structurally, and Robinhood is far worse.

Without the top 5, same impact model, 158 closures:

| size | executable | pocket | hold 15min | hold 1h | hold 2h |
|---|---|---|---|---|---|
| $2 | 40/158 | -53.2% | -60.2% | -66.2% | -72.9% |
| $25 | 39/158 | -55.9% | -61.7% | -67.2% | -73.8% |
| $100 | 37/158 | -62.9% | -68.8% | -81.3% | -82.2% |
| $1000 | 26/158 | -78.3% | -88.0% | -89.7% | -90.5% |

Holding one hour at $25 loses 67.2% here against 17.9% on Solana. And only 39 of
158 positions can take a $25 size at all -- three quarters of this pocket cannot
accommodate any useful size, against 448 of 579 on Solana.

The pocket column stays contaminated by the same lookahead and is shown only as a
landmark. The three control columns are not: they come from `forward_pct_*` and
never touch `final_multiplier`. They carry the conclusion.

**solana_pump was the better of the two.** The general conclusion does not fall,
it strengthens.
