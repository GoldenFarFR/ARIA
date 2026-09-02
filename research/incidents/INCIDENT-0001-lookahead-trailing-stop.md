# INCIDENT-0001 -- Execution lookahead in solana_pump_shadow's exit simulation

    severity: CRITICAL (data quality)
    found by: Claude B (measurement), diagnosed in code by Claude A
    date: 2026-09-02
    status: CONFIRMED. Affects every PnL figure ever produced by this pocket,
            including the ones Claude A computed earlier TODAY.

## What B measured

Holding duration on all 579 closures, by exit reason:

    trailing_stop       n=409   min 120.0   med 122.3   max 128.7
    age_limit           n=106   min 120.0   med 122.0   max 128.5
    max_hold            n=62    min 120.0   med 122.5   max 128.6
    scale_out_complete  n=2     min 120.7   med 120.8   max 120.8

Not one position closes before 120.0 minutes. A trailing stop that never fires
before exactly two hours is not a trailing stop.

Confirmed independently by A on the whole table: duration spans 120.0 to 128.7
minutes, zero closures under 119 minutes. Each position was evaluated ONCE, in a
single pass covering its entire two-hour window.

## The mechanism, read in the code (not inferred)

`solana_pump_shadow.advance_exit_simulation`, in this order within one pass:

    line 1354   peak_price = max(peak_price, effective_high)   # window HIGH
    line 1508   elif effective_low <= peak_price * (1 - TRAILING_STOP_PCT/100)
                # window LOW, tested against the peak just updated above

`effective_high` and `effective_low` are the high and low of the SAME candle
window. Their ORDER inside that window is not known and not used.

So when the price dips then rises inside one window, the code:
1. raises the peak to a high that occurred AFTER the dip,
2. tests the stop against a low that occurred BEFORE that peak,
3. and on a hit, fills at `peak * 0.8` -- a price derived from a peak that did
   not yet exist at the moment the stop was touched.

## Why this is not the "limit order" defence

The fill-price doctrine is defensible on its own terms: a stop posted in advance
fills at its own threshold, and the module documents this deliberately. The
defect is not the fill price, it is the THRESHOLD, which is computed from
information posterior to the trigger. A real stop's threshold can only ever use
the peak reached SO FAR.

## Blast radius

409 of 579 closures (70.6%) exited by `trailing_stop`. 274 positions have a peak
above entry, i.e. a peak that moved and could therefore be posterior to a dip.
The bias is systematically OPTIMISTIC: it can only raise the exit price.

Every figure derived from `final_multiplier` on this pocket is contaminated,
including those A produced earlier today (the +17.4% realistic-at-$0.10 read, and
the whole multi-size executability ladder). They are withdrawn.

## What survives, and it is not nothing

The buy-and-hold baseline does NOT use `final_multiplier`. It is computed from
`forward_pct_m15/h1/h2`, plain forward returns from the entry instant, and is
therefore uncontaminated. Measured with the same impact model, same population,
same sizes, without the top 5:

| size | pocket (contaminated) | hold 15min | hold 1h | hold 2h |
|---|---|---|---|---|
| $2 | +2.9% | -15.5% | -15.7% | -16.6% |
| $25 | +1.5% | -17.6% | -17.9% | -20.9% |
| $100 | -3.7% | -22.3% | -24.0% | -23.9% |
| $1000 | -28.6% | -43.3% | -45.6% | -45.8% |

B predicted this exact reading before the diagnosis: "if that mechanism is
retroactive, that gap IS the measure of the lookahead." The roughly 19-point gap
at $25 is therefore not an edge. It is the artifact's size.

The valid, uncontaminated finding is the baseline itself: **buying these pools at
ARIA's chosen instants and holding two hours lost about 18% at a $25 size, and
about 45% at $1000.** That is a real measurement of the terrain, and it is bad.

## Consequences

- EXP-BASELINE-001 must NOT run on this dataset as designed: it would compare a
  strategy that cheats against controls that do not.
- The `exit_reason` field discriminates nothing here. All four values describe the
  same event: a single evaluation at T+120min.
- This is not repairable on this dataset. Repairing it requires either a real
  intra-window price path (candles per position, which `shadow_candle_archive`
  does NOT hold for this pocket) or a genuinely periodic evaluation cadence.

## Fix owner and scope

The code change (peak must only use information prior to the trigger, or the
evaluation cadence must be real) touches a LIVE pocket's exit behaviour. That is
a trading-behaviour change, therefore a HUMAN GATE. Not applied by either agent.
