# Aria Vanguard — Project instructions

## Vision (mandatory — every task)

**On every task**, read [`VISION.md`](./VISION.md) first — product, bugfix, UI, deploy, refactor, doc. No exceptions.

Filter through: current priorities (§5), recent decisions (§8), founder mode (10x / moat / what to delete). If a request conflicts with `VISION.md`, flag it and propose a vision-aligned alternative.

Reason in founder mode: 10x ambition, distribution via skills/plugins, ARIA as co-founder — not incremental dev tasks.

**All public-facing content in this repo is English** (UI, API messages, docs, GitHub).

## Context

**Aria Vanguard ZHC** holding stack: corporate site + ARIA API + Aria Market product app.

| Surface | Path | URL |
|---------|------|-----|
| Holding vitrine | `src/` | `ariavanguardzhc.com` |
| API ARIA | `backend/` | `api.ariavanguardzhc.com` |
| Product app | `product-frontend/` | served by API (`SERVE_FRONTEND=true`) |

## Repo layout (do not mix)

| Package | Path | Scope |
|---------|------|-------|
| **Holding site** | `src/` | Corporate UI, Privy gate, pricing |
| **Aria Market** | `backend/app/analysis/`, `realtime/`, `services/` | DEX scanner, pairs, watchlist, alerts |
| **ARIA host** | `backend/app/integrations/aria_host.py` | Market plugins at boot (`aria-core`) |
| **ARIA API** | `backend/app/api/routes/aria.py` | HTTP `/api/aria/*` |
| **Shared** | `backend/app/auth/`, `config.py`, `paths.py` | Auth + infra |

**Cerveau ARIA** : `aria-sandbox/packages/aria-core` (pip `aria-core`). Ne pas recréer `backend/app/aria/`.

## Conventions

- Backend: Python 3.12+, FastAPI
- Frontends: React + TypeScript + Tailwind CSS v4
- Never commit `.env`, API tokens, or `backend/data/`
- **Render deploy:** Dockerfile multi-stage (`product-frontend` build + Python). Do not commit `product-frontend/dist/` or root `dist/` from CI.
- Respect rate limits: DEXScreener 60 req/min, GeckoTerminal 30 req/min

## Corporate structure

| Entity | Role |
|--------|------|
| **Aria Vanguard ZHC** | Parent holding (ZHC) — this repo |
| **ARIA ZHC** | Chief Autonomous Officer |
| **Aria Market** | Flagship product (market intelligence) |

## Truth Ledger

Canonical facts: `aria-sandbox/.../aria_core/truth_ledger/canonical_facts.yaml` — edit in aria-sandbox, sync to prod.

See [`docs/ECOSYSTEM-REPOS.md`](./docs/ECOSYSTEM-REPOS.md) for the full repo map.