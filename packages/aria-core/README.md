# aria-core

Runtime du **cerveau ARIA** (ZHC) — SSOT dans `aria-sandbox`, consommé par **`aria-vanguard`** (hôte deploy Render).

## Rôle

| Repo | Rôle |
|------|------|
| **aria-sandbox** | Cerveau SSOT — ce package + truth-ledger + experiments |
| **aria-vanguard** | Holding + API `aria-api` sur Render + scripts opérateur |
| **aria-skills** | Skills Grok/Cursor (hors runtime prod) |

> Repo `dexpulse` : **déprécié** (2026-06-19) — tout passe par `aria-vanguard`.

## Documentation (Phase A)

| Fichier | Contenu |
|---------|---------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Carte modules, flux, 3 couches mémoire |
| [docs/WHERE-TO-PUT.md](docs/WHERE-TO-PUT.md) | Règles placement skills / mémoire / knowledge |

## Install

**Dev** : `pip install -e ".[dev]"` depuis ce dossier.

**Prod / Render** : git pin SHA dans `aria-vanguard/backend/requirements.txt` (jamais `@main`).

```text
aria-core @ git+https://github.com/GoldenFarFR/aria-sandbox.git@<SHA>#subdirectory=packages/aria-core
```

Bump : `aria-vanguard/backend/scripts/bump-aria-core-pin.ps1 -Sha <short-sha>`

## Bootstrap hôte

```python
from pathlib import Path
from aria_core import bootstrap

bootstrap.configure(
    data_dir=Path("/app/backend/data"),
    settings=vanguard_settings,  # pydantic Settings du backend
)
bootstrap.register_host_integrations(
    get_watchlist=...,
    check_rate_limit=...,
)
```

## Dev local

```powershell
# Depuis aria-sandbox/
.\scripts\setup-local.ps1
```

```bash
cd packages/aria-core
pip install -e ".[dev]"
pytest tests -q
```

Validation avant deploy prod :

```powershell
cd projets\aria-vanguard\operator
.\build-local.ps1
.\deploy-render.ps1 -Reason "description du lot"
```

## Tests

**319 tests** dans `packages/aria-core/tests/` — SSOT cerveau, sans hôte FastAPI complet.

```bash
pytest tests -q
```

## Pins & prod (2026-06-20)

| Référence | SHA / valeur |
|-----------|----------------|
| Pin `requirements.txt` (vanguard) | `1a5e0e0c` |
| HEAD `aria-sandbox` (doc Phase A) | `c8a10583`+ |
| Prod live (dernier health) | vérifier `/api/health` → `commit` |

Deploy : `build-local.ps1` → `deploy-render.ps1` (1 redeploy, ~2 min quota Render).  
Ne pas enchaîner `sync-render` — voir `operator_pitfalls.yaml` § `render-pipeline-minutes-exhausted`.

## CI

GitHub Actions : **PR + manuel** recommandé (économie minutes). Validation locale prioritaire.

```bash
python scripts/check_no_drift.py
```

## Deploy après modif cerveau

1. Commit + push **aria-sandbox**
2. `bump-aria-core-pin.ps1 -Sha <commit>`
3. Commit + push **aria-vanguard**
4. `build-local.ps1` puis `deploy-render.ps1 -Reason "..."`

Phase B (2026-06) : package `src/aria_core/memory/` — wrappers + vector stub (`aria_vector_memory=false`).  
Phase C : Chroma opt-in — voir `memory/README.md`.