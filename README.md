# ARIA — monorepo GoldenFar

Repo privé unique regroupant l'écosystème ARIA.

## Structure

| Dossier | Contenu (ex-repo) |
|---------|-------------------|
| `packages/aria-core/` | Cerveau Python ARIA (ex aria-sandbox) |
| `vanguard/` | API FastAPI, site holding, DEXPulse, operator/ (ex aria-vanguard) |
| `skills/` | Skills Grok/Cursor (ex aria-skills) |
| `local-sync/` | Sync multi-PC, coffre chiffré (ex aria-local-sync) |
| `collegue-memoire/` | COLLEGUE.md, HANDOFF, journal (ex collegue-memoire) |
| `sandbox/` | Truth-ledger, prompts expérimentaux |
| `core/` | Personnalité Pro |
| `memory/`, `bot/`, `scripts/` | Travail local ARIA |
| `template-grok-cursor/` | Template IDE (ex template-grok-cursor) |

## Chemins

```powershell
$env:ARIA_REPO_ROOT = "C:\Users\Studi\GitHub-Repos\ARIA"
```

Scripts : `scripts/aria-paths.ps1` (SSOT chemins PowerShell).

## Deploy Render

- Blueprint : `render.yaml` (racine)
- Build : `docker build -f vanguard/Dockerfile .` depuis la racine
- Secrets : `vanguard/operator/production.env` (coffre local, jamais commité)

## Session / handoff

```powershell
cd $env:ARIA_REPO_ROOT\local-sync\scripts
.\session-handoff.ps1
```

## Vision

`VISION.md` à la racine (SSOT).