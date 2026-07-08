#!/bin/bash
# SessionStart hook (ASYNCHRONE) — prépare l'environnement de test d'aria-core pour
# Claude Code on the web, EN ARRIÈRE-PLAN, avec suivi de progression en %.
#
# La session démarre immédiatement ; l'installation tourne en tâche de fond et écrit son
# avancement dans .claude/.setup-status (lu par la barre de statut -> « env NN% »). Quand
# c'est fini, le statut passe à « ready » et l'indicateur disparaît.
#
# aria-core exige Python >= 3.12 (le python par défaut du conteneur est 3.11) : on crée un
# venv 3.12 dédié (.venv, gitignoré) et on exporte son PATH via $CLAUDE_ENV_FILE.
#
# Note (mode async) : tant que le % n'est pas à « ready », l'environnement de test n'est pas
# encore prêt — le % sert justement à savoir quand c'est bon.
set -uo pipefail

# Rend la main immédiatement ; le reste s'exécute en arrière-plan.
echo '{"async": true, "asyncTimeout": 600000}'

# Web (remote) uniquement : en local l'environnement est déjà celui du dev.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-/home/user/ARIA}"
CORE="$ROOT/packages/aria-core"
VENV="$CORE/.venv"
STATUS="$ROOT/.claude/.setup-status"
TARGET=34   # nb de paquets attendus (aria-core[dev] + deps) pour estimer le %

write() { printf '%s\n' "$1" > "$STATUS" 2>/dev/null || true; }
fail() { write "error"; exit 0; }

mkdir -p "$ROOT/.claude" 2>/dev/null || true
write "installing 3"

cd "$CORE" || fail

PY="$(command -v python3.12 || command -v python3 || command -v python)"
if [ ! -x "$VENV/bin/python" ]; then
  write "installing 8"
  "$PY" -m venv "$VENV" || fail
fi
write "installing 15"

"$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true

# Installation en tâche de fond + progression par comptage des paquets installés.
"$VENV/bin/pip" install -e ".[dev]" >/tmp/aria-pip-setup.log 2>&1 &
PIP_PID=$!
while kill -0 "$PIP_PID" 2>/dev/null; do
  n=$(ls -d "$VENV"/lib/python*/site-packages/*.dist-info 2>/dev/null | wc -l | tr -d ' ')
  pct=$(( 15 + (n * 80 / TARGET) ))
  [ "$pct" -gt 95 ] && pct=95
  write "installing $pct"
  sleep 1
done
wait "$PIP_PID" || fail

# PATH de la session : `python` et `pytest` -> venv 3.12 + aria-core.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

write "ready"
