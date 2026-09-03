#!/bin/bash
# COGNITIVE RUNTIME -- loads the epistemic brain at every session start AND
# after every compaction. Wired on SessionStart (no matcher, all sources).
#
# Why this hook exists (03/09, explicit operator requirement): a document the
# model CAN read is a document it will eventually not read. The brain must not
# depend on a voluntary decision by the model -- it must already be active
# before the session starts reasoning.
#
# The pattern is already proven on this project: french-reasoning-reminder.sh
# re-injects a rule on EVERY message because it kept drifting despite its
# presence in CLAUDE.md. Same mechanism, applied to cognitive governance.
#
# Decisive technical point, already verified here on 03/08: PostCompact exists
# but its stdout is IGNORED. SessionStart with matcher "compact" is the only
# event whose stdout is actually injected after a compaction. This hook is
# therefore wired with no matcher (every startup) and covers both cases.
#
# This hook does NOT inject the full 35KB brain: 100% verified is not 100% kept
# in active context. It injects identity (version + fingerprint, the proof),
# the invariants that must never be lost, and the pointer. Everything else is
# routed on demand. "Permanent" does not mean "verbose".
set -uo pipefail

# Paths overridable by tests (never by normal usage) -- so a test can supply a
# fixture or verify drift detection WITHOUT ever touching the real brain or
# polluting the real production trace.
BRAIN="${COGNITIVE_RUNTIME_BRAIN_OVERRIDE:-/opt/aria/docs/cerveau-epistemique-sessions.md}"
REGRESSIONS="${COGNITIVE_RUNTIME_REGRESSIONS_OVERRIDE:-/opt/aria/docs/regressions-cognitives.md}"
TRACE_DIR="${COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE:-/opt/aria-data/cognitive-runtime}"
TRACE="$TRACE_DIR/loaded.log"
mkdir -p "$TRACE_DIR" 2>/dev/null || true

# --- Session identity: read from the hook's stdin JSON, never invented ------
# Fixed 03/09: the previous version discarded stdin (`cat >/dev/null`) then
# read ${CLAUDE_SESSION_ID:-unknown}, a variable the harness never sets
# (confirmed against code.claude.com/docs/en/hooks: SessionStart carries
# session_id/source as stdin JSON, never as an environment variable -- no
# other hook in this project uses CLAUDE_SESSION_ID). Real consequence of the
# bug: every trace line said "session=unknown", so drift detection (the PREV
# line below) never found a line "from another session" to compare against --
# it was silently inoperative from the start.
INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)"
SOURCE="$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null || true)"
SESSION_ID="${SESSION_ID:-unknown}"
SOURCE="${SOURCE:-unknown}"

# --- Brain identity: declared version + real fingerprint --------------------
if [ ! -r "$BRAIN" ]; then
  # Fail-visible, never fail-silent: a session without a brain must KNOW it.
  # Payload stays in French, same convention as french-reasoning-reminder.sh
  # and session-compact-reminder.sh: this text governs the model's runtime
  # reasoning for a French-language project, it is not repo documentation.
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

# --- Mechanical proof: who loaded what, and when -----------------------------
# A model message saying "brain loaded" proves nothing. This line does.
echo "$NOW session=$SESSION_ID source=$SOURCE protocol=${VERSION:-UNVERSIONED} hash=$HASH sections=$SECTIONS regressions=$NREG" >> "$TRACE" 2>/dev/null || true

# --- Brain change detection ---------------------------------------------------
PREV=$(grep -v "session=$SESSION_ID " "$TRACE" 2>/dev/null | tail -1 | grep -oE 'hash=[a-f0-9]+' | cut -d= -f2)
DRIFT=""
if [ -n "$PREV" ] && [ "$PREV" != "$HASH" ]; then
  DRIFT=" ATTENTION : le cerveau a CHANGE depuis la derniere session (empreinte $PREV -> $HASH) -- relis-le integralement avant toute mission significative, une regle a pu etre ajoutee ou retiree."
fi

# --- Injected context: identity + invariants + routing, never the full text -
# Payload stays in French (see the fail-visible branch above for why): it
# governs the model's runtime reasoning on a French-language project, same
# convention as every other hook that injects context to the model rather
# than documenting code for a human reader.
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
