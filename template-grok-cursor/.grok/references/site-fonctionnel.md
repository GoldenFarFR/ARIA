# Blueprint — site lambda (accueil + Privy)

> **Pour l'agent** : à chaque **nouveau repo**, livrer un site **minimal qui fonctionne**. Le contenu riche viendra **plus tard**, quand l'utilisateur alimentera le site.

## Philosophie

| Phase | Qui | Quoi |
|-------|-----|------|
| **Création** (maintenant) | Agent | Page d'accueil standard + connexion Privy + deploy Render |
| **Enrichissement** (plus tard) | Utilisateur + agent | Sections, API, handoff produit, design custom, contenu métier |

**Ne pas** copier `aria-vanguard` ou `kikou` en entier pour un nouveau site — ce sont des sites **déjà alimentés**.  
Copier le **scaffold lambda** : `.grok/references/scaffold/`

Références si besoin d'enrichir plus tard :
- `projets/aria-vanguard` — holding complet + API
- `projets/kikou` — site lambda déployé (exemple)

---

## Site lambda — contenu livré

```
┌─────────────────────────────────────┐
│  Nav : logo + nom du site + Sign in │
├─────────────────────────────────────┤
│  Hero : titre + courte phrase       │
│  (texte statique, pas d'API)        │
├─────────────────────────────────────┤
│  Footer minimal                     │
└─────────────────────────────────────┘
```

**Inclus** : Privy (Sign in → Activate access → Sign out), session backend, deploy Render.  
**Exclu au départ** : sections portfolio, chat, API contenu, lien produit DEXPulse, bannière welcome élaborée.

---

## Résumé agent (création)

1. Repo depuis `template-grok-cursor`
2. Copier scaffold lambda + renommer (`SITE_NAME`, `TOKEN_KEY`, port dev)
3. `npm run build` → push
4. `setup-holding-render.ps1 -SiteName <nom> -UpdateCors` (**obligatoire** — sync CORS + redeploy API + vérif auto)
5. Privy Allowed origins → tester Sign in + Activate access

---

## Privy — une app pour tous les sites

**Ne pas créer une nouvelle app Privy.**

- Réutiliser `VITE_PRIVY_APP_ID` (`aria-vanguard/operator/site.config.json` → `privyAppId`)
- Identity tokens : déjà activés sur l'app existante
- Par nouveau site : ajouter l'URL Render dans **Allowed origins**

---

## Procédure — nouveau repo

### 1. Créer le repo

```powershell
cd $env:USERPROFILE\projets
gh repo create <nom> --template GoldenFarFR/template-grok-cursor --public -c
```

### 2. Scaffold lambda

Copier depuis `.grok/references/scaffold/` vers le repo :

| Fichier scaffold | Destination |
|------------------|-------------|
| `src/*` (incl. `src/shims/privy-vite-peer.ts`) | `src/*` |
| `vite.config.ts` | `vite.config.ts` (ajuster `port`) |
| `package.json` | `package.json` |
| `render.yaml` | `render.yaml` |
| `.env.example` | `.env.example` |
| `tsconfig*.json`, `index.html` | racine |

**Renommer obligatoirement** :

| Constante | Exemple |
|-----------|---------|
| `SITE_NAME` | `MonSite` |
| `SITE_TAGLINE` | phrase courte statique |
| `TOKEN_KEY` | `monsite_session_token` |
| Préfixe storage / events | `monsite:` |
| Port dev | `5176` (unique) |

### 3. `package.json` (rien d'autre)

```json
{
  "dependencies": {
    "@privy-io/react-auth": "^3.31.0",
    "lucide-react": "^1.20.0",
    "react": "^19.2.6",
    "react-dom": "^19.2.6"
  },
  "devDependencies": {
    "@types/node": "^22.15.0",
    "@tailwindcss/vite": "^4.3.1",
    "@vitejs/plugin-react": "^6.0.1",
    "tailwindcss": "^4.3.1",
    "typescript": "~6.0.2",
    "vite": "^8.0.12"
  }
}
```

Build Privy : `src/shims/privy-vite-peer.ts` + alias dans `vite.config.ts` (fournis dans scaffold).

### 4. `.env.example`

