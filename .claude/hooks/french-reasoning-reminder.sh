#!/bin/bash
# Rappel injecté à CHAQUE message opérateur (UserPromptSubmit) : le raisonnement
# interne visible (pas seulement la réponse finale) doit rester en français sur ce
# projet — CLAUDE.md ligne 52. Ajouté le 22/07 après plusieurs rappels manuels
# insuffisants dans une même session (le raisonnement recommençait en anglais malgré
# la règle déjà connue) -- un rappel frais injecté à chaque tour, plutôt qu'une
# règle lue une fois en début de session, est le mécanisme le plus fiable disponible.
cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"RAPPEL PERMANENT (CLAUDE.md ligne 52) : reste entièrement en français sur ce projet -- y compris le bloc de raisonnement interne visible, pas seulement la réponse finale. Ce rappel est injecté à chaque message car ce point a déjà dérapé plusieurs fois dans la même session malgré la règle déjà connue."}}
EOF
