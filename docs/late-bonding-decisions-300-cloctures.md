# LATE-BONDING -- what to decide, and the target to hit

**Operator target set 2026.08.21 (late): +30% average PnL over 500 cumulative
closures of the current epoch** (opened 23:37:10 after banning re-entries).
Milestones alert at 100 / 300 / 500 so the trajectory is visible before the
deadline -- a target only measured at the finish line cannot be corrected.

Everything below was measured on 2026.08.21 over ~210 closures. None of it is
applied unless stated: a filter validated on the very sample that suggested it
is circular, so each item waits for data it never influenced.

**Two filters were already invalidated exactly that way.** Buyer acceleration
and sell-pressure slope looked like the two strongest signals of the evening on
90 closures (+24% and +29% against what they cut). On 207 closures they both
fell BELOW doing nothing at all, while cutting 7-8 x2 winners each. They are
dead, and the lesson outranks them: no signal counts until it holds on data it
did not shape.

---

## A. Exit parameters -- to decide TOGETHER (operator decision, 21/08)

Both mechanisms cut too early, and a live trade the same evening showed it: a
token peaked at x5.23, the trailing exited the remainder at x4.15, but the
ladder had already sold 75% on the way up. Result x2.66 instead of x4.15 --
**149 points lost on one trade**.

1. **Trailing stop** -- replayed over 277 archived paths, the result rises
   monotonically with the distance: 13% (current) +6.03%, 25% +7.65%, 35%
   +8.01%, 50%+ +8.51% where it stops firing entirely. Worth ~1.5 to 2.5 points.
   Widening and removing converge, so the mechanism is not the problem, its
   tightness is.

2. **Profit ladder** -- simulated over 96 closures: current 50/100/200 at 25%
   gives +21.36%, late rungs 75/150/300 give +22.59%, a single +100% rung at
   50% gives +22.48%, no ladder at all +22.99%. Early rungs are clearly worse
   (30/60/120: +18.83%). On the average the gap is thin because most positions
   never climb far; on the rare big winners it is brutal.

3. **Ratchet floor** (operator idea) -- TESTED AND REJECTED on data: over 303
   paths it changed 3 positions, saved 0 and amputated 3. The ladder already
   secures the gain, so a floor on top protects nothing that is not already
   banked. Do not revisit without new evidence.

---

## B. Entry filters -- validate OUT OF SAMPLE, never combine

Each one cuts losers without decapitating winners -- that is what separates
them from every filter dismissed earlier. All three cut exactly ONE trade that
made x2 or better.

4. **Sell-pressure slope < 0.029** -- kept +29.12% vs +11.78% cut (n=69).
   Strongest signal, smallest sample.
5. **Buyer acceleration > 1.095** -- kept +24.28% vs +9.62% cut (n=87).
   Surfaced independently in FOUR separate analyses, which is what makes it
   credible rather than a data-mining artifact.
6. **Curve velocity > 0.30** -- +22.11% against +18.50% unfiltered (n=105).

**Do NOT stack them.** All three together reach 67% of the target population
but drop to +19.49%, below any single one: at three they also cut 68 good
positions. One filter beats three.

---

## C. Analyses to redo with more data

7. **Top buyer share** -- history says concentration helps (+6.69% vs -3.83%),
   the current epoch says the opposite (+22.72% vs +12.26%). One of the two is
   noise. Re-decide on the epoch alone once it is large enough.
8. **Distinct buyers** -- the earlier study was INVALID: the count depends on
   how long the stream had been listening, and shortening that window divided
   the average from 196 to 34 with no market change. `observation_seconds_at_entry`
   now exists, so redo it normalised.
9. **Transaction intensity** -- `trade_count_at_entry` / `reserve_usd` gives an
   activity measure comparable across token sizes (operator insight: a $257K
   token with 220 txns is more active than a $3M one with 1906). Never analysed.
10. **Holding time** -- positions held over 15s return +25 to +30% against
    +13.23% for shorter ones, and 61% of positions are shorter. Never explored;
    possibly the most valuable open question.
11. **Rebound vs collapse** -- among positions that rose then gave back, only
    26% recovered. Buyer acceleration separated them (1.40 vs 0.91) on just 10
    vs 29 cases. Reconfirm.

---

## D. Standing caveat for real capital

12. **43% of the paper PnL comes from positions held under 15 seconds.** Entry,
    Solana confirmation and exit each cost seconds, so those trades are the
    least reproducible with real money. Any real-capital projection must be
    discounted for this, and the measured round-trip friction (2.47% at $0.10)
    applied on top.

---

## Also open, unrelated to the 300 mark

- 30 modules still use the hand-written column migration; three were moved to
  `db_migrations.ensure_columns`. Migrate opportunistically.
- FAST-DISCOVERY exit tracking stays wired until its last open positions close;
  remove the loop afterwards.

---

## E. Found during the closure-by-closure diagnostic (21/08, late)

Never analysed before that pass, all four columns filled 100% since day one.

13. **Paid DexScreener profile** -- 34 closures at +5.4% (**-0.3% without their
    top two**, 41% winrate) against +25.3% / +22.4% / 68% for the 176 without.
    Someone paying for visibility on a bonding-curve token is buying attention,
    not building.
14. **Repeat creator** -- 23 creators launched more than one of our tokens; their
    54 positions returned +10.3% against +26.1% for the 156 single-launch
    creators. Token-factory signature.
15. **Combined (13 + 14)** -- keeps 142 of 211, returns **+27.0% (+23.5% without
    top two)** against +22.0%, and costs only TWO x2 winners. Best
    gain-to-damage ratio of any filter tested. NOT applied: it comes from the
    same sample it was found on, and two filters just died that way today.
16. **Reinforcement is structurally losing** -- simulated on the 96 positions
    where it triggered: +34.6% against +54.9% for the real ones, worse in 96
    cases out of 96. Adding the second half of the stake at +30% means buying
    higher; on a distribution where winners explode, full exposure from entry
    always wins. Do not implement.
17. **Transaction intensity, normalised** -- raw transaction counts are a trap
    (they mostly measure how long the stream had been listening, correlation
    +0.52 with observation time). Per SECOND of observation the gradient
    reverses and holds: 0.03-0.5/s returns +20.3%, 2.65-16/s returns +32.5%.
    Converges with curve velocity and short observation time -- three angles on
    one idea: take the FAST tokens.
18. **Wash trading NOT provable with current data** -- the transactions/distinct
    buyers ratio shows no monotonic gradient and caps at 5.3 (real wash trading
    would show 20-100). Only 1 collapse in 101 closures, so nothing to predict
    against. What would settle it: the count of wallets that BOTH buy and sell
    the same token in the window -- the stream tracks buyers and sellers
    separately and never stores the intersection.
