# HANDOFF — Audit livraison (components that never delivered their expected result)

> **Public repo — never a real IP/secret/token/key/personal email in clear here.** Variable names OK (e.g. `GOPLUS_APP_KEY`), their values never.

> Format: `[STATUS] Subject` / `Date: YYYY.MM.DD / Problem: ...` / `Solution: ... — file (hash)`.
> `[STATUS]`: DEPLOYE / CODE (tested, not deployed) / CONFIG (no commit) / ETAT ACTUEL.
> Full detail, measurement method, and resume path: `specs/001-audit-code-sans/`
> (spec.md/plan.md/research.md/audit-scope.md/tasks.md/quickstart.md). This file is the
> delivered synthesis, not the working log.

Origin (25/08): the wallet-scoring removal showed that a component can run for months,
burn a real rate-limit budget, get rewired ~50 times, and NEVER have delivered its
expected result (a qualified smart wallet) — with no test or code review ever catching
it, because the code did exactly what it was written to do, just not what it was built
for. This audit asks the same question of 11 other active mechanisms.

[ETAT ACTUEL] Subject: x402 seller -- never a real third-party sale
Date: 2026.08.25 / Problem: `ARIA_X402_SELLER_ENABLED`/`_MAINNET` are live in prod, but
`x402_revenue_log` (full table) holds only 2 rows ever -- same payer address, 3h apart on
05/08, reads as an operator smoke-test, never a real third-party payer. Zero sales in 20
days. Zero listing on the x402 Bazaar confirmed live via WebSearch.
Solution: operator decision needed -- invest in discoverability (Bazaar listing, #297)
or pause the mainnet gate until a discovery channel exists. No action taken here (audit
read-only, real revenue path).

------------------------------------------------------------

[ETAT ACTUEL] Subject: CabalSpy sourcing -- orphaned since the wallet-scoring removal, still running
Date: 2026.08.25 / Problem: `cabalspy_kol_wallets` holds 1183 wallets, all from ONE
single sync on 20/08 (`sourced_at` identical), never repeated since. Yet the heartbeat
cycle ran again today (25/08 11:11), spending its API budget daily. Zero real caller of
`catalogued_wallets()` outside the module itself -- exactly the wallet-scoring shape,
this time caught in 5 days instead of months.
Solution: operator decision -- pause `ARIA_CABALSPY_SOURCING_ENABLED` until a real
consumer exists. No action taken here.

------------------------------------------------------------

[ETAT ACTUEL] Subject: Polymarket paper trading -- delivered, cadence now verified
Date: 2026.08.25 / Problem: CLAUDE.md flagged the real cadence/volume as "never
actually verified". Full table (9 positions, ever): first bet 30/07, last 22/08, 3
distinct markets, against 426 rows in `polymarket_judgment_log` (~2.1% judgment-to-bet
conversion). 3 closures with real, code-verified P&L (+100% each).
Solution: none needed -- ~2-3 bets/week cadence is consistent with the design's high
selectivity bar (probability>=0.85 + 3-vote convergence), not a stalled mechanism.
Closes the CLAUDE.md gap.

------------------------------------------------------------

[CODE] Subject: wallet_copy_shadow -- survivorship bias on no-exit-price closures
Date: 2026.08.25 / Problem: `summary()` silently excluded any closed position with a
NULL `exit_price_usd` (42% of the 151 real closures, 64 rows) from `realized_pnl_usd`/
`closed_positions` -- same shape as the 17/08 PnL survivorship bug. The "worth copying"
signal also rests almost entirely on LATENT PnL (up to +$176k unrealized on one wallet)
rather than realized (~+$933 net across 151 closures, all wallets combined). Side
finding: `summary()` has ZERO consumers (no Telegram, no heartbeat, no API).
Solution: `summary()` now exposes `closed_unknown_exit_count` separately (never a
fabricated price, but no longer invisible). 2 new regression tests (18 total in the
module, all pass). Wiring a real consumer is recommended, not done here.
— `wallet_copy_shadow.py`, `test_wallet_copy_shadow.py` (2a7efa57)

------------------------------------------------------------

[CODE] Subject: signal cascade falsifiability -- verdict skewed by an outlier, fixed (final verdict unchanged)
Date: 2026.08.25 / Problem: `falsifiability_report()` decided its verdict ("useful
criterion" vs "not better than chance") from the RAW average forward return -- a single
rejected candidate with a +1,609,067% return (likely artifact) inflated the "rejected"
bucket's average to 38340% (7d window), without ever applying the project's own
statistical guardrail (retest minus the top 1-2 before concluding).
Solution: `avg_return_*_pct_no_top2` computed and used to decide the verdict. Manual
recompute: rejected still averages ~21-29% vs validated's ~7-10% even outlier-free -- the
mechanism's native verdict ("not better than chance, don't wire it into ARIA") still
holds, now for the right statistical reason. 1 new test (30 total, all pass).
— `signal_cascade_convergence.py`, `test_signal_cascade_convergence.py` (074292f0)

------------------------------------------------------------

[ETAT ACTUEL] Subject: candle_staleness_shadow -- plenty of data, analysis never written
Date: 2026.08.25 / Problem: 37,717 observations over 15 days (10/08-25/08), clearly
enough to calibrate a real threshold (10.1% flag rate, full table). But the
"forward-validation pass" the code itself promised ("for the future forward-validation
pass") was never written -- no correlation between `would_flag=1` and a real bad price
outcome has ever been measured.
Solution: write the validation pass (cross-reference `wick_filter_shadow`/`ath_shadow`
on the same contract+timestamp) before this can graduate past shadow mode. Not done here
(outside this audit's read-only scope), but now a concrete, scoped next step.

------------------------------------------------------------

[CODE] Subject: Sepolia autonomous pilot -- silent failure every cycle, fixed
Date: 2026.08.25 / Problem: `sepolia_autonomous_log` holds 0 rows, ever, despite a
heartbeat cycle running daily. Real cause: `anchor_enabled()`/`ledger_address()` are
unset, so every cycle exits via `skipped_no_ledger` -- BEFORE ever calling `_insert_log`,
contradicting the function's own docstring ("logs EVERY round"). No testnet swap was
ever attempted despite `ARIA_SEPOLIA_SWAP_ENABLED=true`.
Solution: `skipped_no_ledger` now writes a real log row (unlike `skipped_paused`/
`skipped_disabled`, deliberate stable OFF states, left untouched). 1 new test (25 total,
all pass). Wiring the real anchor is an operator decision before this pilot can claim
any proven testnet swap.
— `sepolia_autonomous.py`, `test_sepolia_autonomous.py` (7952fed0)

------------------------------------------------------------

[ETAT ACTUEL] Subject: 3 orphaned wallet-scoring gates (`ARIA_WALLET_SCAN_QUEUE_ENABLED`, `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED`, `ARIA_SMART_MONEY_LEADERBOARD_ENABLED`)
Date: 2026.08.25 / Problem: zero reference anywhere in live code/tests/docs -- only in
historical `.env.bak*` backups and a frozen 22/07 snapshot doc. Yet all 3 remain set (to
`false`) in the real container.
Solution: recommended removal from the prod `.env` on the next deploy (pure cleanup, no
behavior change since nothing reads them). Not done here.

------------------------------------------------------------

[ETAT ACTUEL] Subject: daily_trade_floor -- this audit's own false alarm, mechanism is sound
Date: 2026.08.25 / Problem: suspected "undocumented" by this audit's own initial scope
-- suspicion never actually checked, corrected here. In reality extensively documented
(`HANDOFF_PAPER_TRADING.md`/`HANDOFF_PIPELINE_MOMENTUM.md`), gate OFF by design after its
diagnostic test window (Item #100, late July), last run 28/07.
Solution: no action -- delivered exactly what it was built for during its test window,
correctly idle since.

------------------------------------------------------------

[ETAT ACTUEL] Subject: ARIA_SCALPING_ONLY_SOURCING_ENABLED -- dead code already retired, orphaned env var
Date: 2026.08.25 / Problem: the code that read this gate was removed 18/08 during the
scalping v1-v9 retirement (already documented). Only the Docker env var itself remains.
Solution: same cleanup as the 3 wallet-scoring gates (T009), to bundle in the same
`.env` cleanup deploy.

------------------------------------------------------------

[ETAT ACTUEL] Subject: Robinhood testnet rehearsal -- sound, blocked by its parent gate
Date: 2026.08.25 / Problem: 4 attempts (24/08-25/08), all `blocked`, identical explicit
reason: `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` disabled (fail-closed by default). Unlike
the Sepolia case (T008), every attempt is cleanly logged -- not a bug, the module is 1
day old and simply hasn't reached its own rehearsal logic yet.
Solution: operator decision -- enable `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` if continuous
burn-in is still wanted. No action taken here (real-capital-adjacent path).

------------------------------------------------------------

[ETAT ACTUEL] Subject: dip_recovery_shadow -- built 13/08, never enabled
Date: 2026.08.25 / Problem: found during the method's generalization test (T016,
outside this audit's original scope) -- an operator-proposed entry signal (-30%/24h + -5%
stop), wired into the heartbeat scheduler, but its dedicated gate
`ARIA_DIP_RECOVERY_SHADOW_ENABLED` has never been turned on: 0 rows in both its tables,
never a single entry in `heartbeat_state.json`. Neither a bug nor a post-retirement
orphan -- simply never activated since being built 12 days ago.
Solution: operator decision -- enable the gate if the shadow test is still wanted, or
confirm this is a deliberate abandonment. No action taken here.

------------------------------------------------------------

## Incidental security note (25/08)

While checking the Sepolia gate live (T008), a `docker exec aria-api env | grep -i
SEPOLIA` command accidentally printed `ARIA_SEPOLIA_PRIVATE_KEY` in clear text to the
terminal -- a violation of the project's absolute "never display a secret via Bash"
rule, even though this is a testnet-only key with no real funds. Not recopied, stored, or
reused; flagged to the operator live with a rotation recommendation as a precaution.
