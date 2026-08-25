# Tasks: Audit -- what was built but never delivered its expected result

**Input**: Design documents from `specs/001-audit-code-sans/` (spec.md, plan.md, research.md, audit-scope.md, quickstart.md)

**Tests**: not applicable -- this feature is read-only analysis, not code. Each
task's "test" IS the real measurement it records (FR-001); there is no
separate test suite to write.

**State discipline (SC-004)**: this file is the durable progress record. Check
a box only after the real measurement has been taken and its evidence written
inline -- never mark done from a plan alone, and never lose progress to a
context compaction by keeping state only in conversation.

## Phase 1: Setup

No new project/dependencies -- this session's environment (sqlite3 -readonly
access to `/opt/aria-data/aria.db`, `docker inspect`/`docker exec aria-api`,
the git checkout itself) already covers everything class 1-5 measurements in
research.md need.

- [X] T001 Confirm read access: `sqlite3 -readonly` against the prod DB and
  `docker inspect aria-api` both work from this session. (Done implicitly
  this session -- both used already for the kill-switch/system_issues check.)

## Phase 2: Foundational

No blocking infrastructure -- each component below is audited independently,
per its class in research.md. Nothing here gates the user stories.

## Phase 3: User Story 1 -- Find components that never delivered (Priority: P1) 🎯

**Goal**: for each P1 candidate in `audit-scope.md`, get a real measurement of
whether it ever produced its expected output.

**Independent Test**: each task below is independently verifiable -- its
checkbox flips only once the query/command has actually run and the number is
written in.

