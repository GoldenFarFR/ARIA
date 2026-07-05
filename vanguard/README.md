# Aria Vanguard ZHC ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â holding stack

> **Vision ÃƒÆ’Ã‚Â©cosystÃƒÆ’Ã‚Â¨me :** [`VISION.md`](./VISION.md) ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â carte repos : [`docs/ECOSYSTEM-REPOS.md`](./docs/ECOSYSTEM-REPOS.md)

Code source dans le monorepo **GoldenFarFR/ARIA** (sous-dossier vanguard/). Le site + API Aria Telegram sont dÃ©ployÃ©s depuis ici. (Ancien repo sÃ©parÃ© aria-vanguard supprimÃ©.)

| Surface | URL | DÃƒÆ’Ã‚Â©ploiement |
|---------|-----|-------------|
| Vitrine | [ariavanguardzhc.com](https://ariavanguardzhc.com) | Render static (depuis ARIA/vanguard dans le monorepo) |
| API ARIA | [api.ariavanguardzhc.com](https://api.ariavanguardzhc.com) | Render Docker (`aria-api`) |

L'ancien repo `dexpulse` est **dÃƒÆ’Ã‚Â©prÃƒÆ’Ã‚Â©ciÃƒÆ’Ã‚Â©** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â tout vit ici.

## Structure

```
ARIA/vanguard/ (monorepo) 
ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ src/                 # Vitrine holding (React)
ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ product-frontend/    # App marchÃƒÆ’Ã‚Â© servie par l'API (build Docker)
ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ backend/           # API FastAPI (auth, billing, Telegram, ARIA)
ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ operator/          # Scripts opÃƒÆ’Ã‚Â©rateur (sync Render, audit, coffre)
ÃƒÂ¢Ã¢â‚¬ÂÃ…â€œÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Dockerfile         # Build API + product-frontend
ÃƒÂ¢Ã¢â‚¬ÂÃ¢â‚¬ÂÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ render.yaml        # Blueprint Render (static + docker)
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