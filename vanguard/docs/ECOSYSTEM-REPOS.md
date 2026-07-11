# GoldenFar / Aria — Carte des repos GitHub

> **⚠️ Obsolète (nomenclature pré-migration monorepo).** Ce document date du
> 2026-06-19 et décrit une structure multi-repos (`aria-sandbox`,
> `aria-vanguard`, `aria-skills`, `aria-token-base`) antérieure à la migration
> vers le monorepo `github.com/GoldenFarFR/ARIA` (commit `3907bbf3`, 01/07).
> Source de vérité actuelle sur l'architecture : `CLAUDE.md` (section
> « Architecture ») à la racine du repo `ARIA`. À nettoyer/réécrire.

**SSOT vision :** [`VISION.md`](../VISION.md)  
**SSOT faits produit :** `aria-sandbox/packages/aria-core/src/aria_core/truth_ledger/canonical_facts.yaml`  
**Dernière mise à jour :** 2026-06-19

## Règle d'or

| Type | Où ça vit |
|------|-----------|
| **Cerveau ARIA (runtime)** | `aria-sandbox` → package **`aria-core`** |
| Holding + API + app produit | **`aria-vanguard`** (`backend/`, `product-frontend/`, vitrine `src/`) |
| Expériences / vérité miroir | `aria-sandbox` |
| Token R&D | `aria-token-base` |
| Skills Grok/Cursor (moat) | `aria-skills` |
| Nouveau repo bootstrap | `template-grok-cursor` |
| Scripts opérateur + deploy | `aria-vanguard/operator/` (hors API Aria) |

---

## Repos officiels

| Repo | Rôle | Deploy | Aria API |
|------|------|--------|----------|
| **aria-vanguard** | **Holding** + **API ARIA** (`api.ariavanguardzhc.com`) + app Aria Market | Render static + Docker `aria-api` | Infra permanente |
| **aria-sandbox** | **Cerveau** `aria-core` (brain, skills, Telegram, X, indice) + tests + `experiments/` | pip git | Runtime SSOT |
| **aria-token-base** | Docs tokenomics, launchpad narrative | — | Write R&D |
| **aria-skills** | Skills Grok distribuables (`vision-enforcer`, marketing, …) | — | SSOT skills |
| **template-grok-cursor** | Template nouveau repo (rules FR, scaffold site, install-skill) | — | Bootstrap |
| **kikou** | Side project / test Render (nom historique) | — | Hors holding |
| **collegue-memoire** | Mémoire opérateur (hors produit) | — | Hors écosystème Aria |

| ~~**dexpulse**~~ | *Déprécié* — migré dans `aria-vanguard` | — | Ne plus utiliser |

---

## Patterns réutilisables (source → template)

| Pattern | Source aria-vanguard | Usage template |
|---------|----------------------|----------------|
| Skill runtime (`aria_core/skills/*`) | `aria-sandbox/packages/aria-core` | Package cerveau |
| Politique coût API | `x_publication_policy.py` (aria-core) | Nouveaux agents pay-per-use |
| Objectif revenu | `revenue_goals.py` + ledger | ZHC entrepreneur repos |
| Vision rule | `.grok/rules/vision.md` + skill `vision-enforcer` | Template + `aria-skills` |
| Privy + holding gate | `src/` vitrine + `backend/app/auth/` | `template-grok-cursor` references |
| Secrets sync | `aria-vanguard/operator/*.ps1` | Jamais dans template public |

---

## Commandes Aria (opérateur)

```
github status              # droits + liste repos
liste tous les repos       # inventaire live
/repertoire list           # portfolio holding (SQLite, ≠ GitHub)
```

---

## Clone local recommandé

```powershell
cd $env:USERPROFILE\projets
git clone https://github.com/GoldenFarFR/aria-sandbox.git
git clone https://github.com/GoldenFarFR/aria-vanguard.git
cd aria-sandbox
.\scripts\setup-local.ps1
```

- **Tests cerveau** : `aria-sandbox/packages/aria-core/tests/`
- **Tests hôte API** : `aria-vanguard/backend/tests/`
- **Bump deploy** : après push aria-sandbox → `aria-vanguard/backend/scripts/bump-aria-core-pin.ps1`
- Scripts `operator/` : secrets dans le coffre machine uniquement