#!/usr/bin/env bash
# UserPromptSubmit hook -- ALERTE SYSTEM_ISSUES EN COURS DE SESSION.
#
# Pourquoi ce hook existe (17/08, remarque opérateur qui a invalidé la
# conception précédente) : `system-issues-reminder.sh` est un hook
# SessionStart. Il ne se déclenche donc qu'au DÉMARRAGE d'une session. Or
# l'opérateur redémarre très rarement ("je redémarre une nouvelle session
# très rarement tu risques d'en rater beaucoup") -- et les sessions de ce
# projet durent des heures. Câbler les watchdogs vers `system_issues` (fait
# le 17/08 pour log-health-watch/memory-watch/outgoing-pause-watch) ne
# suffisait donc pas : une anomalie détectée en milieu de session ne
# m'atteignait qu'à la session SUIVANTE, potentiellement des jours plus
# tard. On avait juste déplacé le problème qu'on croyait résoudre
# (dépendre de l'opérateur pour relayer une alerte Telegram).
#
# Ce hook comble ce trou : il tourne à CHAQUE message de l'opérateur, donc
# une anomalie remonte en quelques minutes au lieu d'attendre un
# redémarrage.
#
# Anti-bruit (indispensable, sinon il devient inutilisable) : chaque issue
# n'est signalée QU'UNE FOIS par session, via un fichier d'état qui mémorise
# les ids déjà annoncés -- même patron que
# `.architect-pending-reminded-state` dans session-checkpoint.sh. Une issue
# rouverte plus tard porte un nouvel id et sera donc bien re-signalée.
#
# Ne bloque JAMAIS le prompt : toute erreur est absorbée, exit 0 systématique.
set -uo pipefail

cat >/dev/null 2>&1 || true   # draine le JSON d'entrée (non utilisé)

ROOT="${CLAUDE_PROJECT_DIR:-/opt/aria}"
DB="/opt/aria-data/aria.db"
STATE="$ROOT/.claude/.system-issues-alerted"

[ -f "$DB" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

# -readonly : ce hook ne doit JAMAIS pouvoir écrire dans la base de prod.
# .timeout plutôt que PRAGMA busy_timeout : le PRAGMA ÉCHO sa valeur sur
# stdout en mode -cmd, ce qui polluerait le contexte injecté (vrai bug
# rencontré le 17/08 sur log-health-watch/run.sh).
# Volontairement limité à warning/critical. Le niveau `info` (surtout
# file-staleness-watch, qui signale des docs à relire) n'est PAS une
# anomalie : le remonter à chaque message noierait les vraies alertes -- 8
# lignes de bruit au premier test, exactement ce qui rend un mécanisme
# d'alerte inutilisable. Les `info` restent visibles au démarrage de session
# via system-issues-reminder.sh, qui lui les liste toutes.
OPEN_IDS=$(sqlite3 -readonly -cmd ".timeout 3000" "$DB" \
  "SELECT id FROM system_issues WHERE status='open' \
   AND lower(severity) IN ('warning','critical','error') ORDER BY id" 2>/dev/null) || exit 0
[ -n "$OPEN_IDS" ] || exit 0

touch "$STATE" 2>/dev/null || true
NEW_IDS=""
for id in $OPEN_IDS; do
  grep -qx "$id" "$STATE" 2>/dev/null || NEW_IDS="${NEW_IDS}${id} "
done
[ -n "$NEW_IDS" ] || exit 0

DETAILS=""
for id in $NEW_IDS; do
  row=$(sqlite3 -readonly -cmd ".timeout 3000" "$DB" \
    "SELECT '#'||id||' ['||severity||'] ('||source||') '||title||' -- '||substr(detail,1,180) \
     FROM system_issues WHERE id=$id" 2>/dev/null)
  [ -n "$row" ] && DETAILS="${DETAILS}- ${row}"$'\n'
  echo "$id" >> "$STATE" 2>/dev/null || true
done
[ -n "$DETAILS" ] || exit 0

cat <<EOF
🚨 NOUVELLE(S) ANOMALIE(S) SYSTÈME détectée(s) pendant cette session :
${DETAILS}
Ces alertes viennent d'un watchdog mécanique (log-health-watch, memory-watch,
outgoing-pause-watch, vc-watch, file-staleness-watch...) et arrivent EN DIRECT
-- ne pas attendre un redémarrage de session pour les traiter.
Traite-la maintenant si elle touche la prod/le capital, sinon signale-la à
l'opérateur en UNE ligne et continue la demande en cours. Une fois réglée (ou
jugée faux positif), ferme-la : aria_core.system_issues.close_issue(id, raison).
EOF
exit 0
