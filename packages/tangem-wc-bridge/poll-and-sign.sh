#!/usr/bin/env bash
# Waits for an already-created connection to be approved, then requests a
# message signature and captures the result, then closes the session. Takes
# the connectionId as $1. Message-signature only (personal_sign) -- no funds
# move.
set -uo pipefail

BRIDGE="http://127.0.0.1:8787"
CONN_ID="$1"

# Shared bearer token the bridge requires (written by the Node service at
# startup to this 0600 file, or via TANGEM_BRIDGE_TOKEN).
TOKEN="${TANGEM_BRIDGE_TOKEN:-$(cat "${TANGEM_BRIDGE_TOKEN_FILE:-${TMPDIR:-/tmp}/tangem-bridge-token}" 2>/dev/null || true)}"
AUTH=(-H "Authorization: Bearer ${TOKEN}")

echo "Attente de l'approbation de connexion (max 180s)..."
STATUS="pending"; ADDRESS=""
for i in $(seq 1 90); do
  S=$(curl -s "${AUTH[@]}" "$BRIDGE/wc/status?connectionId=$CONN_ID")
  STATUS=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))" 2>/dev/null || echo "err")
  if [ "$STATUS" = "connected" ]; then
    ADDRESS=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin).get('address'))")
    break
  fi
  if [ "$STATUS" = "error" ]; then echo "CONNEXION EN ERREUR: $S"; exit 1; fi
  sleep 2
done
if [ "$STATUS" != "connected" ]; then echo "TIMEOUT connexion (status=$STATUS)"; exit 1; fi

echo "CONNECTE. Adresse: $ADDRESS"
echo "Envoi de la demande de signature -- tape ta carte Tangem quand le telephone l'affiche..."
MSG_HEX="0x48656c6c6f204152494121"
RESULT=$(curl -s -X POST "${AUTH[@]}" "$BRIDGE/wc/request-signature" \
  -H "Content-Type: application/json" \
  -d "{\"connectionId\": \"$CONN_ID\", \"method\": \"personal_sign\", \"params\": [\"$MSG_HEX\", \"$ADDRESS\"]}")
echo "REPONSE: $RESULT"

# Close the session so it can't be reused (audit residual #2).
curl -s -X POST "${AUTH[@]}" "$BRIDGE/wc/disconnect" \
  -H "Content-Type: application/json" \
  -d "{\"connectionId\": \"$CONN_ID\"}" > /dev/null 2>&1 || true
echo "Session fermee."
