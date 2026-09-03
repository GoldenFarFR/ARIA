#!/bin/bash
# COGNITIVE RUNTIME -- charge le cerveau epistemique a chaque demarrage de session
# ET apres chaque compactage. Branche sur SessionStart (tous matchers).
#
# Pourquoi ce hook existe (03/09, exigence operateur explicite) : un document que
# le modele PEUT lire est un document qu'il finira par ne pas lire. Le cerveau ne
# doit pas dependre d'une decision volontaire du modele -- il doit etre deja actif
# quand la session commence a penser.
#
# Le patron est deja prouve sur ce projet : french-reasoning-reminder.sh reinjecte
# une regle a CHAQUE message parce qu'elle derapait malgre sa presence dans
# CLAUDE.md. Meme mecanisme, applique a la gouvernance cognitive.
#
# Point technique decisif, deja verifie ici le 03/08 : PostCompact existe mais son
# stdout est IGNORE. SessionStart avec matcher "compact" est le seul evenement dont
# le stdout est reellement injecte apres un compactage. Ce hook est donc branche
# sans matcher (tous demarrages) et couvre les deux cas.
#
# Ce hook n'injecte PAS les 35 Ko du cerveau : 100 % verifie n'est pas 100 % garde
# en contexte actif. Il injecte l'identite (version + empreinte, la preuve), les
# invariants qui ne doivent jamais etre perdus, et le pointeur. Le reste est route
# a la demande. "Permanent" ne veut pas dire "verbeux".
set -uo pipefail

# Chemins surchargeables par les tests (jamais par un usage normal) -- pour
# qu'un test puisse fournir une fixture ou verifier la detection de derive
# SANS jamais toucher au vrai cerveau ni polluer la vraie trace de production.
BRAIN="${COGNITIVE_RUNTIME_BRAIN_OVERRIDE:-/opt/aria/docs/cerveau-epistemique-sessions.md}"
REGRESSIONS="${COGNITIVE_RUNTIME_REGRESSIONS_OVERRIDE:-/opt/aria/docs/regressions-cognitives.md}"
TRACE_DIR="${COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE:-/opt/aria-data/cognitive-runtime}"
TRACE="$TRACE_DIR/loaded.log"
mkdir -p "$TRACE_DIR" 2>/dev/null || true

# --- Identite de session : lue dans le JSON stdin du hook, jamais inventee ----
# Corrige le 03/09 : la version precedente jetait stdin (`cat >/dev/null`) puis
# lisait ${CLAUDE_SESSION_ID:-unknown}, une variable que le harness ne definit
# jamais (confirme contre code.claude.com/docs/en/hooks : SessionStart transmet
# session_id/source par JSON sur stdin, pas par variable d'environnement -- aucun
# autre hook du projet n'utilise CLAUDE_SESSION_ID). Consequence reelle du bug :
# toute ligne de trace portait "session=unknown", donc la detection de derive
# (ligne PREV plus bas) ne trouvait jamais de ligne "d'une autre session" a
# comparer -- elle etait silencieusement inoperante depuis l'origine.
INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)"
SOURCE="$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null || true)"
SESSION_ID="${SESSION_ID:-unknown}"
SOURCE="${SOURCE:-unknown}"

# --- Identite du cerveau : version declaree + empreinte reelle ----------------
if [ ! -r "$BRAIN" ]; then
  # Fail-visible, jamais fail-silent : une session sans cerveau doit le SAVOIR.
  cat <<'MISSING'
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"⚠️ COGNITIVE RUNTIME INDISPONIBLE : docs/cerveau-epistemique-sessions.md est introuvable ou illisible. Cette session n'est PAS gouvernee par le cerveau epistemique. Signale-le a l'operateur avant toute mission significative -- ne poursuis pas en supposant que les invariants cognitifs s'appliquent."}}
MISSING
  exit 0
fi

VERSION=$(grep -oE 'BRAIN-PROTOCOL: [A-Z-]+ v[0-9.]+' "$BRAIN" 2>/dev/null | head -1 | sed 's/BRAIN-PROTOCOL: //')
HASH=$(sha256sum "$BRAIN" 2>/dev/null | cut -c1-12)
SECTIONS=$(grep -c '^## ' "$BRAIN" 2>/dev/null)
NREG=$(grep -c '^## COGNITIVE-' "$REGRESSIONS" 2>/dev/null || echo 0)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# --- Preuve mecanique : qui a charge quoi, et quand ---------------------------
# Un message du modele disant "brain loaded" ne prouve rien. Cette ligne, si.
echo "$NOW session=$SESSION_ID source=$SOURCE protocol=${VERSION:-UNVERSIONED} hash=$HASH sections=$SECTIONS regressions=$NREG" >> "$TRACE" 2>/dev/null || true

# --- Detection de changement de cerveau ---------------------------------------
PREV=$(grep -v "session=$SESSION_ID " "$TRACE" 2>/dev/null | tail -1 | grep -oE 'hash=[a-f0-9]+' | cut -d= -f2)
DRIFT=""
if [ -n "$PREV" ] && [ "$PREV" != "$HASH" ]; then
  DRIFT=" ATTENTION : le cerveau a CHANGE depuis la derniere session (empreinte $PREV -> $HASH) -- relis-le integralement avant toute mission significative, une regle a pu etre ajoutee ou retiree."
fi

# --- Contexte injecte : identite + invariants + routage, jamais le texte entier -
python3 - "$VERSION" "$HASH" "$SECTIONS" "$NREG" "$DRIFT" <<'PY'
import json, sys
version, h, sections, nreg, drift = sys.argv[1:6]
ctx = f"""COGNITIVE RUNTIME ACTIF -- protocole {version or 'UNVERSIONED'}, empreinte {h}, {sections} sections, {nreg} regressions cognitives.{drift}

Cerveau epistemique deja actif -- tu ne decides pas de l'activer, seulement quel mode cognitif, quelle profondeur, quelles specs/skills mobiliser DANS ce cadre.

INVARIANTS :
1. La question n'est pas « est-ce vrai ? » mais « avions-nous le droit de conclure que c'est vrai ? »
2. UNKNOWN n'est jamais PASS par defaut. UNKNOWN apres investigation serieuse est une REUSSITE, pas un echec.
3. UNKNOWN veut dire « ne pretends pas savoir », JAMAIS « n'agis pas » -- l'incertitude alimente le sizing, ne bloque pas le trading.
4. Resultat surprenant -> attaque l'INSTRUMENT avant le phenomene : quelle erreur de mesure produirait ce resultat ?
5. Ce qui est sur disque n'est pas ce qui est execute. Une montee de version se verifie sur le processus, pas le fichier.
6. « Rien trouve » n'est pas « rien n'existe » tant que la couverture n'est pas demontree.
7. Ne confonds pas accomplir la DEMANDE et accomplir la MISSION -- reconstruis l'objectif terminal avant d'agir.
8. Le producteur d'une preuve n'est jamais son juge ; la diversite cognitive ne prouve pas l'independance.

PROFONDEUR ADAPTATIVE : minimale sur mission triviale, controles cibles en normal, protocole adversarial complet si fort impact, protocole maximal si financier/securite -- jamais d'enquete sur une question simple.

SILENCE PAR DEFAUT : n'expose le cerveau que sur refus de conclure, contradiction, UNKNOWN important, doute sur un instrument, ou changement de strategie.

SOURCES : docs/cerveau-epistemique-sessions.md (protocole complet, a relire avant mission significative ou apres compactage) ; docs/regressions-cognitives.md (erreurs deja vues -- verifie une ressemblance AVANT de commencer)."""
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
PY
