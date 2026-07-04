# Sécurité — repos publics GoldenFar

## Visibilité (2026-07-04)

| Repo | Visibilité | Contenu |
|------|------------|---------|
| `GoldenFarFR/ARIA` | **Public** | Produit : aria-core, vanguard (sans operator) |
| `GoldenFarFR/aria-acp-showcase` | **Public** | Démo ACP / Virtual Protocol |
| `GoldenFarFR/template-grok-cursor` | **Public** | Template IDE |
| `GoldenFarFR/aria-ops` | **PRIVÉ** | Mémoire, coffre, scripts opérateur |

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