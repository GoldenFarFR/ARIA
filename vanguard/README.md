# Aria Vanguard ZHC — holding stack

> **Vision écosystème :** [`VISION.md`](./VISION.md) — carte repos : [`docs/ECOSYSTEM-REPOS.md`](./docs/ECOSYSTEM-REPOS.md)

Code source dans le monorepo **GoldenFarFR/ARIA** (sous-dossier vanguard/). Le site + API Aria Telegram sont déployés depuis ici. (Ancien repo séparé aria-vanguard supprimé.)

| Surface | URL | Déploiement |
|---------|-----|-------------|
| Vitrine | [ariavanguardzhc.com](https://ariavanguardzhc.com) | Build statique via `deploy-vitrine.sh`, servi par nginx sur le VPS |
| API ARIA | [api.ariavanguardzhc.com](https://api.ariavanguardzhc.com) | Docker `aria-api` (`deploy.sh`) sur le VPS, blue-green derrière nginx |

L'ancien repo `dexpulse` est **déprécié** — tout vit ici.

## Structure

```
ARIA/vanguard/ (monorepo) 
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

## Deploy (VPS Docker + nginx, migré depuis Render — README obsolète corrigé 25/08)

Deux scripts indépendants, à lancer séparément si les deux surfaces changent :

1. **API** : `./deploy.sh` — build Docker, lance le nouveau conteneur sur le port
   standby (8000⟷8001 en alternance), health-check AVANT bascule, ancien
   conteneur retiré seulement une fois le trafic réel confirmé à travers nginx
   (rollback quasi instantané, cf. `docs/deploy-rollback-blue-green.md`).
2. **Vitrine** : `./deploy-vitrine.sh` — build statique, même garde-fou double
   (heuristique de contenu + marqueur de build), `.old`/`.failed` conservés en
   cas d'échec.

Binding backend strictement `127.0.0.1:8000/8001` (jamais public), nginx comme
front TLS via `/etc/nginx/conf.d/aria-api-upstream.conf` (hors repo). Secrets
dans `.env` backend (jamais un fichier séparé). Détail complet : section
« Deployment » de `CLAUDE.md`.

## Environment

| Variable | Usage |
|----------|-------|
| `VITE_PRODUCT_URL` | Lien app depuis la vitrine |
| `VITE_PRODUCT_API_URL` | API portfolio live |
| `SITE_BASE_URL` | URL canonique API (`api.ariavanguardzhc.com`) |