# Aria Vanguard ZHC — holding stack

> **Vision écosystème :** [`VISION.md`](./VISION.md) — carte repos : [`docs/ECOSYSTEM-REPOS.md`](./docs/ECOSYSTEM-REPOS.md)

Repo unique pour la holding **Aria Vanguard ZHC** :

| Surface | URL | Déploiement |
|---------|-----|-------------|
| Vitrine | [ariavanguardzhc.com](https://ariavanguardzhc.com) | Render static (`aria-vanguard`) |
| API ARIA | [api.ariavanguardzhc.com](https://api.ariavanguardzhc.com) | Render Docker (`aria-api`) |

L'ancien repo `dexpulse` est **déprécié** — tout vit ici.

## Structure

```
aria-vanguard/
├── src/                 # Vitrine holding (React)
├── product-frontend/    # App marché servie par l'API (build Docker)
├── backend/           # API FastAPI (auth, billing, Telegram, ARIA)
├── operator/          # Scripts opérateur (sync Render, audit, coffre)
├── Dockerfile         # Build API + product-frontend
└── render.yaml        # Blueprint Render (static + docker)
```

## Dev local

**Vitrine holding :**

```bash
npm ci
npm run dev
```

**API + app produit :**

```bash
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd product-frontend && npm ci && npm run dev
```

## Deploy (Render)

1. Connecter `GoldenFarFR/aria-vanguard` (branche `main`)
2. Appliquer `render.yaml` ou configurer les 2 services manuellement
3. Domaines custom :
   - Static : `ariavanguardzhc.com`, `www.ariavanguardzhc.com`
   - API : `api.ariavanguardzhc.com`
4. Secrets : coffre local + `operator\sync-render.ps1`

## Environment

| Variable | Usage |
|----------|-------|
| `VITE_PRODUCT_URL` | Lien app depuis la vitrine |
| `VITE_PRODUCT_API_URL` | API portfolio live |
| `SITE_BASE_URL` | URL canonique API (`api.ariavanguardzhc.com`) |