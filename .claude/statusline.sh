#!/bin/bash
# Status line ARIA — version complète.
# Affiche : modèle · contexte% · coût session · quota 5h · quota hebdo · git · reco modèle.
#
# Installer :
#   1) déposer dans ~/.claude/statusline.sh
#   2) chmod +x ~/.claude/statusline.sh
#   3) dans ~/.claude/settings.json :
#      { "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" } }
# Nécessite `jq`.

input=$(cat)

# --- Champs bruts -----------------------------------------------------------
model=$(echo "$input"    | jq -r '.model.display_name // "?"')
model_id=$(echo "$input" | jq -r '.model.id // ""')
ctx=$(echo "$input"      | jq -r '.context_window.used_percentage // 0')
cost=$(echo "$input"     | jq -r '.cost.total_cost_usd // 0')
h5=$(echo "$input"       | jq -r '.rate_limits.five_hour.used_percentage // empty')
d7=$(echo "$input"       | jq -r '.rate_limits.seven_day.used_percentage // empty')
branch=$(echo "$input"   | jq -r '.git.branch // empty')
added=$(echo "$input"    | jq -r '.total_lines_added // 0')
removed=$(echo "$input"  | jq -r '.total_lines_removed // 0')
dir=$(echo "$input"      | jq -r '.workspace.current_dir // .cwd // ""')
tok=$(echo "$input"      | jq -r '.context_window.total_input_tokens // 0')

ctx_int=${ctx%.*}
tok_k=$(( ${tok:-0} / 1000 ))
h5_int=${h5%.*}
d7_int=${d7%.*}

# --- Reco modèle (règle ARIA) ----------------------------------------------
# Sonnet xhigh par défaut ; Opus ponctuel sur wallet/sécu ; alerte si quotas/contexte hauts.
if [ "${tok:-0}" -ge 500000 ] 2>/dev/null; then
  reco="🧹 ${tok_k}k tokens (≥500k) → /compact"
elif [ "${ctx_int:-0}" -ge 80 ] 2>/dev/null; then
  reco="🧹 contexte plein → /compact"
elif [ -n "$d7_int" ] && [ "$d7_int" -ge 85 ] 2>/dev/null; then
  reco="⚠️ hebdo ${d7_int}% → reste en Sonnet"
elif echo "$model_id" | grep -qi "opus"; then
  reco="🔴 Opus actif — réserve au wallet/sécu"
else
  reco="🟢 Sonnet xhigh (🔴 Opus si wallet/sécu)"
fi

# --- Checkpoint session (compteur de messages tous les 1000) ---------------
# Affiche l'approche du prochain checkpoint auto (« chk NN/1000 ») pour que l'opérateur
# le voie venir ; le hook UserPromptSubmit injecte le rappel pile au 1000e.
chk=""
cf="${dir}/.claude/.msg-counter"
CHECKPOINT_INTERVAL=1000
if [ -f "$cf" ]; then
  cn=$(cat "$cf" 2>/dev/null)
  case "$cn" in
    ''|*[!0-9]*) cn="" ;;
  esac
  if [ -n "$cn" ]; then
    pos=$(( cn % CHECKPOINT_INTERVAL ))
    [ "$pos" -eq 0 ] && pos=$CHECKPOINT_INTERVAL
    chk="📌 chk ${pos}/${CHECKPOINT_INTERVAL} · "
  fi
fi

# --- Lignes non déployées sur le VPS (alimenté par le hook checkpoint) ------
# Affiche « 🚀 Nk non-dépl. » dès qu'il y a du delta ; alerte au-delà du seuil (4000).
deploy=""
uf="${dir}/.claude/.undeployed-lines"
if [ -f "$uf" ]; then
  ud=$(cat "$uf" 2>/dev/null)
  case "$ud" in
    ''|*[!0-9]*) ud="" ;;
  esac
  if [ -n "$ud" ] && [ "$ud" -gt 0 ]; then
    if [ "$ud" -ge 4000 ]; then
      deploy="🚀 ${ud} l. à déployer · "
    elif [ "$ud" -ge 500 ]; then
      deploy="🚀 ${ud} l. non-dépl. · "
    fi
  fi
fi

# --- Progression du setup (hook asynchrone) --------------------------------
# Affiche l'avancement de l'installation de l'environnement (venv + deps) tant qu'elle
# tourne en arrière-plan, puis disparaît quand c'est prêt.
setup=""
sf="${dir}/.claude/.setup-status"
if [ -f "$sf" ]; then
  s=$(cat "$sf" 2>/dev/null)
  case "$s" in
    installing*) setup="🔧 env ${s#installing }% · " ;;
    error)       setup="⚠️ env échec · " ;;
    *)           setup="" ;;
  esac
fi

# --- Construction de la ligne ----------------------------------------------
line="${deploy}${chk}${setup}⚡ ${model} · ctx ${ctx_int:-0}% (${tok_k}k tok)"
line="${line} · \$$(printf '%.2f' "$cost")"
[ -n "$h5_int" ] && line="${line} · 5h ${h5_int}%"
[ -n "$d7_int" ] && line="${line} · 7j ${d7_int}%"
[ -n "$branch" ] && line="${line} · ⎇ ${branch} (+${added}/-${removed})"
line="${line} · ${reco}"

printf "%s" "$line"
