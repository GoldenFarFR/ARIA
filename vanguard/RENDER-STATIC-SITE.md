# Render — site statique Aria Vanguard ZHC (monorepo)

Erreur typique après migration vers `GoldenFarFR/ARIA` :

```
Empty build command; skipping build
Publish directory dist does not exist!
```

## Cause

Le service **Static Site** buildait la **racine** du monorepo. Le frontend vit dans `vanguard/` (`npm run build` → `vanguard/dist/`).

## Correctif dashboard Render (service « Aria Vanguard ZHC »)

| Champ | Valeur |
|-------|--------|
| **Root Directory** | `vanguard` |
| **Build Command** | `npm ci && npm run build` |
| **Publish Directory** | `dist` |

Variables build (Environment) : `VITE_PRODUCT_URL`, `VITE_PRODUCT_API_URL`, `VITE_PRIVY_APP_ID` (comme avant).

Puis **Manual Deploy** → Deploy latest commit.

## Blueprint

`render.yaml` à la racine du repo définit aussi `aria-vanguard-zhc` avec `rootDir: vanguard`.