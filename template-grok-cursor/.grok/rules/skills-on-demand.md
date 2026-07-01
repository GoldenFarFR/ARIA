---
description: Chargement des skills — à la demande uniquement
alwaysApply: true
---

# Skills — à la demande uniquement

## Règle par défaut

- **Ne charger aucune skill** au démarrage ni « par précaution ».
- **Ne pas** lire plusieurs skills en parallèle sauf demande explicite de l'utilisateur.
- Les skills du repo restent **disponibles** via `/nom-skill` ou quand la demande correspond **clairement** à une seule skill.

## Quand charger une skill

| Demande utilisateur | Action |
|---------------------|--------|
| « site web », « landing page », app générique | **Aucune skill** — coder normalement |
| « site holding », « comme Kikou », « lambda Privy », `/spawn-holding-site` | Lire **uniquement** `.grok/skills/spawn-holding-site/SKILL.md` |
| `/nom-skill` ou « utilise la skill X » | Lire **uniquement** la skill nommée |
| « mini-jeux », « jeux sur l'accueil », `/graft-mini-games` | Lire **uniquement** `aria-skills/.grok/skills/graft-mini-games/SKILL.md` |
| Marketing Aria, posts, narrative | `aria-skills` → `marketing-decision-framework` |
| Deploy Render, secrets, nouveau PC | `aria-skills` → `operator-runbook` |

## Quand ne PAS charger

- Site web simple sans mention holding / Privy / nom de skill
- Tâche générique (fix, refactor, question)
- L'utilisateur n'a pas demandé de workflow métier spécifique

## Interdit

- Charger `spawn-holding-site` pour un site web ordinaire
- Fusionner plusieurs workflows sans demande explicite
- Lire `site-fonctionnel.md` sauf via la skill `spawn-holding-site` ou demande holding explicite
- Dupliquer les skills `aria-skills` dans le template — lier via `scripts/install.ps1`