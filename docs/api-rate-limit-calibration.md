# API rate limit calibration — full inventory (07/21)

> Referenced from `CLAUDE.md` (Permanent standards, "Throughput calibrated to 90% of real
> capacity"). Explicit operator decision, 07/21: every external API client must be
> throttled to ~90% of its true sustained capacity — neither too cautious nor too
> aggressive. Method: official provider docs first, **empirical verification in a
> controlled burst** when the docs are absent, ambiguous, or contradictory (needed twice
> that day — GoPlus and DexScreener). Never a guessed or from-memory figure.
>
> This file is a reference inventory, updated on each recalibration. It does not
> replace the source comment next to each throttle constant in the code — the two
> must stay consistent.

## Tier 1 — momentum pipeline (high volume, critical trading path)

| Service | Real throughput (source) | Confidence | Current throttle | 90% target | Action |
|---|---|---|---|---|---|
| GeckoTerminal (Demo key) | 30 req/min documented, real sustained cap lower under multi-chain load (walked back to 15/min after two 429 storms, 08/02) | Confirmed (official docs, researched 07/19) — but the documented figure never held under continuous multi-chain load in practice | 0.8s (75/min) | n/a — see note below | **04/08: widened 5x (4.0s→0.8s), explicit operator decision, exceeds the documented 30/min figure.** Premise re-examined same day: `DEFAULT_CHAINS` has been Base-only since 27/07 (the "Base+Ethereum" load behind every prior widening no longer applies), and the OHLCV fallback cascade (DexPaprika/CoinMarketCap/Mobula/Codex.io/DexScreener/Dune) was confirmed live absorbing a GeckoTerminal 429 cleanly (Mobula fallback succeeded within ~300ms, twice). Not the burst-controlled empirical measurement this file's own doctrine calls for (task #41, still open) — an operator-directed live test leaning on the confirmed cascade safety net. Revert to 4.0s if a sustained (not isolated) 429 rate shows up post-deploy. |
| DexScreener (profiles/boosts) | 60 req/min | Confirmed verbatim (official docs) | none → **1.111s implemented** | 1.111s (54/min) | Done (07/21) |
| DexScreener (pairs/tokens/search) | ~300 req/min | Likely, not confirmed verbatim (docs + a burst of 25 req/1.1s succeeded without error, but the real ceiling was never actually reached) | none → **1.111s implemented (same client throttle, no split)** | 0.222s (270/min) would be the target IF confirmed, but a single entry point (`_get_json`) serves every endpoint in the module — calibrated to the lowest, only-confirmed figure (60/min) rather than risk exceeding the profiles/boosts endpoints. | Done (07/21), deliberately conservative |
| GoPlus (token_security) | **150 CU/min confirmed on the real account dashboard** (gopluslabs.io/dashboard, Free tier) -- but GoPlus bills PER TOKEN, not per call: Token Security API = 15 CU/token (EVM), 30 CU/token (Solana). `get_token_security()` queries a single contract per call -> 1 call = 15 CU on Base -> **10 real req/min**. This explains, in hindsight, the same-day empirical test (blocked on the 11th request = 150/15 = exactly 10 tokens) | **Confirmed at the highest-trust level (account dashboard)** | 0.5s (120/min) initial -> 1.212s (miscalibrated, based on a misread of the empirical test) -> **6.667s (~9/min)** | 6.667s (9/min) | **Corrected twice the same day -- the per-token, not per-call, billing structure was the blind spot** |
| Blockscout Pro | 5 req/s | Confirmed (docs + `x-ratelimit-limit:5` header verified live) | 0.2s (100%, zero margin) | 0.222s (4.5/s) | Slight slowdown |
| Blockscout free (`base.blockscout.com`, fallback path, inactive while the Pro key is valid) | 3 req/min documented for `api.blockscout.com` (different product, not confirmed applicable to `base.blockscout.com`) | Not confirmed for this specific domain | 0.35s (171/min) | Unknown | Dead path in practice — retest if the Pro key were ever to lapse |
| CoinMarketCap Pro (Basic) | 50 req/min | **Confirmed live** via `/v1/key/info` on the real configured key | 1.5s (40/min) | 1.333s (45/min) | **Tightened 08/02** (explicit operator decision): 18 HTTP 500 errors (not 429 -- server error, not our own throughput being exceeded) observed on `/v1/dex/token/pools` over ~4 min. A precautionary reduction, not proof that throughput was the cause -- to revisit if the 500s persist after this change. |
| CoinGecko (Demo, `/simple/price`) | 100 req/min, 10,000 credits/month | Confirmed (2 independent official sources) | 2.2s (27.3/min, 27% used) | 0.667s (90/min) | **Speed-up** (verify empirically before deployment, given the doc/reality gap already observed elsewhere) |
| Mobula | 1 req/s, 10,000 credits/month | Confirmed (official docs) | 1.05s (95.2%) | 1.111s (0.9/s) | Slight slowdown |

## Tier 2 — x402 and secondary data providers

| Service | Real throughput (source) | Confidence | Current throttle | 90% target | Action |
|---|---|---|---|---|---|
| Dune Execute SQL (Free) | 15 req/min (low, binding limit) + 40/min (high limit, separate counter) | Confirmed (official docs) | none | 4.44s (13.5/min) | New throttle |
| Tavily Search | **07/22, corrected against the real (billing) dashboard: NO req/min rate limit published anywhere** — the real structure is a MONTHLY credit budget ("Researcher"/free plan = 1000 credits/month, 1 credit/basic search, 2/advanced). The table's earlier "Dev=100/min, Prod=1000/min" figure was a mix-up (key type confused with subscription plan), never confirmed against a real statement | **Corrected (real dashboard, 07/22)** — the earlier "confirmed" entry was wrong | 0.5s (120/min) — **a figure with no real basis, no known throughput to respect** | — | Not a throughput matter at all — see the "monthly usage" note below instead |
| RugCheck.xyz | No published limit | Absence confirmed (official Swagger verified) | none | — | Reactive backoff only, unknown capacity |
| Farcaster/Warpcast | No published limit | Absence confirmed | none | — | Reactive backoff only, unknown capacity |
| DefiLlama (free) | No published figure (unlike the paid tier, which is 1000/min) | Absence confirmed | none | — | Reactive backoff only, unknown capacity |
| Polymarket Gamma | 4000 req/10s overall, 500/10s `/events`, 300/10s `/markets` | Confirmed (official docs) | 2.0s (30/min) — well under the real ceiling | 0.0222s (2700/min on `/events`) | Already a huge margin, no urgency — usage too low for it to matter |
| twit.sh / Otto AI / Cybercentry (x402) | No published limit — the only real brake is per-call cost | Absence confirmed (all 3) | — | — | The `x402_budget.py` cap ($5/week) already serves this role, nothing to add |
| CDP x402 Discovery/Bazaar | No official figure published; an unofficial third-party report suggests a lower threshold than the generic CDP one (600/10s, likely not applicable) | Low — a single report, not reproduced | none | — | Unknown capacity, current usage low/dormant |

## Tier 3 — low-volume official APIs

| Service | Real throughput (source) | Confidence | Current throttle | 90% target | Action |
|---|---|---|---|---|---|
| GitHub REST (fine-grained PAT) | 5000 req/h | Confirmed (official docs) | none | 0.8s | Actual usage far too low for it to matter, no urgency |
| Telegram Bot API (private chat) | 1 msg/s per chat | Confirmed (official docs) | to verify (`host_hooks.check_rate_limit`, scope not confirmed Telegram-specific) | 0.9s | Check whether a notification spike could ever exceed this pace |
| Virtuals Protocol API | No published limit | Absence confirmed | none | — | Unknown capacity |
| x.ai Management API | No published limit for the API itself | Absence confirmed | none | — | Hourly usage, unknown capacity, no real risk |
| Clanker API | No published limit | Absence confirmed | none | — | Unknown capacity |
| Blockchain.info | Historical "1/10s" figure not found in current docs | Not confirmable | none | — | Practically zero usage |
| Base public RPC (`mainnet.base.org`) | No published figure — official docs explicitly discourage production use, recommend a dedicated provider | Absence confirmed + official recommendation not to rely on it | — | — | The feature that would use it (Virtuals graduation) is already gated OFF — recommended to route through a dedicated RPC provider if ever enabled |

## Empirical verification method used (to reuse)

For GoPlus and DexScreener (docs absent/contradictory), a burst of 20-25 back-to-back
requests (no artificial delay) against the endpoint actually used in production, with
varied contract addresses (never the same one, to avoid a cache skewing the test).
Observed: status code, response body (some providers like GoPlus signal their rate
limit via an HTTP 200 with an error code in the body, not a real HTTP 429), and for
GoPlus a recovery test (wait, then new spaced-out requests) to measure how long the
quota takes to replenish.

## Two families of constraint, never conflated (07/22, Blockscout + Tavily discovery)

This file long treated every limit as a THROUGHPUT (req/s or req/min, guarding against
a 429 on a burst). Two providers reveal a DIFFERENT family: a CUMULATIVE BUDGET over a
period (credits/day for Blockscout Pro, credits/MONTH for Tavily) — a "wise" throughput
does nothing to protect against exhausting this budget if the total VOLUME exceeds what
is allocated for the period. Worse, for Tavily ("Researcher"/free), the provider's docs
explicitly confirm "unused credits do not roll over to the next month" -- an unspent
monthly budget is capacity permanently lost, never carried forward. Two distinct
operational implications: a budget in CREDITS/DAY (Blockscout) deserves a proactive
throttle like the one already built (`blockscout_credit_budget.py`) to never EXCEED it;
a budget in CREDITS/MONTH with end-of-period loss (Tavily) raises the opposite
question -- making sure genuinely available usefulness isn't left unused for lack of
wiring, never by forcing artificial, valueless consumption (needless noise, memory
pollution). Reflex to generalize: before calibrating throughput for a new provider,
FIRST check its dashboard/billing docs to see whether it's an instantaneous rate, a
cumulative budget with reset, or a cumulative budget WITHOUT rollover -- the three are
calibrated differently.
