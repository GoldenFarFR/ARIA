# Secret rotation checklist

If `production.env` or `local.env` were ever committed to git, treat these credentials as **compromised** and rotate them.

## Rotate (in order)

1. **Telegram** — @BotFather → `/revoke` or regenerate token → update `production.env` → `.\sync-render.ps1`
2. **Groq** — [console.groq.com](https://console.groq.com) → new API key → update `production.env` → sync
3. **Render API key** — Dashboard → Account Settings → API Keys → revoke old, create new → `.render-api-key`
4. **X API** (when used) — developer.x.com → regenerate keys

## After rotation

```powershell
cd projets/aria-vanguard/operator
.\sync-all.ps1
```

## Git history

To remove secrets from git history (advanced):

```powershell
# Install git-filter-repo, then:
git filter-repo --path production.env --path local.env --invert-paths
git push --force
```

Prefer rotation over history rewrite if the repo stayed private with only you as collaborator.