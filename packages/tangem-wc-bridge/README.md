# tangem-wc-bridge

Minuscule service local (Node.js) qui permet à ARIA (le code Python) de demander une
signature à ta carte Tangem, via WalletConnect. **Ce service ne détient jamais ta clé
privée** — il transmet juste une demande à l'app Tangem sur ton téléphone, et tu
approuves en tapant physiquement ta carte.

Voir `docs/HANDOFF_COINBASE_CDP.md` pour le contexte complet (pourquoi ce service existe,
ce qui reste à construire).

## Statut actuel

**Prototype, testnet uniquement, jamais branché à du capital réel.** Rien n'est encore
relié au reste du code ARIA — ce service tourne isolé, pour être testé indépendamment.

## Avant de lancer : obtenir un Project ID (gratuit)

1. Aller sur https://cloud.reown.com (anciennement WalletConnect Cloud).
2. Créer un compte gratuit, créer un projet.
3. Copier le "Project ID" affiché.

## Lancer le service

```bash
cd packages/tangem-wc-bridge
npm install
WALLETCONNECT_PROJECT_ID=<ton-project-id> npm start
```

Le service écoute uniquement sur `127.0.0.1:8787` (jamais accessible depuis l'extérieur).
Par défaut, il utilise Base Sepolia (testnet, aucune valeur réelle) — le réseau réel
(mainnet) n'est jamais utilisé sans changer explicitement `TANGEM_BRIDGE_NETWORK`.

## Tester manuellement (avec ta vraie app Tangem)

1. Démarrer une connexion :
   ```bash
   curl -X POST http://127.0.0.1:8787/wc/connect
   ```
   Réponse : `{"uri": "wc:...", "connectionId": "conn_..."}`

2. Ouvrir l'app Tangem sur ton téléphone → section WalletConnect → coller ce `uri` (ou
   scanner si tu le transformes en QR toi-même). Approuver la connexion.

3. Vérifier que la connexion est passée à "connected" :
   ```bash
   curl "http://127.0.0.1:8787/wc/status?connectionId=conn_..."
   ```
   Réponse attendue : `{"status": "connected", "address": "0x..."}`

4. Demander une signature (exemple : signer un message simple) :
   ```bash
   curl -X POST http://127.0.0.1:8787/wc/request-signature \
     -H "Content-Type: application/json" \
     -d '{"connectionId": "conn_...", "method": "personal_sign", "params": ["0x68656c6c6f", "0xTON_ADRESSE"]}'
   ```
   Ton téléphone doit afficher une demande d'approbation — tape ta carte Tangem pour
   confirmer. La réponse contient la signature une fois approuvée.

**Ce test manuel avec ta vraie carte est l'étape de validation qui manque encore** — le
code côté Python (`aria_core.tangem_bridge`) est déjà écrit et testé, mais seul un test
réel avec ton téléphone confirme que le pont fonctionne de bout en bout.
