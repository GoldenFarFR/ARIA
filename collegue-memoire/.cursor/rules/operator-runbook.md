---
description: Runbook operateur ARIA — pièges sync/env, nouveau PC, ne pas oublier
alwaysApply: true
---

# Runbook operateur (obligatoire)

Avant setup Render, sync secrets, deploy, ou « c'est connecte » :

1. Lire `projets\aria-sandbox\packages\aria-core\src\aria_core\knowledge\operator_pitfalls.yaml`
2. Ou skill `operator-runbook` / `aria-vanguard\operator\OPERATOR-RUNBOOK.md`
3. Executer `projets\aria-vanguard\operator\check-aria-status.ps1` apres toute modif secret

## Deploy (2026-06)

- **Toujours** `build-local.ps1` apres changement code
- **Deploy prod** uniquement via `deploy-render.ps1 -Reason "..."` (1 redeploy groupe)
- **Eviter** `sync-render.ps1` en rafale (quota pipeline ~2 min/deploy)
- CI GitHub : PR + manuel — pas de push main

Ne jamais annoncer deploy/commit/API connectee sans preuve (health, skill data, URL GitHub).

Apres incident corrige : append `operator_pitfalls.yaml` + journal `JOURNAL.md`.