# Bankr — diligence approfondie 11/07/2026

> Note brute, recherche web réelle depuis le VPS (docs officielles
> `docs.bankr.bot`). Déclaratif — à vérifier/recouper avant intégration dans
> `knowledge/*.yaml`/`canonical_facts.yaml`, comme toute note de cet inbox.
> Complète (ne remplace pas) la note du même jour sur l'écosystème Virtuals.

## Ce qu'est réellement Bankr

Pas seulement un launchpad de tokens : une infrastructure financière complète
pour agents IA ("agents financièrement autonomes, les frais de trading paient
le compute"). Trois piliers : gestion de wallet cross-chain, trading/DeFi, et
lancement de tokens dont les frais autofinancent l'agent.

## Lancement de token (le point déjà noté dans l'autre note, précisé ici)

- Endpoint `POST /token-launches/deploy`, authentification par
  `X-Partner-Key` (niveau organisation) OU `X-API-Key`/`Authorization: Bearer`
  (niveau wallet) — **pas un accès anonyme/gratuit**, nécessite un partenariat
  ou une clé.
- Sous le capot : protocole **Doppler** (hooks Uniswap V4). Frais de swap
  0,7% sur la pool, répartis 95% créateur / 5% protocole (Doppler).
- Tokenomics fixe : 100 milliards de tokens à l'émission, 85% amorce la
  liquidité, 15% vest au destinataire des frais sur 2 ans (cliff 30 jours).
- Chaînes : Base par défaut, Robinhood Chain en option (`"chain":"robinhood"`)
  — cohérent avec l'intégration Virtuals x Robinhood Chain notée dans l'autre
  fichier.

## Modèle de garde (custody)

**Non-custodial confirmé pour les wallets externes** : les wallets externes
(Safe multisig, hardware wallets, signataires tiers) passent par l'endpoint
public dédié `POST /public/doppler/build-claim` (batch-capable) pour les
opérations d'auto-garde — Bankr ne détient pas les clés de ces wallets.

## Portée au-delà du token launch

Multi-chaîne large : Base, Ethereum, Polygon, Unichain, World Chain,
Arbitrum, BNB Chain, Robinhood Chain, Solana, Hyperliquid. Intégration
possible via : skill installable, API REST avec clé, CLI, **plugins Claude**
(intégration directe), x402 Cloud (paiement agentique), passerelle LLM
compatible OpenAI avec suivi d'usage.

## Pertinence pour ARIA — signaux de légitimité (doctrine "profondeur
## proportionnelle à l'enjeu")

Points positifs : docs publiques complètes, modèle non-custodial pour wallets
externes, mécanique de frais transparente (Doppler, vérifiable on-chain).
Points à vérifier avant tout branchement réel : nécessite une clé
API/partenariat (pas un simple client HTTP anonyme comme DexScreener/
GeckoTerminal déjà utilisés) — donc un vrai coût d'intégration et une
dépendance à un tiers pour toute action d'écriture. Aucune adresse de
contrat Doppler/Bankr vérifiée récupérée ce passage (recherche documentaire,
pas d'inspection on-chain).

## Limite de cette note

Recherche documentaire (docs.bankr.bot), pas de test API réel (pas de clé),
pas de vérification on-chain des contrats Doppler sous-jacents. À recouper
avant intégration.
