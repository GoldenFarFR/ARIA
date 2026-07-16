# #205 (1/5) -- Vecteurs d'attaque connus x402/CDP wallet, à blinder avant activation réelle

**Mandat** : rejoint #192 (points faibles IA-trader). Recherche pure, rien construit
côté code -- pertinent pour `docs/pilote-agent-wallet-10usd.md` (pilote 10$ Coinbase
Agent Wallet, pas encore construit) et pour tout futur usage direct de x402 par ARIA.

## 5 attaques nommées sur le protocole x402 (papier académique, arxiv 2605.11781)

Source : [arxiv.org/html/2605.11781v1](https://arxiv.org/html/2605.11781v1) --
"Five Attacks on x402 Agentic Payment Protocol". Chaque attaque a un mécanisme et une
mitigation concrète proposée par les auteurs :

1. **Revert-Grant Under Optimistic Execution (I-A)** -- un serveur livre la ressource
   AVANT que le paiement atteigne la finalité on-chain ; un attaquant exploite l'écart
   en déclenchant une réorganisation de chaîne qui retire la transaction de paiement
   après livraison. **Mitigation** : ne livrer qu'après réserve ou k-finalité
   (attendre une profondeur de confirmation suffisante).
2. **Unauthorized Settlement Preemption (I-B)** -- un observateur du chemin de
   requête (ou un serveur malveillant) extrait l'autorisation de paiement valide de
   l'en-tête `X-PAYMENT` et la règle AVANT le facilitator légitime, consommant le
   nonce -- le client d'origine est débité mais jamais servi. **Mitigation** : lier
   l'identité de l'appelant au facilitator endossé par le payeur (vérification de
   l'appelant au niveau contrat).
3. **Replay/Idempotency Across HTTP-Chain Boundary (II)** -- un paiement valide
   rejoué plusieurs fois : la blockchain impose un nonce à usage unique, mais sans
   vérification d'idempotence côté HTTP, le MÊME paiement peut générer plusieurs
   accès à la ressource. **Mitigation** : lier la portée de la ressource et
   consommer le paiement une seule fois, atomiquement, AVANT de livrer.
4. **HTTP/Proxy-Level Confusion et manipulation d'en-têtes (III)** -- un
   intermédiaire modifie l'en-tête `X-PAYMENT` en transit, ou met en cache une
   réponse payée sans restriction, permettant à un client non payeur de récupérer le
   contenu via un cache partagé. **Mitigation** : `no-store` obligatoire sur les
   réponses payées, isoler les en-têtes de paiement de toute normalisation
   intermédiaire.
5. **Server-Selection Attacks (IV)** -- manipulation des métadonnées de découverte
   ou inondation Sybil de la liste des endpoints x402 pour biaiser la sélection d'un
   agent vers un endpoint malveillant AVANT même l'exécution du paiement.
   **Mitigation** : validation des métadonnées, pondération par réputation,
   diversification du classement contre la manipulation de marketplace.

**Applicabilité à ARIA** : les attaques 1, 2, 3 visent le rôle SERVEUR/facilitator
(exposer un endpoint x402) -- pertinentes seulement si ARIA vend un jour un accès
payant via x402, pas pour un usage CLIENT (payer un service tiers). L'attaque 5
(Server-Selection) est la plus directement pertinente pour ARIA en tant que CLIENT
d'un futur agent-wallet x402 -- même famille de risque que le mandat #192
(manipulation via métadonnées non fiables), mais côté découverte de service plutôt
que côté prompt.

## Incident réel : 402bridge (27-28 octobre, année non confirmée dans les sources --
probablement 2025, antérieur au "07/03/2026" cité pour un autre incident)

**Cause racine précise** (vérifiée, pas supposée) : PAS une faille du protocole
x402 lui-même -- une **fuite de clé privée** a compromis plus d'une douzaine de
wallets test/principal de l'équipe, permettant un transfert de propriété du contrat
vers une adresse malveillante (`0x2b8F`), qui a ensuite exécuté une fonction
`transferUserToken` pour drainer les USDC des wallets ayant déjà accordé une
autorisation ("excessive authorization") au contrat. **Impact réel, à ne pas
surestimer** : ~17 693$ drainés sur 200+ utilisateurs (montant modeste malgré le
nombre de victimes), convertis en 4,2 ETH puis déplacés vers Arbitrum. Sources :
[crypto.news](https://crypto.news/402bridge-hack-leads-to-over-200-users-drained-of-usdc/),
[ainvest](https://www.ainvest.com/news/402bridge-hack-200-users-drained-usdc-due-leaked-admin-private-key-2510/).
**Leçon directement actionnable** : le vecteur réel était la gestion de clé privée
côté équipe (même famille que l'incident `connect.ts` d'ARIA le 09/07 -- la doctrine
"clé privée jamais sur le serveur" reste la bonne réponse), pas un exploit du
protocole de paiement lui-même.

## Audit GoPlus sur l'écosystème x402 (novembre 2025)

Quatre familles de risques identifiées sur des tokens/projets basés x402 :
**excessive authorization** (le pattern exploité par 402bridge), **signature
replay**, **honeypots**, **unlimited minting**. Source :
[CryptoTimes](https://www.cryptotimes.io/2025/11/17/goplus-security-highlights-key-risks-in-x402-crypto-projects/).
GoPlus est déjà le fournisseur honeypot utilisé par ARIA (`services/goplus.py`,
seul garde-fou dur du pipeline momentum #194) -- confirme que cette source reste
pertinente pour un futur usage x402, pas seulement pour le screening de tokens.

## Vulnérabilité SDK datée : 7 mars 2026

Une **faille de contournement de vérification de signature** a été découverte dans
le SDK x402 à cette date (mentionnée dans les résultats de recherche, source
d'origine non revérifiée directement dans cette passe -- à confirmer avant de la
citer comme un fait de premier rang si elle devient pertinente pour une décision
réelle).

## Contrôles concrets recommandés (Halborn, synthèse actionnable)

Source : [Halborn -- x402 Explained](https://www.halborn.com/blog/post/x402-explained-security-risks-and-controls-for-http-402-micropayments).
Huit contrôles, dont plusieurs qu'ARIA a déjà (ou prévoit déjà dans
`docs/pilote-agent-wallet-10usd.md`) et d'autres non encore couverts :
- **Nonces uniques + expiration courte** sur chaque paiement (déjà l'esprit du
  slippage/plafond dur ARIA, transposable).
- **TLS/HSTS/certificate pinning** sur toute communication.
- **Signature du payload + vérification d'intégrité** avant traitement.
- **Validation d'entrée sur toute réponse 402** avant qu'un agent ne la traite --
  ne jamais faire confiance à une demande de paiement sans la valider d'abord (angle
  mandat #192 direct : une réponse 402 est une DONNÉE externe, pas une instruction).
- **Plafonds de dépense durs + liste blanche de destinataires + validation humaine
  sur les transactions à fort montant** -- déjà exactement la doctrine du plan
  `docs/pilote-agent-wallet-10usd.md` (plafond codé, pas de transfert libre, une
  seule adresse pré-enregistrée). Confirme que ce plan est aligné sur les
  recommandations de sécurité externes, pas une prudence excessive isolée.
- **Facilitators multiples (réduire la centralisation) OU vérification on-chain
  directe** -- non encore une décision prise côté ARIA, à trancher si x402 est
  activé.
- **Audits de sécurité des contrats stablecoin utilisés.**
- **Adresses à usage unique** pour casser la chaînabilité (confidentialité).

## Point de sécurité opérationnel CDP wallet (hors x402)

Coinbase documente explicitement : si les identifiants de connexion CDP ou les clés
API sont compromis, **les fonds restent à risque même avec un MPC 2-of-2** --
recommandation de stocker les clés API secrètes dans un coffre dédié (AWS Secrets
Manager, Azure Key Vault). Source :
[docs.cdp.coinbase.com/server-wallets](https://docs.cdp.coinbase.com/server-wallets/v1/concepts/wallets).
**Directement actionnable pour le pilote 10$** : si Coinbase Agentic Wallet est
retenu, les clés API doivent aller dans un coffre dédié sur le VPS, jamais dans le
`.env` en clair au même niveau que les autres secrets -- point à ajouter au plan
avant construction.

## Branches ouvertes

- Vérifier la vulnérabilité SDK du 07/03/2026 à la source primaire avant de la citer
  comme fait de premier rang.
- Lire le papier arxiv 2605.11781 en entier (pas seulement les 5 attaques) pour
  d'éventuelles mitigations transposables au design du futur module ARIA
  (`agent_wallet_pilot.py`, pas encore construit).
- Croiser cette diligence avec le mandat permanent #192 (vulnérabilité à la
  manipulation adversariale) au moment où le pilote 10$ sera réellement construit --
  l'attaque Server-Selection (#5 ci-dessus) est la plus proche du pattern déjà
  corrigé aujourd'hui sur `momentum_entry.py`.
