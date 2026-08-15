# ARIA Roadmap — August 2026

> Dated snapshot (02/08/2026), not a living document to maintain indefinitely — to be
> revisited/revised at the weekly review or if context changes significantly.
> Built from facts verified in the database (`/opt/aria-data/aria.db`), the real code,
> CLAUDE.md, and persistent memory — cross-checked by two rounds of an adversarial review
> workflow (consistency + completeness, project doctrine) before operator validation.
> Deliberately **not reduced to a single financial axis** (profit, compound interest,
> revenue sources) — explicit operator decision of 02/08: the roadmap must cover the
> full breadth of the project.

**Overarching framework, valid for all 7 axes**: "ARIA first, token later" doctrine —
real performance → utility → identity → community → token. No secondary axis should
invert this order (e.g. rushing tokenization or external visibility before
performance and utility are proven).

---

## Overview — 7 parallel axes

1. **Performance** — the weekly +10% test, repeated until reliably validated; the
   quality each pocket demonstrates directly determines the amount of real capital it
   will receive next (see axis 1).
2. **Identity & presence** — voice, avatar, free memory (aria-brain).
3. **Security & robustness** — adversarial blind spot, governance guardrails,
   prerequisite before any real capital.
4. **Ecosystem & network** — Base, x402/Bazaar, potential tokenization, external
   visibility.
5. **Infrastructure & technical autonomy** — CDP, LLM, API dependency, wallet-scoring.
6. **Governance & hygiene** — backlog, HANDOFF, adversarial review mechanisms
   (currently partly broken — see axis 6), deployment cadence.
7. **Revenue sources** — one dimension among the other six, not the sole compass.

**Permanent override, takes priority over this entire sequence**: a security fix (flaw,
exposed secret, broken guardrail) deploys immediately, never held for a roadmap slot
(CLAUDE.md, "Deployment cadence").

---

## Axis 1 — Performance (weekly +10% test)

**Real state as of 02/08**: no `validated=1` week on `paper_weekly_cycle` to date
(only 3 rows — swing, vc, scalping_v6; the other pockets have never yet completed a
full cycle). Last completed cycle: swing -0.51% (9 trades, 22% winrate),
scalping_v6 +2.20% (18 trades, 78% winrate). **Explicit operator judgment (02/08):
disappointing on the pocket side for now.**

**Direct link to upcoming real capital**: the real pilot's cap is no longer fixed at
10-15$ — the direction under consideration is **3 distinct real Smart Wallets
(scalping / swing / vc), ~50$ each**, but their final size will depend on the
**quality each pocket demonstrates on paper**, not an amount decided in advance. A
pocket that keeps disappointing gets less (or nothing); a pocket that proves its
discipline can get more. So it is axis 1 — not a calendar decision — that sets the
real pace of this 5/7 axis.

**Tonight (02/08, ~21:50Z)**: all 4 pockets (scalping v1-v6, swing, vc, megacap) are
now sourcing in parallel — `ARIA_SCALPING_ONLY_SOURCING_ENABLED` disabled,
`ARIA_VC_POCKET_SOURCING_ENABLED` enabled, 3 fixes deployed (entry_atr_pct,
scalping-only wash-trading, DexScreener/B20 liquidity blind spot, CoinMarketCap
throttle, `_execute_trigger` race). Verification scheduled for 22:51Z.

