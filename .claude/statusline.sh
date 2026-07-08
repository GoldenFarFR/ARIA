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

ctx_int=${ctx%.*}
h5_int=${h5%.*}
d7_int=${d7%.*}

# --- Reco modèle (règle ARIA) ----------------------------------------------
# Sonnet xhigh par défaut ; Opus ponctuel sur wallet/sécu ; alerte si quotas/contexte hauts.
if [ "${ctx_int:-0}" -ge 80 ] 2>/dev/null; then
  reco="🧹 contexte plein → /compact"
elif [ -n "$d7_int" ] && [ "$d7_int" -ge 85 ] 2>/dev/null; then
  reco="⚠️ hebdo ${d7_int}% → reste en Sonnet"
elif echo "$model_id" | grep -qi "opus"; then
  reco="🔴 Opus actif — réserve au wallet/sécu"
else
  reco="🟢 Sonnet xhigh (🔴 Opus si wallet/sécu)"
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
line="${setup}⚡ ${model} · ctx ${ctx_int:-0}%"
line="${line} · \$$(printf '%.2f' "$cost")"
[ -n "$h5_int" ] && line="${line} · 5h ${h5_int}%"
[ -n "$d7_int" ] && line="${line} · 7j ${d7_int}%"
[ -n "$branch" ] && line="${line} · ⎇ ${branch} (+${added}/-${removed})"
line="${line} · ${reco}"

printf "%s" "$line"
