# Aria Vanguard ZHC Ã¢â‚¬â€ holding stack

> **Vision ÃƒÂ©cosystÃƒÂ¨me :** [`VISION.md`](./VISION.md) Ã¢â‚¬â€ carte repos : [`docs/ECOSYSTEM-REPOS.md`](./docs/ECOSYSTEM-REPOS.md)

Code source dans le monorepo **GoldenFarFR/ARIA** (sous-dossier vanguard/). Le site + API Aria Telegram sont déployés depuis ici. (Ancien repo séparé aria-vanguard supprimé.)

| Surface | URL | DÃƒÂ©ploiement |
|---------|-----|-------------|
| Vitrine | [ariavanguardzhc.com](https://ariavanguardzhc.com) | Render static (depuis ARIA/vanguard dans le monorepo) |
| API ARIA | [api.ariavanguardzhc.com](https://api.ariavanguardzhc.com) | Render Docker (`aria-api`) |

L'ancien repo `dexpulse` est **dÃƒÂ©prÃƒÂ©ciÃƒÂ©** Ã¢â‚¬â€ tout vit ici.

## Structure

```
ARIA/vanguard/ (monorepo) 
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ src/                 # Vitrine holding (React)
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ product-frontend/    # App marchÃƒÂ© servie par l'API (build Docker)
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ backend/           # API FastAPI (auth, billing, Telegram, ARIA)
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ operator/          # Scripts opÃƒÂ©rateur (sync Render, audit, coffre)
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ Dockerfile         # Build API + product-frontend
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ render.yaml        # Blueprint Render (static + docker)
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

1. Connecter `GoldenFarFR/ARIA` (branche main, dossier vanguard/ comme rootDir dans render)
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