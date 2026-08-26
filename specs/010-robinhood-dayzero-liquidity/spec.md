# Feature Specification: Calibrate Robinhood shadow pocket's day-zero liquidity gate

**Feature Branch**: `010-robinhood-dayzero-liquidity`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Calibrer le sourcing et la stratégie de la poche shadow Robinhood
(robinhood_pump_shadow.py / robinhood_pump_v2_shadow.py), suite au diagnostic du 26/08 : le
day-zero discovery (specs/006, déployé le 25/08) a remplacé DexPaprika comme source primaire de
candidats, mais aucun candidat qualifié n'a été produit depuis le 25/08 23h00 UTC (silence
total, ~15h+). Cause racine mesurée : MIN_LIQUIDITY_USD=4000.0, calibré le 23/08 sur l'ancien
mécanisme DexPaprika (200 trades réels, winrate 61.8%), appliqué tel quel à la nouvelle
population day-zero (médiane de liquidité quasi nulle à la détection, mesuré sur 318 rejets
réels : p75=134$, p90=2460$) bloque pratiquement 100% du flux day-zero. Le seuil est appliqué en
double (discovery-level et record_signals-level, dans v1 ET v2). Question ouverte : seuil ou
timing de mesure (laisser le pool mûrir avant de juger sa liquidité) ou les deux. Objectif final :
+25%/trade minimum une fois le sourcing débloqué et n>=100 clôtures accumulées. Hors Fast-Track
(paramètre de stratégie) -- cycle spec-kit complet. Kill-switch /stop reste armé (capital réel),
shadow/simulation uniquement. Un spec par poche shadow -- celui-ci est dédié Robinhood."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The Robinhood shadow pocket resumes seeing real candidates (Priority: P1)

The day-zero discovery feed has been silent for the pocket's regime/candidate table for over
15 hours because every detected pool fails a liquidity floor calibrated for a completely
different population (pools already "trending" with tens of thousands of dollars in reserve,
not pools just created). The pocket must resume qualifying real candidates without simply
disabling the floor (which would reopen the exact defect fixed on 23/08: positions on
near-zero-liquidity pools that are unenterable/unexitable in practice).

**Why this priority**: nothing else in this spec can be measured or calibrated while the
pocket produces zero candidates — this is the blocking prerequisite.

**Independent Test**: query the day-zero regime/candidate table after deployment — new rows
with a `decided_at` timestamp after the fix ship, at a rate consistent with Robinhood Chain's
real pool-creation volume (not zero, not every single pool unconditionally).

**Acceptance Scenarios**:

1. **Given** a newly created Robinhood pool with near-zero liquidity at the moment of
   detection, **When** the pocket evaluates it, **Then** it is neither instantly rejected
   (current broken state) nor instantly accepted (would reintroduce the 23/08 defect) — it is
   evaluated using a criterion appropriate to a pool that has not yet had time to receive real
   deposits.
2. **Given** the existing DexPaprika fallback path (used when the day-zero WS feed is
   unavailable), **When** this feature ships, **Then** its own liquidity floor and calibration
   (validated on 200 real trades, 61.8% winrate) remain completely unchanged.

---

### User Story 2 - The liquidity judgment reflects when a pool can realistically be judged (Priority: P2)

Determine whether liquidity should be judged at the instant of detection or after the pool has
had a chance to mature (receive real deposits), and apply that decision consistently across
both places the current filter duplicates it.

**Why this priority**: this is the actual design question behind User Story 1 — solving it
properly (not just picking an arbitrary lower number) determines whether the fix is durable or
just moves the same failure to a different threshold.

**Independent Test**: for a sample of pools tracked through their full observation window,
compare their liquidity at detection vs. at the end of the window — the chosen judgment point
must be shown (not assumed) to separate pools that go on to have tradeable liquidity from
pools that never do.

**Acceptance Scenarios**:

1. **Given** two detection-time-filtering points that today apply the same threshold
   independently (the discovery-level check and the `record_signals` check, duplicated in
   both v1 and v2 of the pocket), **When** the new criterion is adopted, **Then** both points
   apply the same coherent rule for the same entry mode — no silent divergence between them.
