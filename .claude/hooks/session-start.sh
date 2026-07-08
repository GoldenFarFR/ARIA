#!/bin/bash
# SessionStart hook — prépare l'environnement de test d'aria-core pour Claude Code on the web.
# But : une nouvelle session peut lancer pytest / vérifier IMMÉDIATEMENT, sans réinstaller
# l'environnement à la main. Reproduit la recette de la CI (.github/workflows/ci.yml).
#
# aria-core exige Python >= 3.12, or le python par défaut du conteneur est 3.11 : on crée
# un venv 3.12 dédié (.venv, gitignoré) et on le rend actif pour toute la session via
# $CLAUDE_ENV_FILE, pour que `python` et `pytest` pointent dessus.
set -euo pipefail

# Web (remote) uniquement : en local l'environnement est déjà celui du dev.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-/home/user/ARIA}"
CORE="$ROOT/packages/aria-core"
VENV="$CORE/.venv"
cd "$CORE"

# Interpréteur 3.12 (repli sur python3/python si absent).
PY="$(command -v python3.12 || command -v python3 || command -v python)"

# Venv dédié, idempotent (réutilisé s'il existe déjà — le conteneur est mis en cache).
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
# aria-core en editable + dépendances de test (pytest…).
"$VENV/bin/pip" install -e ".[dev]"

# Rend le venv actif pour toute la session : `python` et `pytest` -> 3.12 + aria-core.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

echo "aria-core: venv Python 3.12 prêt — 'pytest' disponible depuis packages/aria-core."
