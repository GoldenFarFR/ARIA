---
description: Runbook operateur ARIA — pièges sync/env, nouveau PC, ne pas oublier
alwaysApply: true
---

# Runbook operateur (obligatoire)

Avant setup Render, sync secrets, deploy, ou « c'est connecte » :

1. Lire `projets\aria-sandbox\packages\aria-core\src\aria_core\knowledge\operator_pitfalls.yaml`
2. Ou skill `operator-runbook` / `aria-vanguard\operator\OPERATOR-RUNBOOK.md`
3. Executer `projets\aria-vanguard\operator\check-aria-status.ps1` apres toute modif secret

**Regle d'or :** `operator\sync-render.ps1` met a jour Render mais le process Python doit **redemarrer** (redeploy inclus dans le script).

Ne jamais annoncer deploy/commit/API connectee sans preuve (health, skill data, URL GitHub).

Apres incident corrige : append `operator_pitfalls.yaml` + journal `JOURNAL.md`.