2. **Given** a pool whose liquidity never rises above near-zero throughout its entire
   observation window, **When** the pocket evaluates it, **Then** it is still rejected (the
   23/08 defect must not reappear under a different mechanism).

---

### User Story 3 - A recalibration protocol exists for the +25%/trade target (Priority: P3)

Once real day-zero candidates and closures accumulate, define how and when the pocket's
parameters get recalibrated toward the operator's stated target (+25%/trade minimum) —
without forcing a specific number today, since today's sample (12 total closures pocket-wide
as of 2026-08-26) is far below the doctrine's own n≥100 threshold for a trustworthy
recalibration.

**Why this priority**: lowest priority because it depends entirely on User Stories 1-2
producing real data first — defining the protocol now, without data, avoids repeating the
22/08 incident where a filter calibrated on a partial sample evaporated once the full sample
was checked.

**Independent Test**: the protocol document states the exact sample-size gate, the exact
robustness checks required (outlier removal, day-count coverage — per CLAUDE.md's existing
statistical doctrine), and what happens if the sample stays insufficient for an extended
period (explicit fallback: keep collecting, do not force a premature number).

**Acceptance Scenarios**:

1. **Given** fewer than 100 day-zero closures accumulated, **When** anyone (operator or future
   session) asks "what should the threshold be", **Then** the answer references the protocol
   and states the sample is insufficient — it does not produce a number anyway.
2. **Given** 100 or more day-zero closures accumulated, **When** the recalibration protocol
   runs, **Then** it applies the same statistical safeguards already mandated elsewhere in this
   project (outlier removal for top-2/top-5, day-count coverage check) before proposing any
   new threshold.

### Edge Cases

- What happens to a pool that matures past near-zero liquidity but only after the observation
  window has already expired? → It is correctly rejected (the window boundary is a real
  constraint, not a bug) — but this case should be counted/logged distinctly from an
  always-near-zero pool, so a future recalibration can tell "arrived too late" from "never
  arrived" apart.
- What happens if the day-zero WS feed becomes unavailable mid-operation and the pocket falls
  back to DexPaprika? → The DexPaprika path's own untouched threshold (4000$) applies
  automatically — no cross-contamination between the two entry modes' criteria.
- What happens if `robinhood_pump_shadow.py`'s and `robinhood_pump_v2_shadow.py`'s filters
  drift apart during implementation (one updated, the other forgotten)? → Both must be
  verified together; a mismatch is a defect, not an acceptable variance, since both share the
  same imported constant today.
- What happens if the sample stays under 100 closures for weeks? → Documented as a known
  possible outcome (Robinhood Chain may simply not produce enough qualifying volume yet) —
  never silently treated as "good enough" to recalibrate anyway.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST apply a liquidity criterion to day-zero candidates that is
  distinct from the DexPaprika fallback path's criterion — the fallback's validated 4000$
  floor (200 real trades, 61.8% winrate) MUST remain unchanged.
- **FR-002**: The day-zero liquidity criterion MUST be derived from measured data (the real
  distribution of detected-pool liquidity, at detection and/or after maturation), not an
  arbitrary guessed number.
- **FR-003**: The two duplicated filtering points (discovery-level `check_candidates` and the
  `record_signals` check inside the pocket module) MUST apply the same coherent rule for a
  given entry mode — no silent divergence.
- **FR-004**: The fix MUST cover both `robinhood_pump_shadow.py` (v1) and
  `robinhood_pump_v2_shadow.py` (v2) consistently.
- **FR-005**: After deployment, the day-zero regime/candidate table MUST resume receiving new
  qualified rows, verified by direct measurement post-deploy (not assumed from the code change
  alone).
- **FR-006**: Any decision about WHEN liquidity is judged (at detection vs. after a maturation
  window) MUST be justified by an explicit tradeoff analysis (detection latency lost vs.
  signal quality gained), not picked arbitrarily.
- **FR-007**: A recalibration protocol toward the +25%/trade target MUST be documented,
  including the exact sample-size gate (n≥100, per existing doctrine) and the explicit
  fallback behavior if that sample is not reached within a reasonable window.