**Week 1 (03-09/08)** — structural point to keep in mind: the swing/vc/
scalping_v6 reset lands on **08/08**, right in the middle of the window. The 09/08
review will judge a cycle barely ~1 day old under the corrected pipeline — don't read
it as a full week. Immediate priorities:
- Recheck v2/v4/v5 right now (already 28h at zero trades) rather than waiting 24-48h (#22).
- Diagnose the real wallet-scoring blockage (oscillating on 3-4 wallets, logs
  02/08 21:02-21:25) — not simple slowness, possibly a recurrence of the bug fixed on 23/07 (#32).

**Weeks 2-4**: diagnose → fix → observe cycle (18/07 doctrine, unchanged) — each week
judged on its own, no threshold of consecutive weeks set to date.

**Dated milestone this month — first external user (~13/08)**: operator plan to
onboard a first external user who copy-trades ARIA (~50$), blocked by the absolute
rule "no payment collection before lawyer validation." To be decided BEFORE the
deadline, not after (#54).

**End of month**: if the +10% is repeatedly validated, reopen the discussion on the
criterion for moving to more real capital — no figure is set today. Prerequisites to
address before any extension: disable Solana, decide on the risk circuit-breakers
currently disabled in paper mode, resume the agent-wallet hardening backlog (#49).

---

## Axis 2 — Identity & presence

Operator vision from 15/07, never built but never forgotten: beyond being an investor,
ARIA should eventually become a recognizable presence — voice, avatar, X presence —
with a taste boundary already carved in stone (never suggestive/nude/sexualized,
10/07).

- **This month, diligence only, no building**: realistic TTS stack (cost,
  latency, quality) + **talking avatar (HeyGen)** + **X posting cadence gated by
  human review + extended kill-switch** (#45/#53 — the three pillars of the vision banked on
  22/07, not just voice). Decide after diligence whether a prototype is warranted.
- **aria-brain** (free memory, one page/day) remains active — 99% real / 1%
  explicitly-marked speculation doctrine unchanged, no action required this month except
  on incident.
- **`knowledge/dna.yaml`**: architectural tension never settled since 21/07
  (multi-anchor identity/memory suggested by external research vs. merging into a single
  file as the operator wants) — to be settled before any future refactor (#50).

---

## Axis 3 — Security & robustness

Permanent VPS Research mandate (15/07): catalogs and verifies that the strengths unique to
an AI-trader are TRULY exploited, and that the weaknesses unique to an AI are found
and then addressed — until the operator judges ARIA ready.

- **Adversarial/prompt-injection vulnerability on-chain** — a malicious project that
  would craft its name/site/metadata to bias ARIA's LLM judgment. Tested
  only at n=2 prompts on 17/07 (#117). This month: expand the sample,
  document, address any real weakness found (#44).
- **Agent-wallet hardening backlog #215-#230, never resumed** — directly
  relevant now that 3 real Smart Wallets are being considered (axis 1). Prioritize #224
  (ERC-20 allowance never unlimited + pre-signature simulation before any real swap) and
  #221 (audit that nothing can widen the pilot's swap-only scope) (#49).
- **Prerequisite before extended real capital**: paper risk circuit-breakers currently
  disabled (the commit itself says "MUST be revisited before any real-capital
  transition"); Solana to be disabled before any extension of the pipeline toward
  real execution beyond the already Base-only agent-wallet pilot.

---

## Axis 4 — Ecosystem & network

- **Base/Jesse Pollak watch** (ongoing since 16/07): decision #199 (which
  x402 resource to pay for first — Cybercentry, 0,02$/call) still awaiting an
  operator ruling (#36).
- **ARIA tokenization diligence, dig deeper into Clanker** (surface diligence from 27/07):
  dig into the exact LP lock mechanics and real governance before any decision (#41).
- **Visibility/recognition in the AI-agent crypto ecosystem — general ambition, no
  named target** (clarified by the operator on 02/08: "ai16z" was only an image,
  not a literal objective). Useful factual context kept in mind, verified 02/08:
  "ai16z" no longer exists under that name since January 2025 (rebranded **ElizaOS**, at
  the request of a16z the real VC) and has been subject to an active class-action since
  22/04/2026 (fraud allegation, 2,6Md$) — a landscape player worth knowing, not a
  target to aim for at this precise moment given the turbulence. Concrete direction: build
  recognition through proof (public track record, performance, presence — axes 1/2),
  not through a calculated alignment with a specific player.
- **Monad** (candidate chain) — EVM/GoPlus/DexScreener OK, but the unofficial Blockscout
  remains a real blocker. To be rechecked periodically (#52).

---

## Axis 5 — Infrastructure & technical autonomy

- **LLM migration to Claude (Haiku 4.5 + Sonnet 5)** — direction settled, gate now
  split by role (`ARIA_LLM_ANTHROPIC_ROUTING_ENABLED` / `..._TRADING_ENABLED`, commit from
  02/08). Sequence before any real flip: dedicated OpenRouter account for DeepSeek, verify
  `ANTHROPIC_API_KEY` in prod, general gate first (observe), trading gate last (#48).
- **CDP Smart Account (Spend Permissions + Paymaster)** — direction settled, ~10 days of
  design already done. Next step (`eth_account.BaseAccount` wrapper) requires
  hardware-in-the-loop sessions with the operator's physical Tangem — to be planned
  explicitly (#35). Becomes more concrete now that 3 real Smart Wallets are
  being considered (axis 1).
- **unified_entry.py** (amended #194, unified VC/Swing screen) — CODE, dormant since
  22/07, half done. Decide this month: resume, or explicitly freeze (#33).
- **Wallet-scoring toward the ~500 threshold** — 9 unique wallets / 775 rows as of 02/08, well
  below. Linked to axis 1's blockage diagnosis (#40).
- **API dependency reduction** — identify ONE concrete candidate this month (#42).

---

## Axis 6 — Governance & hygiene

**Urgent, found today**: the Devil's Advocate mechanism (`scripts/devils-advocate-
review.sh`, post-push code review) has been **broken since 26/07** (OpenRouter account
out of funds, HTTP 402) — and the same shared account also feeds the adversarial trading
judge (`trade_devils_advocate.py`/`trade_loss_batch_review.py`). Both governance
guardrails have been silently out of service for a week. Operator action required
first (top up the account), then the migration already decided but never tracked toward
Gemini for the code hook (#47).

- Backlog brought back to 10-15 pending items on 02/08 (09/07 norm) — to be replenished as soon as it
  drops back down.
- HANDOFF per component: active practice, verify that no new component remains
  without a dedicated file.
- Direct vs batch deployment cadence: 18/07 doctrine already applied without
  incident this month (3 fixes grouped into a single deployment on 02/08).

---

## Axis 7 — Revenue sources (one dimension among the other six)

- **x402 seller** (`/api/x402/walletscore`) — complete code, dormant. Only remaining
  step: the operator's own testnet self-payment test.
- **Expand the sellable catalog** — research/scoping only this month. **Real
  contractual blocker found**: none of the providers used (GoPlus, Blockscout,
  CabalSpy, TwitterAPI.io) explicitly authorizes reselling derived data —
  GoPlus and CabalSpy are flatly restrictive. Write to obtain written permission
  before any expansion (#51).
- **Mindshare Rewards** — the revenue split is **already decided** (5-8% Mindshare, 4-7%
  buybacks/burn preferred, remainder → treasury/dev/compute/voice/avatar/infra, hard cap
  15% redistribution) — what remains open is ONLY the automated multi-recipient
  outgoing payment mechanism (who validates, cap, anti-abuse) (#43).

---

## Open branches — generative brainstorm (02/08)

At explicit operator request ("more imagination"), a workflow dedicated to pure
idea generation (not fact verification) — two distinct angles, the same
boundaries as the "multiply the branches" doctrine from 10/07 (never anything that
would touch `wallet_guard`/`permission_mode`/real capital/secrets).

**6 high-potential leads, near-zero first-step cost** (all built on an already
existing, code-verified brick, never a from-scratch project):

1. **Public registry of rejections** — expose (1-week delay) rejected candidates + their
   already-computed counterfactual (`/counterfactual`). Materializes "proof before promise."
   Zero competitor observed (aixbt and other agents only show their good calls) (#55).
2. **ARIA vs. market differential index** — contextualizes the weekly report (axis 1)
   against a simple benchmark, instead of an isolated figure (#56).
3. **Desks in competition** — narrate scalping/swing/vc as distinct
   teams in the weekly report, reuses a segmentation already in the database (#57).
4. **`x402_trust_score.py` as a 2nd sellable product** — complete, tested engine, never
   wired into prod; proprietary computation (not a resale of third-party data), so it
   **unlocks an x402 product without waiting on GoPlus/CabalSpy permission** (#51) (#58).
5. **`pump_dump_autopsy.py` → aria-brain** — text already produced, never pushed to
   free memory. Lowest cost on the whole list (#59).
6. **Public "Wallet Passport"** — combines 4 identity/reputation bricks that never
   talk to each other (Farcaster, Basenames, CabalSpy, smart_money) into a narrative
   sheet, zero new API cost, natural teaser for x402 walletscore (#60).

**Other banked leads, not yet scoped** (higher cost or external dependency,
kept for a future iteration): auto-generated Trade Cards (reuses
`chart_render.render_scenario_png`), Replay Mode for a past decision (`thesis_journal`/
`truth_ledger`), gated subscriber terminal to query ARIA on a specific Base token,
smart-money watchlist as a visible subscriber perk, Kalshi client (`blockrun_kalshi.py`,
built, zero callers) as a 2nd prediction market, `insider_wallets.py`/
`deployer_history.py` as a distinct x402 product, expanding `arena_signal.py`
beyond BTC alone, `liquidity_rotation.py` to prioritize the evaluation order of
candidates (never the decision thresholds), full-graph Sybil clustering (research
already done on 15/07, never implemented — the most expensive item on the list).

---

## Active backlog (24 pending items as of 02/08, TaskList #22/#32-#60)

| # | Subject | Axis |
|---|---|---|
| #22 | Recheck v2/v4/v5, remove if still inactive | 1 |
| #32 | Diagnose blocked wallet_scan_queue | 1/5 |
| #33 | Decide the fate of unified_entry.py | 5 |
| #34 | Restore Polymarket paper cadence | 6 |
| #35 | Schedule dedicated Smart Account CDP session | 1/5 |
| #36 | Settle Base watch #199 (x402 resource) | 4 |
| #37 | Reminder: disable Solana before real capital | 3 |
| #38 | Decide the fate of the disabled circuit-breakers | 3 |
| #39 | Track the 08/08 weekly reset (truncated cycle) | 1 |
| #40 | Recheck wallet-scoring progress toward 500 | 5 |
| #41 | In-depth Clanker diligence | 4 |
| #42 | Identify one concrete API-dependency-reduction candidate | 5 |
| #43 | Decide Mindshare Rewards payment mechanism | 7 |
| #44 | Dig into on-chain adversarial vulnerability | 3 |
| #45 | Voice stack diligence for ARIA | 2 |
| #46 | Expand the sellable x402 catalog | 7 |
| #47 | Top up the OpenRouter account (Devil's Advocate broken) | 6 |
| #48 | Sequence the Anthropic LLM routing flip | 5 |
| #49 | Resume the agent-wallet backlog #215-#230 | 1/3 |
| #50 | Settle the dna.yaml architectural tension | 2 |
| #51 | x402 resale permission (GoPlus/Blockscout/CabalSpy) | 7 |
| #52 | Revisit Monad as a candidate chain | 4 |
| #53 | Scope HeyGen avatar + gated X cadence | 2 |
| #54 | Decide on the custody portal, first external user | 1 |
| #55 | Scope the public registry of rejections | 2/4 |
| #56 | Scope the ARIA vs. market differential index | 1 |
| #57 | Scope the "desks in competition" narration | 2/4 |
| #58 | Wire x402_trust_score.py as a 2nd sellable product | 7 |
| #59 | Push pump_dump_autopsy.py to aria-brain | 2 |
| #60 | Scope the public "Wallet Passport" | 2/4/7 |
