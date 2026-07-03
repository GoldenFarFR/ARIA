# Truth Ledger (projet Python)

Scaffold standalone — ledger JSON avec filelock, loguru, pydantic-settings.

## Portage aria-core (2026-07-03)

Patterns repris dans `packages/aria-core/src/aria_core/truth_ledger/io.py` :
écriture atomique (temp + replace), filelock multi-process sur mises à jour markdown.
SQLite + sync GitHub inchangés.

## CI

Le workflow monorepo `.github/workflows/main.yml` ne cible **que** ce dossier (pas tout ARIA).

```powershell
ruff check .
ruff format --check .
black --check .
pytest
```