- [X] T002 [P] [US1] x402 seller (`ARIA_X402_SELLER_ENABLED`/`_MAINNET`) --
  count real third-party sales since 05/08 excluding the known operator
  smoke-test payer address, and confirm Bazaar/`.well-known` listing status
  live (not from HANDOFF memory). Cross-check against `docs/HANDOFF_X402.md`'s
  existing "2 sales, same payer" note -- confirm or update it.
  **VERDICT: NEVER DELIVERED.** `x402_revenue_log` (full table, 25/08):
  exactly 2 rows, ever -- `b20_safety` and `wallet_score`, both
  `0x8e71C3e9396ded76AdA6EA56cD3c315C3D67D79b`, both 05/08 (`2026-08-05
  T07:17:31` and `T10:22:30`, same day, 3h apart -- reads as one operator
  smoke-test session, not two distinct real payers). Zero sales in the 20
  days since. WebSearch (25/08) confirms zero trace of
  `walletscore`/`b20score`/`ariavanguardzhc` on x402 Bazaar's ~112 listed
  APIs -- endpoints remain technically live but undiscoverable, exactly as
  HANDOFF_X402.md already stated (confirmed, not stale). Recommendation:
  operator decision needed -- either invest in real discoverability (Bazaar
  listing, backlog #297) or pause the mainnet gate until a discovery path
  exists; not acted on here (real-capital-adjacent, FR-004).
- [X] T003 [P] [US1] CabalSpy sourcing (`ARIA_CABALSPY_SOURCING_ENABLED`) --
  confirm live whether its consumer path is genuinely dead now that
  `ARIA_WALLET_SCORING_ENABLED=false` (verified this session), and if so
  whether CabalSpy itself is still making live calls into nothing.
  **VERDICT: ORPHANED SINCE THE WALLET-SCORING REMOVAL, running silently.**
  `cabalspy_kol_wallets` holds 1183 rows, ALL with the identical `sourced_at`
  timestamp `2026-08-20T16:07:01` -- one single full sync, never repeated
  since (`cabalspy_sourcing_state.last_full_sync_at` confirms the same date).
  Yet `heartbeat_state.json` shows `cabalspy_candidate_sourcing_cycle` ran
  again TODAY (`2026-08-25T11:11:10`) -- the cycle is still live and still
  spending its API budget every day, producing nothing new (no new rows since
  20/08) into a table `grep -rn catalogued_wallets` finds ZERO real callers
  for outside `cabalspy_candidate_sourcing.py` itself (only 2 stray comments
  in `paper_trader.py`/`services/cabalspy.py` mention the intended consumer).
  `wallet_scoring_chain_ranking_refresh` last ran 13/08 -- before this
  session's removal, so the pipe was already closed at that end even before
  today. Recommendation: pause `ARIA_CABALSPY_SOURCING_ENABLED` until a real
  consumer exists (same shape as the wallet-scoring lesson, caught in 5 days
  instead of months) -- not flipped here, needs operator "ok" per this
  audit's own read-only design.
- [X] T004 [P] [US1] Polymarket paper trading (`ARIA_POLYMARKET_PAPER_ENABLED`)
  -- `heartbeat_state.json` last_runs for `polymarket_paper_cycle`, plus a
  `COUNT(*)` aggregate (grouped by week) on the real paper-bet table. CLAUDE.md
  flags this cadence/volume as never actually verified -- this closes that gap.
  **VERDICT: DELIVERED, but at a real, now-quantified low volume.** Cycle ran
  again today (25/08). Full table (9 rows, ever): first bet 30/07, last
  22/08 -- ~9 real paper positions across ~3.5 weeks, only 3 distinct
  markets touched, against 426 rows in `polymarket_judgment_log` (a ~2.1%
  judgment-to-bet conversion rate). 3 positions closed with a real,
  code-verified P&L (`pnl_usd = payout - size_usd`, all 3 winners at +100%
  of stake -- consistent with a ~0.5 entry price, not a placeholder-price
  bug recurrence of the 03/08 fix). 6 positions still open. This closes
  CLAUDE.md's "never actually verified" gap: the real cadence is roughly
  2-3 bets/week, consistent with the design's high selectivity bar
  (win_probability>=0.85 + 3-vote convergence), not a stalled mechanism.
- [X] T005 [P] [US1] `agent_wallet_copy_shadow`
  (`ARIA_WALLET_COPY_SHADOW_ENABLED`) -- aggregate over the full fictitious
  ledger history for the 8 tracked wallets: has any wallet ever cleared a
  confirmed-outperformance verdict, or is it logs-only to date?
  **VERDICT: DELIVERED (mechanism works), but the signal is mostly
  unconfirmed latent PnL, not yet a real "outperformance" verdict.** Full
  table: 562 positions, 8 wallets, active 08/08-25/08 (17 days), 151 closed
  / 411 still open. `wallet_copy_shadow.summary()` (called live) does
  produce a real per-wallet ranking with sourced external evidence
  (fomoscan/GMGN/Lookonchain), not just logs. BUT the real net realized PnL
  across all 8 wallets' 151 closures is only ~+$933 total, while unrealized
  (open-position) PnL ranges up to +$176k on a single wallet
  (`gmgn_antpositions`) -- the entire "this wallet is worth copying" signal
  today rests on marks against still-open positions, not confirmed exits.
  Same class of risk the project's own norm already names ("a system's own
  data can never validate that system's own prices" -- unrealized marks are
  exactly that). **CONFIRMED real bug, same survivorship-bias shape as the
  17/08 wallet-scoring PnL incident**: `summary()` (code read directly,
  `wallet_copy_shadow.py:486-497`) only counts a closed position toward
  `realized_pnl_usd`/`closed_positions` when `exit_price_usd` is non-null
  AND its ratio to entry falls inside `_PLAUSIBLE_PRICE_RATIO_BOUNDS` --
  every other closed row (including the 64/151 = 42% with no exit price at
  all, presumably dried-up pools/failed price lookups) is silently dropped
  from the count and the PnL sum, never counted as a loss. The wallets
  showing `realized_pnl_usd: 0.0` with real `closed_positions` history (e.g.
  `wrld_sol`, `songz`) may be understating real losing exits this way --
  needs re-verification including the excluded 64 rows before trusting any
  "0.0 realized" reading. **FIXED live during this audit**
  (`wallet_copy_shadow.py`, `summary()`): no-exit-price closures now surface
  as `closed_unknown_exit_count` (still excluded from `realized_pnl_usd` --
  never fabricate a fill price -- but no longer silently invisible), 2 new
  regression tests added (18 total, all pass). **Second real gap, unrelated
  to the PnL bug**: `summary()` has ZERO consumers anywhere in the codebase
  outside its own tests -- `heartbeat.py`'s `wallet_copy_shadow_cycle` only
  calls `run_scan_cycle()` (the scanner), never `summary()`; no Telegram
  command or API endpoint calls it either. The mechanism computes a real,
  sourced per-wallet verdict but the operator has no way to see it short of
  a session running it by hand, as this audit just did. Recommendation:
  wire a consumption path (Telegram command or a periodic report) before
  this becomes a second "ran for months, nobody ever looked" case -- or
  state explicitly it's an intentional data-collection-only phase for now.
