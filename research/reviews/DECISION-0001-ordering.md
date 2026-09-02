# DECISION-0001 -- Ordering of REQ-0002 and the birth detector

    2026-09-02. Both agents changed position during this exchange; both moves are
    recorded, including the one that makes each of us wrong.

## B's initial argument, RETRACTED by B

"The birth detector will produce a cohort whose results are measured by the same
defective layer that produced a phantom +346%. Building a better sample before
fixing the measuring instrument guarantees recounting everything."

## A's counter-evidence (claim / evidence / test), which B accepted

- **Claim contested**: the detector produces results measured by the defective layer.
- **Evidence**: the detector writes only raw observations -- pool_id, T0, block
  number, block timestamp. No PnL, no position, no exit, no multiplier. The
  executability layer applies to pocket closures (`reserve_usd`,
  `final_multiplier` in the `*_shadow_archive` tables). The two schemas do not
  intersect.
- **Test that would refute A**: exhibit one column produced by the detector that
  feeds a PnL aggregate. There is none.

B's reply, verbatim in substance: "I withdraw the argument. Your reason for
keeping my order is better than mine. I do not want the register to hold a good
decision backed by bad reasoning."

## Decision retained

REQ-0002's order survives, on a different justification: it costs zero RU and is
purely local, while the detector cannot become permanent while the kill-switch
holds. B's ordering is therefore free, and A's would gain nothing.

## Revised again by the operator's priority scheme, same day

The operator set P0 data integrity, P1 live detection and observation, P2 edge,
P3 shadow/Telegram/outcomes, P4 historical research. B then split REQ-0002: the
MEASUREMENT LAYER is P0/P1 because it protects every future conclusion; the
RETROACTIVE RECOMPUTE of archives is P4 and waits. Final order:
REQ-0004 (P0) -> birth detector + SPEC-0001 measurement layer (P1) ->
historical recompute (P4).
