---
name: spawn-holding-site
description: >
  Créer un site lambda holding (accueil + Privy + Render + CORS). Utiliser
  UNIQUEMENT quand l'utilisateur lance /spawn-holding-site ou demande
  explicitement un site holding, vitrine Aria Vanguard, ou « comme Kikou ».
disable-model-invocation: true
user-invocable: true
argument-hint: "nom-du-site"
metadata:
  short-description: "Site lambda holding (Privy + Render)"
---

# Spawn holding site

Site **minimal** : accueil statique + Privy + deploy Render. Pas d'enrichissement sauf demande explicite.

## Avant de commencer

Lire **en entier** : `.grok/references/site-fonctionnel.md`

## Entrée

- Nom du site fourni par l'utilisateur (argument ou message), ex. `kikou`
- Si plusieurs noms : traiter **un par un**, même checklist

## Checklist (par site)

1. `gh repo create <nom> --template GoldenFarFR/template-grok-cursor --public --clone`
2. Copier `.grok/references/scaffold/` → racine du repo ; renommer `SITE_NAME`, `TOKEN_KEY`, préfixe storage, port dev unique
3. `npm install && npm run build` → commit → push
4. `cd $env:USERPROFILE\projets\aria-vanguard\operator` → `.\setup-holding-render.ps1 -SiteName <nom> -UpdateCors`
5. Mettre à jour `src/lib/site.ts` (`SITE_DOMAIN`) avec l'URL Render affichée → push
6. Rappeler à l'utilisateur : ajouter l'URL + `http://localhost:<port>` dans Privy Allowed origins

## Ne pas faire

- Enrichir (sections, API contenu, DEXPulse handoff) sans demande
- Charger d'autres skills pendant ce workflow