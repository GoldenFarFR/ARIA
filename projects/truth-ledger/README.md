# Truth Ledger (projet Python)

Scaffold standalone — ledger JSON avec filelock, loguru, pydantic-settings.

## CI

Le workflow monorepo `.github/workflows/main.yml` ne cible **que** ce dossier (pas tout ARIA).

```powershell
ruff check .
ruff format --check .
black --check .
pytest
```