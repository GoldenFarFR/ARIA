# Recherche écosystème Virtuals / launchpads Base — 11/07/2026

> Note brute déposée par une session Claude Code avec accès web réel (pas les
> sessions cloud, bloquées par la liste blanche de domaines). Faits externes,
> déclaratifs — à vérifier/recouper avant toute intégration dans
> `knowledge/*.yaml`/`canonical_facts.yaml`, comme toute note de cet inbox.

## Robinhood Chain intègre l'infra agent de Virtuals (annoncé 02/07/2026)

Robinhood Chain va intégrer l'infrastructure d'agents IA de Virtuals Protocol
dès son lancement, permettant aux utilisateurs de créer/financer/déployer des
agents autonomes sur des marchés tokenisés (les agents pourraient exécuter des
stratégies ou du market-making pour le compte d'utilisateurs). Potentiellement
pertinent pour la thèse produit d'ARIA (écosystème Virtuals en expansion vers
un acteur retail mainstream) — non mentionné dans les docs ARIA actuelles
(datées jusqu'au 10/07). Source : couverture TradingView/CoinMarketCal,
02/07/2026.

## ACP Node v2 publié (04/05/2026)

Mise à niveau majeure de l'infra du réseau d'agents ACP (fiabilité,
performance), plus une mise à jour de la gouvernance des contrats du protocole
(30/04) et des améliorations de l'outillage CLI (30/04). ARIA documente l'ACP
comme "abandonné" (routage conversationnel désactivé par défaut) — cette
mise à jour ne remet pas en cause cette décision produit, mais pourrait
changer le calcul si le sujet revient (nouvelle version, pas la même
maturité qu'au moment de la décision d'abandon).

## Launchpads Base — statut réel confirmé (Bankr, Ape.store, Mint.club)

Ces trois launchpads sont mentionnés dans `knowledge/launchpads.yaml` avec
`confidence: unverified` (adaptateurs `discover=None`, jamais de client
construit). Confirmé actifs et documentés publiquement :
- **Bankr** : lance des tokens sur Base et renvoie l'adresse du token + les
  métadonnées de la pool Uniswap V4 créée. Doc API : `docs.bankr.bot`.
- **Ape.store** : lancement de token quasi gratuit (frais de gas seul), listé
  immédiatement sur Uniswap dès que le market cap atteint 69K, LP brûlée et
  contrat renoncé automatiquement à ce seuil.
- **Mint.club** : plateforme no-code de tokens à courbe de bonding
  personnalisable, sur n'importe quel actif ERC20, migration du token MT vers
  Base Chain.

Aucune adresse de contrat vérifiée récupérée ce passage (recherche web
généraliste, pas d'inspection on-chain) — à traiter avec la même doctrine
« profondeur proportionnelle à l'enjeu » que les autres launchpads avant de
construire un vrai client.

## Limite de cette note

Recherche web généraliste (pas d'accès on-chain direct depuis cet outil pour
ce passage), aucun fait vérifié en direct sur un contrat réel. À recouper
avant intégration, comme toute note de cet inbox.
