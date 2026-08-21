# Should +25% become the floor instead of 0%?

> Operator question, 2026.08.21 late night, to answer on waking. Prepared with
> the epoch's own data (222 closures) rather than from principle.

## The question, restated precisely

Today any trade with a positive expected outcome is worth taking. Should the
bar move to +25%, so the pocket only fires on setups it judges excellent, or is
a modest gain between 0 and +25% still worth the risk?

## What the data says

Selecting only the best setups raises the AVERAGE but not the TOTAL. Simulated
on the epoch's 222 closures, assuming perfect foresight (an upper bound we can
never reach in practice):

| Selectivity | Trades | Average PnL | **Cumulative gain** | Trades/hour |
|---|---|---|---|---|
| all | 222 | +23.8% | **+52.7** | 31.7 |
| top 75% | 166 | +37.5% | **+62.3** | 23.7 |
| top 50% | 111 | +56.2% | **+62.4** | 15.9 |
| top 25% | 55 | +91.5% | +50.3 | 7.9 |
| top 10% | 22 | +136.0% | +29.9 | 3.1 |

The cumulative gain peaks around 50-75% selectivity, then FALLS. Cutting past
that point removes more capital deployed than it removes bad outcomes.

## Why the average is the wrong target on its own

A +25% floor optimises a ratio, and a ratio can always be improved by trading
less -- down to one perfect trade a month at +200%, which would look excellent
and earn almost nothing. What compounds a portfolio is total gain per unit of
time and capital, not the prettiness of the average.

Two constraints make this concrete here:

- **Positions are short.** Median holding time is under a minute, so the same
  capital recycles dozens of times an hour. Volume is not a cost, it is the
  engine.
- **Capital is the binding constraint, not opportunities.** With ~32 trades an
  hour and a small wallet, what limits the pocket is how much it can deploy at
  once -- and that argues for taking every positive-expectancy trade the
  capital allows, not for waiting.

## Where a floor DOES make sense

A floor stops being arbitrary the moment a trade has a real cost beyond its
outcome. Two exist here and both are measured:

1. **Round-trip friction: 2.47% at $0.10 per trade**, and it grows with size
   (7-8% at $200). Any expected gain under that is a guaranteed loss -- there
   the floor is not a preference, it is arithmetic. This is already why
   `solana_trade_pilot.MIN_TRADE_USD` refuses trades below $0.02.
2. **Simultaneous positions competing for the same capital.** With real money
   and several candidates at once, a +5% setup taken now can block a +80% one
   thirty seconds later. That is an opportunity cost, and it justifies ranking
   candidates rather than taking them first-come.

## Recommendation

**No fixed +25% floor. A floor at the friction cost, and ranking above it.**

Concretely: refuse anything whose expected gain does not clear the measured
round-trip cost (already enforced), and when several candidates compete for
limited capital, take the best-ranked rather than the first seen. That captures
the intent -- excellence in the ratio -- without paying for it in foregone
compounding.

**And a caution that outranks the whole question**: we cannot rank setups by
expected outcome today. Two filters that looked like strong predictors on 90
closures fell BELOW doing nothing when the sample doubled. A +25% floor would
require exactly the prediction ability we have just proven we lack. Revisit
once a filter has held on data it never influenced -- which is what the 500
closures are for.