- **FR-008**: The fix MUST NOT reintroduce the pre-23/08 defect (positions opened on pools
  whose liquidity stays near-zero throughout their entire tracked life).
- **FR-009**: Every rejection and qualification decision MUST remain logged in the existing
  rejection-log mechanism (`fresh_launch_pretrade_gate_log` or equivalent), preserving the
  ability to recalibrate from real data later rather than creating a new unobservable filter.

### Key Entities

- **Day-zero candidate**: a Robinhood Chain pool detected at creation (`PairCreated`/
  `PoolCreated`), attributes include detection timestamp, liquidity at detection, liquidity at
  end of observation window, qualification outcome.
- **DexPaprika fallback candidate**: a pool sourced via the pre-existing trending-pools path
  when the day-zero feed is unavailable — untouched by this feature, kept as the reference
  population its own threshold was calibrated against.
- **Liquidity floor (day-zero)**: new, distinct constant/criterion from `MIN_LIQUIDITY_USD`,
  scoped to the day-zero entry mode only.
- **Recalibration protocol**: a documented procedure (sample-size gate, statistical
  safeguards, fallback if insufficient) for revisiting the day-zero criterion once real
  closures accumulate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within 24 hours of deployment, the day-zero regime/candidate table shows new
  entries at a rate consistent with real Robinhood Chain pool-creation activity (not zero,
  verified by direct query, not assumed).
- **SC-002**: The DexPaprika fallback path's behavior and outcomes are unchanged after this
  feature ships (zero regression on its already-validated calibration).
- **SC-003**: After 7 days of the fix running, the accumulated day-zero closure count is
  measured and reported — either on track toward n≥100 (the minimum for a first provisional
  recalibration pass), or explicitly documented as insufficient with a stated reason (not
  silently ignored).
- **SC-004**: Zero positions are opened, after this fix, on a pool whose liquidity was
  measured as near-zero throughout its entire observation window (no regression on the
  23/08-fixed defect).
- **SC-005 (Closure criterion — operator-directed 2026-08-26, refined same day)**: This spec
  is marked "Closed" only once the **average realized return across at least 1000 closed
  trades reaches +25% minimum** — not a single trade, an average over the full 1000+ sample
  — computed over trades belonging to the SAME "epoch" (the period since the last reset/
  archive triggered by a trading-style-affecting parameter change; this project already
  practices this — see `robinhood_pump_shadow_log_archive_reset_20260825` and
  `_archive_nofloor_age25_20260823`). This fix itself changes the entry style (new day-zero
  liquidity floor), so it starts a NEW epoch: the 1000-trade count restarts from zero at this
  fix's deployment, never blended with pre-fix closures. The n≥100 provisional-recalibration
  minimum (SC-003) still applies for an earlier, interim sanity check within the same epoch —
  it does not substitute for the 1000-trade closure gate. Shipping the code fix (User
  Stories 1-3) makes the implementation "Done"; it does NOT make the spec "Closed" on its
  own. Until 1000 same-epoch closures accumulate and average ≥+25%, this spec remains open
  and tracked, revisited periodically rather than forgotten — and if a FUTURE parameter
  change starts a new epoch before 1000 closures accumulate, the count restarts again (this
  is a feature of the protocol, not a failure of it).

## Assumptions

- The kill-switch `/stop` stays armed throughout this feature's lifecycle (operator's explicit
  choice, real-capital guardrail) — this feature covers shadow/simulation trading only, no
  real-capital activation.
- The final numeric target (+25%/trade minimum) is the operator's stated goal, but this
  feature does not force a specific calibrated number before a trustworthy sample (n≥100)
  exists — User Story 3 defines the protocol, not the final number.
- The existing observation-window mechanism (`_OBSERVATION_WINDOW_SECONDS` in
  `OnChainPoolDiscoveryFeed`) is a candidate building block for the timing question in User
  Story 2; its current value will be verified against real data during planning, not assumed
  correct here.
- This is one of three planned per-pocket specs (Solana, Robinhood, Base) per the operator's
  stated preference for individual tracking — this spec is scoped to Robinhood only.
