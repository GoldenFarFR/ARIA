# ARIA

Autonomous AI agent operating **Aria Vanguard ZHC**, a self-directed investment holding on Base. ARIA researches, decides, and (within strict human-approved boundaries) trades — building a public, on-chain track record before any real capital is put at risk.

Public presence: X [@Aria_ZHC](https://x.com/Aria_ZHC) · Telegram [@Aria_ZHC_Bot](https://t.me/Aria_ZHC_Bot) · [ariavanguardzhc.com](https://ariavanguardzhc.com)

**Private ops** (infra, credentials, deployment access): [`GoldenFarFR/aria-ops`](https://github.com/GoldenFarFR/aria-ops) — restricted.

## Thesis

The moat is **proven analysis**, not blind execution. Target allocation is 85% long/mid-term conviction picks on early Base builders, 15% short-term momentum trading. Proof precedes promise: every strategy runs on a fully-tracked paper portfolio, judged against explicit weekly targets, before it is ever trusted with real funds.

## What's actually built

- **Momentum trading engine** — technical-analysis entry (Fibonacci/RSI/EMA/MACD), regime-aware sizing and exit discipline (fear/neutral/euphoria), hard safety gates (honeypot detection, wash-trading caps, blacklists) that can never be bypassed by a good-looking setup.
- **VC-thesis engine** — fundamentals + security screening for early-stage Base tokens, LLM-judged conviction with a hard on-chain safety veto.
- **Paper-trading protocol** — a full $1M simulated portfolio resets weekly against a +10% target, used to diagnose and harden ARIA's decision-making before any real money is involved.
- **Wallet intelligence** — smart-money scoring, copy-trading and insider-wallet detection, all built on free/self-hosted data sources.
- **Real-money agent-wallet pilot** — a tightly-scoped, capped pilot (Coinbase CDP wallets) where ARIA can autonomously execute small, pre-approved swaps; every other real-capital action still requires explicit human confirmation.
- **x402 micropayments** — ARIA pays for the external data/services it consumes autonomously, under a hard weekly budget cap.
- **Public cockpit & VC reports** — a live dashboard and gated, watermarked PDF reports for tracked candidates.

## Guardrails that never bend

- **Human confirmation is mandatory on real capital** anywhere it could matter, with narrowly-scoped, explicitly-approved exceptions only (a testnet rehearsal, the capped agent-wallet pilot) — mainnet trading with real capital beyond those exceptions is never autonomous.
- A dedicated kill-switch (`/stop`) halts all outgoing action instantly.
- Hard, code-level vetoes (honeypot, ownership takeover, wash-trading) can never be judgment-called away by an LLM.

## Repository layout

| Path | Contents |
|---|---|
| `packages/aria-core/` | ARIA's core Python brain — skills, services, heartbeat loop |
| `vanguard/` | FastAPI backend + public site (operator-only tooling excluded) |
| `docs/` | Architecture, strategy, and protocol documentation |
| `contracts/` | On-chain anchoring contracts |
| `skills/` | Standalone skill modules |

## Docs

Start with `VISION.md` (root, vision SSOT), `docs/architecture-extensibilite.md` (extensibility architecture), and `docs/strategie-aria-investissement.md` (investment strategy). Security posture for this public repo: `REPO-PUBLIC-SECURITY.md`.
