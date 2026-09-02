# FINDING-0005 -- Where the winners come from, and the radar that already exists

    2026-09-02, Claude A. Zero RU, read-only.

## Part 1 -- Is the right tail a process property or one lucky event?

B's three tests, on `solana_support_bounce`, 1h horizon, random starts, n=595.

| test | result |
|---|---|
| pools in the top decile | **47 distinct pools** for 59 observations |
| hours spanned by the top decile | **23 distinct hours**, busiest holds 6 of 59 (sample spans 39) |
| first temporal half | n=299, mean +8.5%, median -3.6%, top decile +154.5%, max +939.4% |
| second temporal half | n=296, mean +10.7%, median -1.5%, top decile +142.1%, max +460.0% |

Dispersed across pools, dispersed in time, and **each half carries its own right
tail** with near-identical means and top deciles. Per B's own pre-stated
criterion, that is the signature of a process property rather than a
retrospective illusion.

And the mean here is NOT carried by a handful: removing the single best
observation costs 1.6 points, the best 5 cost 4.8, the best 10 cost 6.7, and the
mean is still +2.9% after. Contrast `solana_pump`, where removing 5 of 444 flipped
+41.3% to -17.9%.

**Reservations that stand.** 39 hours of coverage. Random starts inside pools the
pocket had already selected, so this is B0 (value of the chosen moment), never B1
(value of the chosen pool). No confidence interval on the mean, which is the least
reliable statistic of this distribution.

**Correction B made and A accepts**: a "without top-N" figure is a ROBUSTNESS
TEST, never a return measurement. Publishing one without its raw counterpart is
what produced the false "the terrain structurally loses 18 to 67%" conclusion.
Both figures, always, side by side.

## Part 2 -- REQ-0007 asked to build something that has run since 29/08

Searched before writing any code, as the mandate requires.
`services/onchain_pool_discovery.py` already detects pool creation by subscribing
to the factories' Initialize topic, at a measured 7,200-11,100 units/day, and is
instantiated for Base and Robinhood in the shadow process. Its companion
`discovery_liquidity_observation.py` logs EVERY liquidity decision including the
rejections -- built 29/08 for exactly the reason B restated: without a record of
what failed the floor, "the floor eliminates everything" and "the market produces
nothing" are indistinguishable.

    onchain_discovery_liquidity_log (in shadow.db)
    92,668 observations | 3,359 distinct pools | 5 days | 29/08 -> 02/09 12:35
    base 1,569 pools / 41,337 obs   robinhood 1,790 pools / 51,331 obs

The denominator exists, carrying the pools that failed the floor, the value that
failed, and the threshold it failed against.

**What is genuinely missing**, and it is far smaller than REQ-0007 assumed:
- Detection covers a FIXED factory list, not the whole chain. Deliberate and well
  founded (the 21/08 Solana incident at 74 GB/day is cited). It is a DEX perimeter,
  not a quality filter -- but whether every pool of interest is born on those
  factories remains to be verified.
- Multi-horizon outcomes (10s to 24h) do not exist. This is the real work, and it
  is distinct from detection.
- The random fine-capture sample does not exist.

## Part 3 -- Governance note

B stated the operator had validated the radar twice. A has not seen that in its
own messages, and a peer cannot grant an authorisation on the operator's behalf.
A had itself written into EXP-0001 that this collector never becomes permanent
without an explicit operator go.

The point is largely moot: the collector already runs and its cost has been paid
for four days. Nothing needs to be put into service. Exploiting the 92,668
existing rows and building the outcome layer is read-and-write on a research
table, never a new RU consumer.
