# Piste bancarisée — Velvet Capital comme mécanisme futur de trading réel

Résumé pour intégration future dans la connaissance d'ARIA (pas d'action requise
maintenant — recherche demandée par l'opérateur, 14/07, "plus tard").

## Ce que c'est

Protocole de gestion d'actifs on-chain. Un "vault manager" (ARIA, potentiellement)
crée un vault ; les déposants y mettent des fonds et reçoivent un jeton de vault ;
le manager trade/rééquilibre via le smart contract **sans jamais prendre la garde
des fonds des déposants** (non-custodial par défaut, multisig/MPC en option pour
les institutions).

## Signaux vérifiés (WebSearch, 14/07)

- **Base supportée nativement** (+ Ethereum, BNB, Solana, Hyperliquid, Monad, Sonic).
- **Vraie API programmatique** : `GET https://api.velvet.capital/api/v3/portfolio/owner/<wallet>?chain=base`
  + endpoints dépôt/retrait/rééquilibrage/trade (calldata généré) — documentée sur
  `docs.velvet.capital/for-developers/portfolio-management-api`.
- **7 audits indépendants** (PeckShield, Spearbit, Softstack, Resonance, + concours
  public Hats Finance), monitoring Forta, alertes tokens à risque Webacy, bug bounty actif.
- **Financement réel** : YZi Labs (ex-Binance Labs), DWF Labs, Selini Capital, Mucker Capital.
- **Frais transparents** : le manager fixe ses propres frais (gestion/performance/
  entrée/sortie) ; la plateforme prend 0-25% des frais de performance (dégressif
  selon la taille du vault), 0,02% de frais de trading.

## Réserve honnête

- Incident de phishing sur le frontend (avril 2024, site coupé par précaution,
  aucune perte de fonds rapportée).
- Faille de smart contract documentée et corrigée : délai de flux de prix Chainlink
  permettant un arbitrage de mint de parts.
- Rien trouvé côté hack/rug en 2025-2026, mais ce n'est pas un protocole sans historique.

## Pourquoi ça s'aligne avec la doctrine actuelle (pas un raccourci)

Contrairement au pilote Arena (#60, exécution 100% autonome par conception, hors
de portée de `wallet_guard`), un vault Velvet est un smart contract que la clé du
manager doit signer pour agir -- donc structurellement compatible avec le flux
`wallet_guard`/confirmation Telegram existant (jamais de trade sans validation
humaine sur du capital réel, règle absolue intacte, aucune exception à négocier
comme pour Sepolia/Arena).

Deuxième angle, distinct du trading pour compte propre : un vault PUBLIC Velvet
où des tiers déposent donnerait à ARIA un vrai modèle de frais de gestion sur du
capital externe -- distinct de l'abonnement vitrine (gamme luxe) et de l'ACP déjà
abandonné. Angle à recroiser avec le dossier de positionnement (#78) et le fil
go-to-market (#13) si l'opérateur veut creuser la diversification de revenus.

## Pourquoi ce n'est PAS pour maintenant

Le capital réel côté trading (15%) est explicitement la DEUXIÈME étape du pacte
(`docs/protocole-argent-reel.md`) -- débloquée seulement après que le VC réel (85%)
ait lui-même prouvé le barème des 8 cases sur son propre track-record réel. Rien
à construire avant que cette étape soit atteinte. Cette note sert uniquement à ne
pas reperdre la recherche d'ici là.

## Branches ouvertes (non creusées)

- Comparer Velvet à d'autres vaults non-custodiaux équivalents sur Base (ex.
  Enzyme Finance, Yearn V3 vaults) avant de trancher si Velvet reste le meilleur
  choix le moment venu -- cette recherche n'a couvert QUE Velvet (nommé par
  l'opérateur), pas un vrai comparatif concurrentiel.
- Vérifier le détail exact du contrôle qu'un "vault manager" a réellement sur les
  fonds (limites de rééquilibrage, whitelist de tokens/protocoles autorisés côté
  vault) avant toute intégration -- non vérifié ce soir, sujet à re-creuser en
  détail au moment où cette piste serait activée pour de vrai.
