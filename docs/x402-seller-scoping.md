# x402 SELLER — scoping & decision framework (ARIA sells her own judgment)

> **Repo PUBLIC — no IP/secret/token/key/personal email in clear here.**

Status at 2026-07-23: **scoped, NOT built, blocked on three operator/legal decisions
below.** This is the mirror of everything built so far: every real-money mechanism to
date governs ARIA *spending* capital (wallet_guard, Sepolia rehearsal, agent-wallet
pilot). This is the first time ARIA would *receive* capital (incoming x402 payments for
API access) — a new governance category, to treat with the same rigor as the
agent-wallet pilot diligence, never a quick bolt-on.

## The core idea

Now that ARIA wires ~12 external data APIs, flip from x402 *buyer* to also x402
*seller*: expose her own composite JUDGMENT (never a third-party raw pass-through) as a
paid pay-per-call endpoint for other agents. The moat is the aggregated analysis, not
the API access — consistent with the project thesis ("le moat = l'analyse prouvée") and
the Base 2026 pivot (trading/paiements/agents IA).

## What is safe to sell TODAY vs blocked

ARIA computes these herself, but they are built ON providers whose ToS matter:

| Signal | Sellable today? | Reason |
|---|---|---|
| `/walletscore` composite (percentile + confidence) | **YES** (only one) | ARIA's own blended score over multiple sources, already cached, no single provider's raw output exposed |
| `safety_screen` composite security score | BLOCKED | built on GoPlus — GoPlus ToS explicitly prohibits commercial use of "original data" without written permission + mandatory "Powered by GoPlus" attribution |
| GitHub/Website/Docs/X substance scores | BLOCKED | built on Tavily + TwitterAPI.io — TwitterAPI.io self-describes as an unofficial scraper (X Corp Developer Agreement exposure = structurally riskiest of the six) |
| smart-money convergence, deployer reputation, copy-trading flag | BLOCKED | built on Blockscout (ToS unclear, two possibly-applicable docs, one bans resale without written consent) + CabalSpy (base license is "personal, non-commercial") |

**Provider ToS is THE actual blocker, not a formality.** None of the 6 providers ARIA
depends on (GoPlus, DexScreener, Blockscout, CabalSpy, TwitterAPI.io, Tavily) explicitly
permits reselling a derived score in writing. Severity: CabalSpy (non-commercial base
license) and GoPlus (explicit prohibition + attribution-or-sue) are the hardest;
Blockscout the most unclear; TwitterAPI.io the riskiest (X Corp exposure); DexScreener
and Tavily read most favorably but still no affirmative written permission found.

## Cost & effort (researched, near-zero money cost)

- **Platform fees**: near-zero. Coinbase CDP facilitator free up to 1,000 settlements/mo
  then $0.001/settlement; fully free alternatives exist (xpay zero-fee gas-sponsored,
  PayAI 10k free/mo). The receiving wallet needs no special "merchant" feature — any EVM
  address works, never signs, never spends gas to receive (EIP-3009 gasless transfer
  signed by the *buyer*).
- **Engineering**: the official `x402[fastapi]` Python package exists (~30-40 line
  middleware) but is **Alpha** (one v1→v2 breaking rewrite already, release days old at
  research time) — pin the version, test against the testnet facilitator before mainnet.
  Honest estimate: "days, several moving pieces," dominated by the new governance, not
  the middleware.

## Competitive landscape (real but thin)

Niche already populated: Cybercentry (known to ARIA since 16/07), Rug Munch Intelligence
(security+social+LLM, 19 endpoints, $0.02–$2), and notably **Nansen** already selling
smart-money data via x402 ($0.01/$0.05). Typical pricing: raw lookups $0.001–$0.02,
composite judgments $0.05–$0.20, elaborate reports $0.50–$2. **No competitor bundles what
ARIA builds internally** (security + smart-money + Web/Docs/X substance + deployer
reputation in ONE synthesized thesis) — the real differentiation angle. Market thin
(~$28k/day real volume vs $7B ecosystem valuation — speculative noise, no proven large
paying-buyer base yet for this judgment category).

## Pricing model (three natural tiers, price on real COGS never guessed)

1. **Cached composite lookup** (cheapest, near-zero margin cost) — a wallet score or
   substance score already computed and cached → high margin.
2. **Forced fresh re-scan** (pricier) — triggers a real network scan (Tavily crawl,
   TwitterAPI.io, GoPlus, Blockscout, one LLM call) → price must cover real per-call cost
   + margin.
3. **Full consultation** (priciest) — the complete `/vc`-equivalent synthesized thesis.

Price each tier on its REAL cost-of-goods, verified (same "verify before affirming"
doctrine as API throttling), never a guessed number.

## Two design constraints (settled 23/07, build them in from day one)

- **Anti-front-running delay on LIVE buy-trigger alerts: minimum 8h, anchored at signal
  TRIGGER/execution time (not detection time).** If a paying customer got the same alert
  that feeds ARIA's own buy decision and acted faster, their buy would move the price
  against ARIA before she enters. Anchoring the 8h clock at trigger time means her
  position is by construction already open before the countdown starts. Applies ONLY to
  live buy-trigger signals (momentum/VC thesis on a specific contract), NOT to static
  diagnostic lookups (a cached wallet/substance score doesn't tip a trade happening now).
  This risk only truly bites once REAL capital sits behind the alerts (not the current
  paper test) — but cheap to design in now.
- **Substance-cache TTL policy (differentiated, NOT a flat number)** — see backlog #40:
  never scanned → full scan; within TTL → serve cache; past TTL → fresh re-scan that
  refreshes the cache AND delivers real-time to the paying account. TTLs: GitHub 7d, X 7d
  (faster-moving), Website 15d, Docs 15d (change rarely). `safety_screen`/security score
  stays always-fresh, never cached for sale. This is a DIFFERENT mechanism from the
  anti-front-running delay — don't conflate.

## TWO gating decisions before ANY build (operator/legal, not code)

(The lawyer gate — a possible third — is already resolved for the crypto-only path, see
decision 2 below; it is NOT a blocker.)

1. **Provider ToS** — write to GoPlus, Blockscout, CabalSpy describing exactly what ARIA
   intends to sell, get written confirmation (GoPlus's own contract requires this). Until
   done, only `/walletscore` is safe to sell.
2. **Lawyer gate — ALREADY RESOLVED for the crypto-only path, do not relitigate.**
   `docs/conformite-dossier-avocat.md`'s "zéro encaissement avant avocat" rule does NOT
   apply to a pure crypto-to-crypto (USDC on Base) x402 revenue stream — explicit,
   repeated, final operator decision (23/07): "on est libre." This carve-out is
   crypto-only: any FIAT rail (USD/EUR) added later DOES still require the lawyer step.
   So this is NOT a blocker for the crypto-only build — only decisions 1 (provider ToS)
   and 3 (receiving-wallet governance) remain.
3. **Receiving-wallet governance** — a NEW dedicated receiving wallet, isolated from every
   spending wallet (same doctrine as the agent-wallet pilot's dedicated wallet). Decide:
   which wallet, does human validation gate incoming settlement, bookkeeping/tax.

## Recommended first step (if/when unblocked)

Build ONLY the `/walletscore` seller endpoint (ARIA's own composite, no third-party raw
pass-through, the one signal safe today), DORMANT + testnet-facilitator only + gated OFF,
so it's ready when the three gates clear — same pattern as the Sepolia rehearsal /
agent-wallet pilot (build the skeleton on testnet, activate after governance). No mainnet
receiving until all three decisions above are settled.
