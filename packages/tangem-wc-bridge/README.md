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

**Test de bout en bout RÉUSSI (24/07)** — connexion + demande de signature + tap carte +
signature de retour vérifiée cryptographiquement (l'adresse récupérée correspond exactement
au propriétaire Tangem de `aria-smart-st`). Le pont fonctionne.

## Sécurité — modèle de menace (préoccupation opérateur explicite : aucune faille qui viderait un wallet)

**Ce qui te protège structurellement (ne dépend d'aucune ligne de code, donc increvable) :**
1. **La clé privée n'est JAMAIS sur le serveur.** Elle reste sur ta carte Tangem physique.
   Même une compromission totale du VPS ne donne pas la clé — impossible de signer sans la
   carte physique ET ton tap NFC.
2. **Chaque signature exige ton action physique** (tap de la carte). Un attaquant ne peut
   jamais signer tout seul.
3. **Le service écoute UNIQUEMENT sur `127.0.0.1`** — inatteignable depuis Internet. Il
   faudrait déjà être root/local sur le VPS pour l'atteindre (auquel cas il y a des problèmes
   bien plus graves ailleurs).
4. **Ce service n'est PAS un daemon.** Il ne tourne QUE quand tu le lances à la main pour un
   setup, puis tu l'arrêtes. Vérifié : il n'est câblé à aucun démarrage automatique
   (boot/heartbeat/cron/systemd/docker). La fenêtre d'attaque est réduite aux moments où tu
   le lances sciemment.

**Trois durcissements contre "détourner le hash pour vider un wallet" :**

1. **Verrou sur les méthodes dangereuses.** Par défaut, ce pont ne peut demander QUE
   `personal_sign` — signer un MESSAGE texte, ce qui ne déplace **jamais** de fonds, quel que
   soit le réseau (un message peut servir à de l'authentification, donc ce n'est pas "sans
   aucun effet" dans l'absolu — mais il ne bouge aucun fonds). Les deux seules méthodes
   capables de bouger des fonds ou d'autoriser une dépense — `eth_sendTransaction` (une vraie
   transaction) et `eth_signTypedData_v4` (peut être un Permit ERC-2612) — sont **bloquées**
   sauf si tu actives explicitement `TANGEM_BRIDGE_ALLOW_TX_SIGNING=true`.
2. **Secret partagé (auth locale).** Chaque appel au service exige un jeton
   `Authorization: Bearer <token>`. Le service génère ce jeton au démarrage et l'écrit dans un
   fichier `0600` (lisible seulement par ton utilisateur, `<tmp>/tangem-bridge-token`, ou via
   `TANGEM_BRIDGE_TOKEN`). Ça ferme le résidu "n'importe quel process local peut appeler le
   service" — un process d'un autre utilisateur ne peut pas lire le jeton. (Ça ne protège pas
   contre un attaquant déjà root, qui peut tout lire — mais root a déjà tout de toute façon.)
3. **Fermeture de session.** Un endpoint `/wc/disconnect` (appelé automatiquement par les
   scripts en fin de test) ferme la session WalletConnect pour qu'elle ne puisse pas être
   réutilisée pour d'autres signatures pendant que le service tourne encore.

**Le seul résidu honnête** (aucun système n'a zéro risque) : le jour où tu activeras le flag
transaction (nécessaire plus tard pour le grant unique de Spend Permission), un attaquant qui
serait déjà TON utilisateur sur la machine ET pendant une session active pourrait en théorie
glisser une demande de transaction malveillante. **La défense finale, c'est toi : l'app Tangem
affiche TOUJOURS ce que tu signes** (message, ou destinataire + montant d'une transaction) —
lis l'écran avant de taper. Et une fois la migration Smart Account terminée, le plafond de
50$/semaine gravé dans le contrat + la Policy "swap uniquement" limiteront le pire cas même si
une mauvaise transaction passait.

## Lancer le service — usage

Par défaut (le plus sûr, signature de message uniquement) :
```bash
cd packages/tangem-wc-bridge
npm install
WALLETCONNECT_PROJECT_ID=<ton-project-id> TANGEM_BRIDGE_NETWORK=eip155:8453 npm start
```
`TANGEM_BRIDGE_NETWORK=eip155:8453` (Base mainnet) est nécessaire car **Tangem refuse les
testnets en WalletConnect** (vérifié 24/07). C'est sans danger tant que seule la signature
de message est autorisée. Le test tout-en-un : `bash test-signature.sh`.
