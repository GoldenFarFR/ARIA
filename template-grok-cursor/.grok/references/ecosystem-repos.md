# Écosystème GoldenFar — où mettre quoi

**Vision SSOT :** [aria-vanguard/VISION.md](https://github.com/GoldenFarFR/aria-vanguard/blob/main/VISION.md)  
**Carte complète :** [aria-vanguard/docs/ECOSYSTEM-REPOS.md](https://github.com/GoldenFarFR/aria-vanguard/blob/main/docs/ECOSYSTEM-REPOS.md)

## Nouveau repo depuis ce template

1. **Use this template** → nouveau repo sous `GoldenFarFR/`
2. Copier `.grok/references/scaffold/` si site web holding
3. Lier les skills métier : `aria-skills/scripts/install.ps1` (ou `scripts/link-aria-skills.ps1` depuis ce repo)
4. Lire `VISION.md` (aria-vanguard) avant toute feature produit
5. Secrets → **jamais** dans le repo ; coffre local + `aria-vanguard/operator/` pour deploy Render

## Repos officiels (2026)

| Repo | Rôle |
|------|------|
| **aria-vanguard** | Holding + API ARIA (`backend/`, vitrine, DEXPulse app) + scripts `operator/` |
| **aria-sandbox** | Cerveau `aria-core` (runtime pip) — skills, Telegram, mémoire, indice |
| **aria-skills** | Skills Grok/Cursor distribuables (SSOT métier) |
| **aria-token-base** | Token R&D, tokenomics |
| **template-grok-cursor** | Ce template (bootstrap nouveau repo) |
| **collegue-memoire** | Mémoire opérateur (hors produit Aria) |
| **kikou** | Exemple site lambda holding déployé |
| ~~dexpulse~~ | *Déprécié 2026-06-19* — migré dans `aria-vanguard` |
| ~~dexpulse-secrets~~ | *Déprécié* — fusionné dans `aria-vanguard/operator/` |

## Règle d'or

| Type | Où ça vit |
|------|-----------|
| Cerveau ARIA (runtime) | `aria-sandbox` → package `aria-core` |
| Holding + API + app produit | `aria-vanguard` |
| Skills IDE (moat) | `aria-skills` |
| Scripts opérateur + deploy | `aria-vanguard/operator/` (hors Git, secrets dans coffre) |

## Patterns copiables

- Skill runtime : `aria-sandbox/packages/aria-core/src/aria_core/skills/`
- Plugins hôte marché : `aria-vanguard/backend/app/integrations/aria_host.py`
- Politique coût API : `aria_core/x_publication_policy.py`
- Site holding Privy : `aria-vanguard/src/` ou scaffold de ce template
- Deploy holding : `aria-vanguard/operator/setup-holding-render.ps1`

Voir `aria-backend-patterns.md` pour le détail backend.