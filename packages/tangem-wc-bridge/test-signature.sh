#!/usr/bin/env bash
# One-shot end-to-end test of the Tangem bridge -- connect, wait for the
# operator to approve on their phone, request a message signature, capture the
# result. Avoids the manual copy/paste/Ctrl-C juggling that desynced earlier
# sessions. Message-signature only (personal_sign) -- never a transaction, so
# no funds move regardless of the network the session runs on.
#
# Usage: bash test-signature.sh
set -euo pipefail

BRIDGE="http://127.0.0.1:8787"

echo "==> 1/4  Ouverture d'une connexion WalletConnect..."
RESP=$(curl -s -X POST "$BRIDGE/wc/connect")
CONN_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['connectionId'])")
URI=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['uri'])")

echo
echo "    Copie CETTE URI et colle-la dans l'app Tangem (WalletConnect > coller),"
echo "    puis approuve la connexion. NE COLLE PAS l'URI dans ce terminal."
echo
echo "-----------------------------------------------------------------------"
echo "$URI"
echo "-----------------------------------------------------------------------"
echo
echo "==> 2/4  Attente de ton approbation de connexion sur le telephone (max 120s)..."

STATUS="pending"
ADDRESS=""
for i in $(seq 1 60); do
  S=$(curl -s "$BRIDGE/wc/status?connectionId=$CONN_ID")
  STATUS=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")
  if [ "$STATUS" = "connected" ]; then
    ADDRESS=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin).get('address'))")
    break
  fi
  if [ "$STATUS" = "error" ]; then
    echo "    Connexion refusee ou en erreur : $S"
    exit 1
  fi
  sleep 2
done

if [ "$STATUS" != "connected" ]; then
  echo "    Toujours pas connecte apres 120s -- relance le script et approuve plus vite."
  exit 1
fi

echo "    Connecte. Adresse : $ADDRESS"
echo
echo "==> 3/4  Envoi de la demande de signature (message 'Hello ARIA!', AUCUN mouvement de fonds)."
echo "         Ton telephone va afficher une demande -- tape ta carte Tangem pour approuver."
echo "         (le script attend la reponse, ne fais rien ici)"
echo

# "Hello ARIA!" en hexadecimal + l'adresse remontee par la connexion.
MSG_HEX="0x48656c6c6f204152494121"
RESULT=$(curl -s -X POST "$BRIDGE/wc/request-signature" \
  -H "Content-Type: application/json" \
  -d "{\"connectionId\": \"$CONN_ID\", \"method\": \"personal_sign\", \"params\": [\"$MSG_HEX\", \"$ADDRESS\"]}")

echo "==> 4/4  Reponse du pont :"
echo
echo "$RESULT"
echo
if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('result') else 1)" 2>/dev/null; then
  echo "    SUCCES -- signature reelle recue de bout en bout. Le pont Tangem fonctionne."
else
  echo "    Pas de signature dans la reponse -- voir le message d'erreur ci-dessus."
fi