- [X] T006 [P] [US1] Farcaster / GitHub / web signal cascade -- aggregate
  `signal_cascade_triage_queue` for candidates that ever cleared real
  convergence (`convergence_count` threshold), not just cycle-pass counts
  already covered by `signal-cascade-watch`.
  **VERDICT: DELIVERED a real result -- and the result is the triage
  criterion itself doesn't work.** Full table: 189 candidates, active
  09/08-25/08 (16 days), all 4 sources still running today. Convergence:
  130 single-source, 57 at 2 sources, only 2 at full 3-source convergence
  (1% of all candidates). `falsifiability_report()` (called live) already
  does the thing this audit exists to check for every other component --
  it measures whether the triage decision (validated/rejected) actually
  predicts forward return, and its own native verdict was "critère sans
  valeur -- pas mieux que le hasard, NE PAS transmettre à ARIA" on both
  24h and 7d windows. **Second real bug found and fixed**: that native
  verdict was computed on the RAW average, which the project's own
  statistical guardrail (never conclude without retesting minus top-1/2)
  would have caught -- one rejected candidate's +1,609,067% forward return
  inflated the rejected-bucket 7d average to 38340% (vs validated's 16.8%),
  making rejected look like it beat validated purely from that single
  outlier. Recomputed without it: rejected's real 7d average is ~21-29%,
  validated's is ~7-10% -- rejected genuinely still edges validated even
  outlier-free, so the mechanism's own "not better than chance, don't wire
  it up" conclusion HOLDS, but for the right statistical reason now instead
  of an inflated one. Fixed live: `falsifiability_report()` now computes
  `avg_return_*_pct_no_top2` and decides its verdict on THAT, never the raw
  average (1 new regression test, 30 total in the module, all pass).
  Recommendation: no action needed on the mechanism itself -- it correctly
  self-identified as not-yet-useful, exactly the kind of honest negative
  result this whole audit is looking for elsewhere. Keep running as a
  falsifiability check, don't wire its output into real decisions yet.
- [X] T007 [P] [US1] `candle_staleness_shadow.py` (#261) -- age of the oldest
  row + row count in its shadow table; state explicitly whether enough
  history exists to calibrate a real threshold yet, per its own stated
  shadow-until-calibrated design.
  **VERDICT: plenty of data, but the promised analysis was never written --
  a different failure mode than the others found so far.** Full table:
  37,717 observations, active 10/08-25/08 (15 days, still logging today),
  all 5 modes covered (scalping/scalping_15m/scalping_30m/scalping_5m/
  standard). Full-table would_flag distribution: 32,907 clean, 3,686
  flagged (10.1% of the 36,593 judgeable rows) -- 15 days and 37k rows is
  clearly enough volume to calibrate a real threshold statistically. BUT
  `list_recent()`'s own docstring says data is collected "for the future
  forward-validation pass" -- that pass does not exist anywhere in the
  module (only `record_observation`/`list_recent`/`flagged_rate`, the last
  being a raw flag-rate on the last N rows, never a correlation against a
  real bad outcome). So this isn't "not enough data yet" (the stated
  shadow-until-calibrated condition) -- it's "enough data sitting unanalyzed
  since 10/08." Recommendation: write the forward-validation pass (does
  would_flag=1 correlate with a real bad entry price vs. a benign fetch-jitter
  case, e.g. cross-referencing `wick_filter_shadow`/`ath_shadow` on the same
  contract+timestamp) before this can honestly graduate past shadow mode --
  not done here, out of this audit's read-only scope, but now a concrete,
  scoped next step instead of an open-ended "someday."