```env
# API holding ARIA — auth membre uniquement au départ
VITE_DEXPULSE_API_URL=https://test-1-nwf2.onrender.com/api
# Canonique quand DNS+SSL OK : https://api.ariavanguardzhc.com/api
VITE_PRIVY_APP_ID=<privyAppId depuis site.config.json>
```

(`VITE_DEXPULSE_API_URL` = nom legacy ; pointe vers l'API `aria-vanguard/backend`.)

### 5. Build et push

```powershell
npm install && npm run build
git add -A && git commit -m "feat: site lambda <nom>" && git push
```

### 6. Deploy Render

```powershell
cd $env:USERPROFILE\projets\aria-vanguard\operator
.\setup-holding-render.ps1 -SiteName <nom> -Repo GoldenFarFR/<nom> -Branch master -UpdateCors
```

**Toujours passer `-UpdateCors`.** Le script :
1. ajoute l'URL Render dans `production.env` → `CORS_ORIGINS`
2. synchronise vers l'API holding (`sync-render.ps1`)
3. **force un redéploiement** de l'API (sinon `Failed to fetch` côté navigateur)
4. **vérifie** que `Access-Control-Allow-Origin` répond pour l'URL du site

Si la vérif CORS échoue, le script s'arrête avec une erreur explicite — ne pas considérer le site comme prêt.

Mettre à jour `src/lib/site.ts` (`SITE_DOMAIN`) avec l'URL Render retournée, puis push.

### 7. Privy + test

- Allowed origins : `https://<slug>-xxxx.onrender.com` (URL exacte affichée par le script) + `http://localhost:<port>`
- Tester : page OK → Sign in → Activate access → Sign out

---

## Arborescence lambda (`src/`)

```
src/
├── main.tsx
├── App.tsx                 # MemberGate + HomePage (pas de MemberWelcome au départ)
├── api.ts                  # auth seulement (required, session, privy/login)
├── index.css
├── pages/HomePage.tsx      # accueil statique
├── components/
│   ├── SimpleNav.tsx       # logo + Sign in
│   ├── MemberSignInButton.tsx
│   └── MemberGate.tsx
├── lib/
│   ├── site.ts
│   ├── privy-config.ts
│   ├── privy-session.ts
│   ├── auth.ts
│   └── shims/privy-vite-peer.ts
```

Fichiers **optionnels** (phase enrichissement — ajouter quand l'utilisateur alimente le site) :

| Fichier | Rôle |
|---------|------|
| `visitor.ts` | APIs publiques avec `X-Visitor-Id` |
| `product-handoff.ts` | Lien produit `?aria_token=` |
| `member-profile.ts` + `MemberWelcome.tsx` | Bannière bienvenue |
| `types.ts` + appels `/aria/content/*` | Contenu dynamique API |
| Sections / FAQ / chat | Contenu métier |

---

## Règles auth (inchangées)

- Un seul `POST /auth/privy/login` par connexion
- `getIdentityToken()` async + retries après `refreshUser()`
- `MemberGate` : site public, pas de blocage
- `MemberSignInButton` : Sign in → Activate access → Sign out
- Pas d'auto-échange au chargement

---

## Pièges connus

| Symptôme | Fix |
|----------|-----|
| Identity token manquant | Identity tokens déjà OK sur l'app existante |
| Failed to fetch (auth API) | Relancer `setup-holding-render.ps1 -SiteName <nom> -UpdateCors` — redeploy + vérif CORS |
| Build Vite peer deps | Copier `privy-vite-peer.ts` + `vite.config.ts` |
| Privy OK local, KO prod | Allowed origins Privy (URL Render exacte) |
| Telegram muet après redeploy | Vérifier `getWebhookInfo` — voir `operator_pitfalls.yaml` dans aria-core |

---

## Enrichissement (quand l'utilisateur revient)

L'utilisateur dira ce qu'il veut ajouter. S'inspirer alors de `aria-vanguard` ou `kikou` :

- Contenu API → `visitor.ts`, `getSiteContent()`, sections
- Produit DEXPulse → handoff token, bouton nav
- Bannière membre → `MemberWelcome.tsx`
- Design → `index.css`, composants

**Ne pas enrichir par défaut** à la création du repo.

---

## Instruction agent (une ligne)

**Nouveau repo** = scaffold lambda → build → `setup-holding-render.ps1 -UpdateCors` (CORS redeploy+vérif) → Privy origins → test auth. Contenu = plus tard.