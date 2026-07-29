# Base MCP -- vérification à la source officielle (29/07)

**Déclencheur** : l'opérateur a partagé un lien X (`x.com/i/status/2059305572793508045`,
redirige vers `x.com/base/status/...`) en demandant si c'est intéressant pour ARIA.
Vérifié en direct via navigateur (compte `@base` certifié, contenu réel capturé par
capture d'écran + extraction de texte -- pas une inférence de recherche Google, qui ne
renvoyait rien d'exploitable sur ce lien précis).

**Referme une branche ouverte le 16/07** (`2026-07-16-veille-base-198-jesse-pollak-
leadership-grants.md`) : "Base MCP (mai 2026) -- vérifier à la source officielle (pas
seulement une synthèse tierce) ce qu'il couvre exactement avant de le considérer comme
une piste sérieuse." C'est fait -- source primaire lue intégralement.

## Ce que c'est réellement

Post officiel `@base`, 26 mai 2026 (donc en place depuis ~2 mois, pas une nouveauté du
jour -- seule sa portée réelle n'avait jamais été vérifiée). **Base MCP** connecte le
**Base Account** d'un humain (son compte Base App) à n'importe quel client compatible
MCP -- la liste citée EXPLICITEMENT inclut **Claude Web/Desktop/Code, ChatGPT, Codex,
Cursor**. Une fois authentifié (OAuth 2.1, même standard que "Sign in with Google"),
l'agent peut : suivre le portefeuille, voir l'historique, envoyer des fonds, swapper,
et utiliser des "skill plugins" pour des apps de l'écosystème Base.

**Skills au lancement** : Moonwell (lending), Morpho (lending/vaults), Uniswap (swaps/
liquidité), Avantis (perps), Bankr (lancements de tokens), Aerodrome (LP/swaps),
Virtuals (lancements d'agents/tokens). Une des capacités listées explicitement :
**"Pay for x402 enabled services."**

## Modèle de sécurité -- point le plus important pour ARIA

"Nothing happens onchain without your explicit approval." Le serveur MCP **ne détient
et n'accède jamais aux clés privées**. L'agent construit la transaction et la stocke
comme "pending request" (primitive "stored requests", déjà utilisée pour les paiements
Shopify Base Pay) ; un lien est envoyé, le Base Account de l'humain l'ouvre dans une
fenêtre séparée, simule les changements d'actifs, et l'utilisateur confirme ou annule.
**Aucune exécution autonome sans confirmation humaine, jamais, structurellement** --
philosophie identique à la règle absolue d'ARIA sur le capital réel (validation humaine
avant tout mouvement mainnet), mais portée par un rail natif Base/OAuth plutôt que par
Telegram/`wallet_guard.escalate_spend`.

## Pertinence pour ARIA -- verdict d'intégration

- **Ne recoupe PAS les clients de données existants d'ARIA** (DexScreener/GeckoTerminal/
  Blockscout/GoPlus) -- Base MCP est orienté EXÉCUTION côté compte d'un humain, pas
  lecture de données de marché pour un moteur d'analyse. Répond directement à la
  question laissée ouverte le 16/07 : portée différente, pas de doublon, rien à
  remplacer dans le pipeline d'analyse.
- **Piste de gouvernance à comparer un jour (pas urgent, rien construit)** : le modèle
  "stored request + lien de confirmation Base Account" pourrait être une alternative ou
  un complément futur à `wallet_guard.escalate_spend` (Telegram) pour le pilote
  agent-wallet réel -- mais il exige TOUJOURS une confirmation humaine par transaction,
  donc il ne remplace ni ne concurrence l'exécution autonome bornée déjà accordée à
  ARIA sur son pilote (Exceptions nommées #3/#4, plafond 10-15$) : ce serait un rail
  pour un usage humain direct (l'opérateur lui-même, depuis Claude Code), pas pour ARIA
  en autonomie.
- **Canal de demande potentiel pour le futur x402-seller d'ARIA** (Item #39/#188,
  dormant, gates OFF) : la capacité "Pay for x402 enabled services" listée dans Base
  MCP signifie qu'un utilisateur tiers pourrait un jour payer pour le score wallet
  composite d'ARIA via ce rail, sans qu'ARIA ait besoin de construire son propre client
  payeur. À garder en tête si/quand le x402-seller passe en mainnet -- rien à faire
  maintenant.
- **Confirme la légitimité des choix déjà faits par ARIA**, sans rien ajouter de
  nouveau à construire : les 7 skills au lancement (Morpho, Virtuals, Bankr, Uniswap
  notamment) recoupent des protocoles déjà identifiés/intégrés côté ARIA (module Yield
  Morpho évalué #32/Item #194 en attente, bonding Virtuals déjà en prod, client Doppler
  Bankr déjà construit #93/94) -- Base elle-même valide ces protocoles comme piliers de
  l'écosystème.

## Détail trouvé en passant, hors sujet Base MCP

La bio du compte `@base` affirme littéralement : **"Base is beginning to explore a
network token."** Signal de première main (pas une rumeur tierce, le compte officiel
lui-même) -- connecte directement à `2026-07-27-diligence-launchpads-tokenisation-
aria.md` (diligence tokenisation ARIA). Aucun détail concret au-delà de cette phrase à
ce jour.

## Branches ouvertes

- Revérifier périodiquement si "Base is beginning to explore a network token" se
  concrétise (calendrier, mécanisme, implications pour le paysage concurrentiel d'une
  éventuelle tokenisation ARIA).
- Si un jour le x402-seller d'ARIA passe en mainnet, vérifier concrètement si Base MCP
  devient une voie d'accès réelle pour des payeurs tiers (pas seulement théorique).
- Comparer formellement (session dédiée, pas maintenant) le modèle "stored request +
  lien Base Account" vs `wallet_guard.escalate_spend` si l'opérateur veut un jour un
  canal d'approbation alternatif à Telegram pour le capital réel.
