# ARIA — produit GoldenFar (public)

Cerveau autonome **Aria Vanguard ZHC** : API, site, skills, intégration ACP.

**Livraison Spark (preuve Virtuals)** : [`GoldenFarFR/aria-acp-showcase`](https://github.com/GoldenFarFR/aria-acp-showcase) · PR Showcase : [`acp-cli-demos/showcase/aria-vanguard-zhc`](https://github.com/GoldenFarFR/acp-cli-demos/tree/showcase/aria-vanguard-zhc)

**Ops privées** (coffre, deploy, mémoire) : [`GoldenFarFR/aria-ops`](https://github.com/GoldenFarFR/aria-ops) — accès restreint.

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

## Deploy VPS IONOS

- Procédure complète : `docs/deploy-ionos.md`
- Script backend : `vanguard/deploy.sh` · vitrine : `vanguard/deploy-vitrine.sh`
- Scripts opérateur : `aria-ops/vanguard/operator/` (privé)
- Secrets : coffre `%LOCALAPPDATA%\GoldenFar\vault` — jamais Git

## Session handoff

```powershell
& "$env:ARIA_OPS_ROOT\local-sync\scripts\session-handoff.ps1"
```

Voir `REPO-PUBLIC-SECURITY.md`.

## Vision

`VISION.md` à la racine (SSOT).