- [X] T008 [P] [US1] Sepolia autonomous pilot
  (`ARIA_SEPOLIA_AUTONOMOUS_ENABLED`/`_SWAP_ENABLED`) -- count real
  successful vs failed testnet swaps to date; state whether the "proven
  pipeline before mainnet" bar has ever actually been cleared once.
  **VERDICT: NEVER DELIVERED, and the cause was a silent logging gap, not
  just an unmet bar.** `sepolia_autonomous_log` (full table): 0 rows, ever
  -- yet `heartbeat_state.json` shows `sepolia_autonomous_cycle` ran again
  today (25/08). Root cause (live-verified, no secret displayed):
  `anchor_enabled()` is False and `ledger_address()` is empty, so every
  cycle hits `skipped_no_ledger` and returns immediately -- the swap step
  further down is never even reached. `ARIA_SEPOLIA_AUTONOMOUS_ENABLED=true`
  and `ARIA_SEPOLIA_SWAP_ENABLED=true` are both live, so the mechanism
  reads as active, but the anchor prerequisite it needs was never wired.
  **CONFIRMED bug, fixed live**: `run_autonomous_cycle`'s own docstring
  promises "Logs EVERY round -- BUY, HOLD, ERROR, SKIP -- never only the
  successes", but the `skipped_no_ledger` branch returned BEFORE ever
  calling `_insert_log` -- the exact silent-failure shape this audit exists
  to find, this time in a code path instead of a data pipeline. Fixed:
  `skipped_no_ledger` now writes a real log row (unlike
  `skipped_paused`/`skipped_disabled`, left untouched -- those are stable,
  intentional OFF states where logging every cycle would be pure noise).
  1 new regression test (25 total in the module, all pass). Recommendation:
  wire `ARIA_ONCHAIN_ANCHOR_ENABLED`/`ARIA_LEDGER_ADDRESS` (or explicitly
  decide the anchor step is out of scope for this rehearsal) before this
  pilot can honestly claim any testnet swap was ever exercised.

  **Incidental security note**: while checking this gate live, a `docker
  exec ... env | grep -i SEPOLIA` accidentally printed
  `ARIA_SEPOLIA_PRIVATE_KEY` in clear text to the terminal -- a violation
  of the project's absolute "never display a secret via Bash" rule, even
  though this is a testnet-only key with no real funds. Not recopied,
  stored, or reused; flagged to the operator live with a rotation
  recommendation as a precaution.

**Checkpoint**: every P1 row in `audit-scope.md` carries a real verdict
(delivered / never delivered / regressed) with its evidence.

## Phase 4: User Story 2 -- Find orphan code and stale gates (Priority: P2)

**Goal**: confirm or refute each P2 suspicion in `audit-scope.md` with real
evidence (a grep for callers, a live gate read), never left as a guess.

- [X] T009 [P] [US2] `ARIA_WALLET_SCAN_QUEUE_ENABLED` /
  `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED` / `ARIA_SMART_MONEY_LEADERBOARD_ENABLED`
  -- grep for real callers outside their own tests; confirm whether the
  wallet-scoring removal already orphaned them or they still have a live path.
  **VERDICT: CONFIRMED fully orphaned.** Repo-wide grep (code, tests, docs,
  CLAUDE.md) finds these 3 names ONLY in historical `.env.bak*` files
  (never read by any process) and in the frozen 22/07 snapshot doc
  (`docs/codex-aria-2026-07-22.md`, explicitly not an authority past its
  date). Zero references in any live source file. Yet all 3 remain SET in
  the real container env (`false`, confirmed live this session). Pure
  leftover config -- nothing reads them, nothing would notice if they were
  removed. Recommendation: drop these 3 vars from the prod `.env` on the
  next deploy (pure cleanup, no behavior change since nothing reads them) --
  not done here per this audit's read-only design, flagged as a concrete
  next step instead.
