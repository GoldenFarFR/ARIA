---
name: operator-runbook
description: >
  Runbook operateur GoldenFar/ARIA — pièges env Render, sync secrets, nouveau PC,
  nouveau repo GitHub, nouvel agent IDE. Toujours actif avant setup/deploy.
  Triggers: runbook, nouveau pc, nouveau github, check-aria, setup render, ne pas oublier,
  /operator-runbook.
---

# Operator runbook

## When to use

- Nouveau PC, nouveau compte GitHub, nouvel agent (Cursor/Grok/Claude).
- Sync secrets, deploy Render, X API, GitHub droits.
- L'utilisateur dit que quelque chose « devrait marcher » mais `/x status` ou health dit non.
- Avant de dire « c'est deploye » ou « c'est connecte ».

## SSOT (lire en premier)

| Fichier | Role |
|---------|------|
| `aria-sandbox/.../knowledge/operator_pitfalls.yaml` | Machine — ARIA + tests |
| `aria-vanguard/operator/OPERATOR-RUNBOOK.md` | Humain |
| `aria-vanguard/operator/check-aria-status.ps1` | Audit live |

## Regle d'or (deploy 2026-06)

1. **`build-local.ps1`** apres chaque changement code (pip + import + npm).
2. **`deploy-render.ps1 -Reason "..."`** seulement quand un lot prod est pret — **1 redeploy** (~2 min quota).
3. Ne pas enchaîner `sync-render` — quota `pipeline_minutes_exhausted` bloque le mois.
4. Variables Render ≠ process recharge : redeploy obligatoire pour activer les secrets.

CI GitHub : PR + manuel seulement (pas push main). Budget 0 $ Actions = stop net, pas de facture.

## Nouveau PC (ordre)

1. `git clone` collegue-memoire, aria-skills, aria-sandbox, aria-vanguard
2. `aria-skills\scripts\install.ps1`
3. Copier `.cursor\rules\` depuis collegue-memoire
4. Coffre `vault\keys\render.api-key` (Syncthing ou import `.gfv`)
5. `aria-vanguard\operator\check-aria-status.ps1`

## Ne jamais

- Annoncer commit/deploy sans lien GitHub ou `data` skill.
- Dire X connecte sans `/x status` ou health `aria_x.post_configured`.
- `sync-render` en rafale (preferer `deploy-render.ps1` une fois).
- Deploy sans `build-local.ps1` prealable.
- Commiter `production.env`.

## Apres chaque incident corrige

1. Ajouter une entree dans `operator_pitfalls.yaml`
2. `check-aria-status.ps1` vert
3. Proposer `/learn` a l'operateur pour memoire strategique