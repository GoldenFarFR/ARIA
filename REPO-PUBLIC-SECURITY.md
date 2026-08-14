# Sécurité — repos publics GoldenFar

## Visibilité (2026-08-14, rafraîchi — était daté 2026-07-04)

| Repo | Visibilité | Contenu |
|------|------------|---------|
| `GoldenFarFR/ARIA` | **Public** | Produit : aria-core, vanguard (sans operator) |
| `GoldenFarFR/aria-acp-showcase` | **Public** | Démo ACP / Virtual Protocol |
| `GoldenFarFR/template-grok-cursor` | **Public**, archivé (décision opérateur 18/07) | Template IDE — la copie qui vivait dans ce monorepo (`template-grok-cursor/`) a été supprimée le 14/08, distincte de ce repo séparé qui reste archivé et intouché |
| `GoldenFarFR/aria-ops` | **PRIVÉ** | Mémoire, coffre, scripts opérateur |
| `GoldenFarFR/aria-brain` | **PRIVÉ** | Mémoire libre d'ARIA (gate `ARIA_BRAIN_ENABLED`, OFF en prod au 14/08) |

## Secrets

- **Jamais** dans Git : `production.env`, coffre `%LOCALAPPDATA%\GoldenFar\vault`
- Ops : repo **`aria-ops`** uniquement

## Variables locales

```powershell
$env:ARIA_REPO_ROOT = "$env:USERPROFILE\GitHub-Repos\ARIA"
$env:ARIA_OPS_ROOT  = "$env:USERPROFILE\GitHub-Repos\aria-ops"
```

## Handoff / deploy

```powershell
& "$env:ARIA_OPS_ROOT\local-sync\scripts\session-handoff.ps1"
& "$env:ARIA_OPS_ROOT\vanguard\operator\check-aria-status.ps1"
```