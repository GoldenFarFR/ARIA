#!/usr/bin/env bash
# Waits for an already-created connection to be approved, then requests a
# message signature and captures the result. Takes the connectionId as $1.
# Message-signature only (personal_sign) -- no funds move.
set -uo pipefail

BRIDGE="http://127.0.0.1:8787"
CONN_ID="$1"

echo "Attente de l'approbation de connexion (max 180s)..."
STATUS="pending"; ADDRESS=""
for i in $(seq 1 90); do
  S=$(curl -s "$BRIDGE/wc/status?connectionId=$CONN_ID")
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
RESULT=$(curl -s -X POST "$BRIDGE/wc/request-signature" \
  -H "Content-Type: application/json" \
  -d "{\"connectionId\": \"$CONN_ID\", \"method\": \"personal_sign\", \"params\": [\"$MSG_HEX\", \"$ADDRESS\"]}")
echo "REPONSE: $RESULT"
