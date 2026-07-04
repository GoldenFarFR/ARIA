# ARIA — produit GoldenFar (public)

Cerveau autonome **Aria Vanguard ZHC** : API, site, skills, intégration ACP.

**Ops privées** (coffre, deploy, mémoire) : repo [`GoldenFarFR/aria-ops`](https://github.com/GoldenFarFR/aria-ops) — accès restreint.

**Showcase Virtual Protocol** : [`GoldenFarFR/aria-acp-showcase`](https://github.com/GoldenFarFR/aria-acp-showcase)

## Structure (ce repo)

| Dossier | Contenu |
|---------|---------|
| `packages/aria-core/` | Cerveau Python ARIA |
| `vanguard/` | API FastAPI, site holding (sans `operator/`) |
| `skills/` | Skills Grok/Cursor |
| `template-grok-cursor/` | Template IDE |
| `scripts/` | `aria-paths.ps1`, redirect handoff |

## Chemins

```powershell
$env:ARIA_REPO_ROOT = "$env:USERPROFILE\GitHub-Repos\ARIA"
$env:ARIA_OPS_ROOT  = "$env:USERPROFILE\GitHub-Repos\aria-ops"
```

## Deploy Render

- Blueprint : `render.yaml`
- Scripts opérateur : `aria-ops/vanguard/operator/` (privé)
- Secrets : coffre `%LOCALAPPDATA%\GoldenFar\vault` — jamais Git

## Session handoff

```powershell
& "$env:ARIA_OPS_ROOT\local-sync\scripts\session-handoff.ps1"
```

Voir `REPO-PUBLIC-SECURITY.md`.

## Vision

`VISION.md` à la racine (SSOT).