# Operator — scripts GoldenFar / ARIA

Scripts opérateur pour **aria-vanguard** (API + vitrine). Les **secrets** sont dans le coffre local — jamais dans Git.

## Coffre (secrets)

```
%LOCALAPPDATA%\GoldenFar\vault\
├── production.env
├── local.env
├── vanguard.env
└── keys\render.api-key
```

Guide : [`VAULT-SECURITY.md`](./VAULT-SECURITY.md) · Runbook : [`OPERATOR-RUNBOOK.md`](./OPERATOR-RUNBOOK.md)

## Usage quotidien

```powershell
cd projets\aria-vanguard\operator
.\check-aria-status.ps1
.\sync-all.ps1          # après modif secret
```

| Script | Rôle |
|--------|------|
| `build-local.ps1` | Validation locale (pip, import, npm) avant deploy |
| `deploy-render.ps1` | Build local + sync + **1** redeploy (`-Reason` obligatoire) |
| `sync-render.ps1` | Coffre → Render env (`-SkipRedeploy` si quota epuise) |
| `sync-vanguard.ps1` | Coffre → Render static vitrine |
| `pull-render.ps1` | Render → coffre |
| `new-pc.ps1` | Clone repos + audit nouveau PC |
| `setup.ps1` | Init une fois |

## Nouveau PC

```powershell
cd projets\aria-vanguard\operator
.\new-pc.ps1
```

Stripe Pro : [`stripe/README.md`](./stripe/README.md)