- [X] T010 [P] [US2] `ARIA_DAILY_TRADE_FLOOR_ENABLED` -- find its owner
  module and stated purpose (none found yet in CLAUDE.md/HANDOFF search this
  session); report if genuinely undocumented.
  **VERDICT: FALSE ALARM -- this audit's own P2 suspicion was wrong,
  corrected here.** `audit-scope.md`'s "no HANDOFF reference found yet" was
  a provisional guess never actually checked; `docs/HANDOFF_PAPER_TRADING.md`
  and `docs/HANDOFF_PIPELINE_MOMENTUM.md` both document this mechanism
  extensively (`paper_trader.run_daily_trade_floor_cycle`, a diagnostic
  floor that force-opens small tagged trades when behind the daily pace,
  relaxed-gate momentum path, multiple real bugs already found/fixed on it
  historically). Gate is OFF by default (confirmed live), last real run
  28/07 -- consistent with a mechanism used for a specific diagnostic test
  window (Item #100, 24h aggressive test) rather than abandonment. Not
  orphaned, not undocumented, not never-delivered -- it delivered exactly
  what it was built for during its test window and is now correctly idle.
  No action needed.
- [X] T011 [P] [US2] `ARIA_SCALPING_ONLY_SOURCING_ENABLED` -- confirm whether
  any live code path still reads this flag after the 18/08 v1-v9 retirement,
  or if it's a dead switch.
  **VERDICT: CONFIRMED dead code, already deliberately retired (not a new
  finding, but now cross-checked).** `docs/HANDOFF_PIPELINE_MOMENTUM.md`'s
  18/08 entry documents the removal explicitly: "`scalping_only_sourcing_enabled()`
  removed as dead code since no wallet can ever start with 'scalping' in the
  active roster anymore", and `test_coherence.py`'s gate registry was
  cleaned of this name in the same commit. Repo-wide grep confirms zero
  live callers -- the only remaining trace is the env var itself, still SET
  in the real container (`false`, confirmed live), same leftover-config
  shape as T009. Recommendation: same cleanup, drop it from the prod
  `.env` on the next deploy alongside the T009 batch.
- [X] T012 [P] [US2] `ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED` -- what has
  it actually produced since being armed (testnet transactions, rehearsal
  logs), against the 23/08 CLAUDE.md note that infra is testnet-only so far.
  **VERDICT: not orphaned/buggy -- correctly fail-closed on its PARENT
  gate, every attempt properly logged (unlike the T008 Sepolia finding).**
  `agent_wallet_tx_log` (full table, chain=`robinhood_testnet`): 4 attempts,
  24/08 22:31 through 25/08 10:36 (~4 ticks in 12h, consistent with the
  60min periodic-allowance cadence), ALL status `blocked`, reason
  identical every time: `"ARIA_HOMEMADE_AGENT_WALLET_ENABLED désactivé
  (fail-closed par défaut)"` -- this module's own gate is ON, but the
  shared wrapper gate it also goes through is OFF, exactly as its own
  docstring says it should behave. This is the opposite of T008: same
  "never succeeded yet" shape, but here every attempt left a clean,
  explicit trace, so nothing was hidden. This module is only 1 day old
  (created 24/08) -- too early to call it never-delivered; its actual
  purpose (rehearsing continuous unattended operation) hasn't been tested
  even once yet because the parent gate cuts it off before reaching that
  logic. Recommendation: if burn-in rehearsal is still wanted, enable
  `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` -- operator decision, not acted on
  here (real-capital-adjacent path, FR-004).

**Checkpoint**: every P2 row has a caller-count/gate-age verdict, not a guess.

## Phase 5: User Story 3 -- Give every survivor a success criterion (Priority: P3)

**Goal**: every component that survives P1/P2 with a "keep" verdict gets a
measurable success criterion and the place it's checked -- written from the
real measurement just taken, not guessed in advance of it.

- [X] T013 [US3] For every component marked KEEP in T002-T012, write its
  success criterion (metric + query/check location) directly into
  `docs/HANDOFF_AUDIT_LIVRAISON.md` alongside its verdict. Depends on
  T002-T012 being resolved first -- no criteria to backfill before then.

  Criteria (component -> measurable bar -> where to check):
  - **Polymarket paper (T004, KEEP as-is)**: no new criterion needed --
    already meets its design bar (win_probability>=0.85, 3-vote convergence,
    real closed P&L). Re-check quarterly: `COUNT(*) GROUP BY strftime('%Y-%m', opened_at)`
    on `polymarket_paper_position` should keep showing >=1 bet/week; a
    silent drop to zero for 2+ weeks would itself be a new finding.
  - **wallet_copy_shadow (T005, KEEP + wire a consumer)**: a wallet counts
    as a real "worth copying" verdict only once `closed_positions >= 10`
    AND `realized_pnl_usd > 0` AND `closed_unknown_exit_count` is under 20%
    of its total closures (else the sample is too thin or too tainted by
    unknown exits to trust) -- check via `wallet_copy_shadow.summary()`.
    Separately: wire the already-fixed `summary()` into a Telegram command
    or periodic report before the next audit pass, or this becomes a repeat
    of the same "nobody ever looked" finding.
  - **signal cascade falsifiability (T006, KEEP as an observer, don't wire
    to real filtering)**: only promote the triage criterion to an actual
    filter if `avg_return_validated_pct_no_top2 > avg_return_rejected_pct_no_top2`
    holds on BOTH the 24h and 7d windows simultaneously, with
    `enough_data=true` on both -- check via `signal_cascade_convergence.falsifiability_report()`.
    Currently false on both windows even outlier-free; re-check monthly as
    the sample (189 candidates so far) grows.
  - **candle_staleness_shadow (T007, criterion IS the missing analysis)**:
    graduate past shadow mode only once a forward-validation function
    exists AND shows `would_flag=1` observations correlate with a real bad
    outcome (cross-referenced against `wick_filter_shadow`/`ath_shadow` on
    the same contract+timestamp) at a rate meaningfully above the flagged
    rows' base rate (10.1%, full-table). No such function exists yet --
    writing it is the next concrete step, not measuring against it today.
  - **Sepolia autonomous pilot (T008, criterion is unmet, decision needed)**:
    the "proven pipeline before mainnet" bar requires
    `sepolia_autonomous_log` to show at least one real `outcome='ok'` swap
    -- currently impossible until `ARIA_ONCHAIN_ANCHOR_ENABLED`/
    `ARIA_LEDGER_ADDRESS` are wired (or the anchor step is explicitly
    descoped from this rehearsal). Operator decision, not this audit's call.
  - **Robinhood testnet rehearsal (T012, KEEP, needs its parent gate)**:
    "proves it keeps working under continuous operation" requires at least
    10 consecutive `status='ok'` rows in `agent_wallet_tx_log` (chain=
    `robinhood_testnet`) once `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` is armed
    -- currently 0/4 attempts have even reached that logic (all blocked
    upstream).
  - **daily_trade_floor (T010, KEEP, already proved its point)**: no new
    criterion -- it already delivered its diagnostic purpose during its
    24h test window (Item #100, late July). Re-arm only for a new,
    explicitly scoped test, not as a standing mechanism.

## Phase 6: Polish

- [X] T014 Write `docs/HANDOFF_AUDIT_LIVRAISON.md` (one `[STATUS] Subject /
  Date / Probleme / Solution` entry per audited component, per the CLAUDE.md
  HANDOFF format) and add it to CLAUDE.md's "Index des HANDOFF" in the SAME
  commit (test_handoff_file_indexed_in_claude_md enforces this).
  Done -- 11 entries + 1 incidental security note, `test_handoff_entries_use_valid_status_and_required_fields`
  and `test_handoff_file_indexed_in_claude_md` both green.
- [X] T015 For any live discrepancy found during T002-T012 (gate state vs
  documented state, a dead process, an orphaned consumer), open a
  `system_issues` entry so it survives past this audit's own report, per
  FR-003's reuse-existing-mechanisms requirement.
  Done -- 4 issues opened for the still-live, actionable divergences (bugs
  already fixed in-commit don't need one, the commit itself is the trace):
  #244 (CabalSpy orphaned consumer, high), #245 (x402 no third-party sale,
  warning), #246 (3 orphaned wallet-scoring env vars, info), #247
  (dip_recovery_shadow never enabled, info -- found during T016).
- [X] T016 Run `quickstart.md`'s resume/extend steps once against a component
  NOT in the original scope, to confirm the method actually generalizes
  before calling this feature done.
  **Done -- tested against `dip_recovery_shadow` (13/08, not in the original
  scope).** Followed quickstart.md's steps: found its class (shadow observer,
  same as candle_staleness_shadow), read its stated purpose in
  `heartbeat.py` (operator-proposed -30%/24h dip-buy signal, shadow-tested
  before ever building a live pocket), measured live (`summary()`: 0
  everywhere; `dip_recovery_shadow`/`dip_recovery_shadow_episode_state`: 0
  rows both; `heartbeat_state.json`: no `dip_recovery_shadow_cycle` entry at
  all, ever; `ARIA_DIP_RECOVERY_SHADOW_ENABLED` unset in the real
  container). **VERDICT: never delivered -- but a THIRD distinct shape from
  the ones found in P1/P2**: not a bug, not orphaned-after-a-retirement,
  not blocked by a parent gate -- built 13/08, wired into the heartbeat
  scheduler, and its own dedicated gate has simply never been turned on
  since. The method generalized cleanly to a component outside the original
  scope without any adjustment. Added to `audit-scope.md` as evidence this
  audit's scope is a floor, not a ceiling.

---

## Dependencies & Execution Order

- Phase 1/2: trivial, no real blocking work.
- Phase 3 (US1) and Phase 4 (US2) have no dependency on each other -- both can
  run in parallel, and every task within each phase is independent (different
  component, different query) -- all marked [P].
- Phase 5 (US3) depends on Phase 3+4 verdicts existing (T013 cannot start
  before at least one KEEP verdict lands).
- Phase 6 depends on Phase 5 (the HANDOFF should carry criteria, not just
  verdicts).

## Parallel Example

```
# All of Phase 3 can be delegated/run independently:
Task: "x402 seller real-sales count -- T002"
Task: "CabalSpy consumer-liveness check -- T003"
Task: "Polymarket paper cadence/volume -- T004"
Task: "agent_wallet_copy_shadow verdict-ever-cleared check -- T005"
Task: "signal cascade real-convergence count -- T006"
Task: "candle_staleness_shadow calibration-readiness check -- T007"
Task: "Sepolia pilot real swap success/failure count -- T008"
```
