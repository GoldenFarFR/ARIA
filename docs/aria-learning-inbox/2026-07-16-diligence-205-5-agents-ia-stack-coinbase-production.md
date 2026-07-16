# #205 (5/5) -- Agents IA en production sur la pile Coinbase (Agentic Wallet/x402) --
complète la cartographie AIXBT/ai16z du 16/07

**Contraste net avec la cartographie précédente** (AIXBT/Luna/KellyClaude/ai16z/
Freysa/Wayfinder, note du 16/07) : cette pile-ci est **B2B/infrastructure**, pas des
personas spéculatives à token propre. Aucun des agents "célèbres" recensés
précédemment (AIXBT, Luna) n'a été trouvé comme s'appuyant sur x402/Agentic Wallet
dans cette recherche -- les deux écosystèmes semblent aujourd'hui largement
disjoints (Virtuals/persona-tokens d'un côté, Coinbase/infra-paiement de l'autre).

## Coinbase Agentic Wallets -- lancé le 11 février 2026, chiffres de production
vérifiés

- **Lancement** : 11 février 2026, premier produit de wallet explicitement conçu
  pour des agents IA autonomes (dépense/gains/trading avec garde-fous intégrés).
  Sources : [Coinbase -- Introducing Agentic Wallets](https://www.coinbase.com/developer-platform/discover/launches/agentic-wallets),
  [Genfinity](https://genfinity.io/2026/02/11/coinbase-agentic-wallets-ai-agents/).
- **Bundle de skills curées** (distinct des 19 action providers d'AgentKit, cf.
  #205 3/5) : **Authenticate, Fund, Send, Trade, Earn** -- un sous-ensemble plus
  simple, orienté produit fini plutôt que framework complet.
- **Garde-fous natifs, à comparer directement au plan ARIA** : plafonds de dépense
  avec limites de session ET plafonds par transaction -- *"agents peuvent dépenser
  un montant fixé par jour mais jamais plus qu'un plafond spécifié par
  transaction"*. **C'est exactement le patron déjà conçu indépendamment dans
  `docs/pilote-agent-wallet-10usd.md`** (plafond dur codé, vérification de solde
  avant chaque transaction) -- confirme que ce plan est aligné sur le standard
  produit de Coinbase lui-même, pas une prudence isolée ou excessive.

## x402 -- chiffres de production divergents selon la source, à rapporter
honnêtement

Deux jeux de chiffres trouvés, non réconciliés dans cette recherche (probablement
des instantanés à des dates différentes, mais aucune source ne date précisément la
mesure) :
- Une source cite **"over 50M transactions"** (battle-tested).
- Une autre cite **"165 million transactions and 69,000 active agents at $50M
  cumulative volume"**.
**Traitement honnête** : ces deux chiffres ne sont PAS la même mesure (le premier
ressemble à un chiffre antérieur, le second à un chiffre plus récent et plus
complet incluant le nombre d'agents actifs) -- présentés ici sans trancher lequel
est le plus à jour, à revérifier à la source officielle x402 avant de citer un
chiffre précis dans un document destiné à l'opérateur ou un dossier externe.
Sources : [BlockEden](https://blockeden.xyz/blog/2026/02/24/coinbase-agentic-wallets-autonomous-ai-payments-2026/),
synthèse de recherche croisée (non vérifiée à la source x402 elle-même dans cette
passe).

**Ce qui est solide, indépendamment du chiffre exact** : x402 est un protocole en
**production réelle et à échelle mesurable** (dizaines de millions de transactions,
dizaines de milliers d'agents actifs) -- pas un concept encore expérimental.
Contraste net avec les agents-personas spéculatifs de la cartographie précédente
(AIXBT, Luna, KellyClaude), dont l'activité "de production" se limite largement au
trading spéculatif sur leur propre token.

## Cas d'usage concrets trouvés (au-delà des chiffres agrégés)

- **AWS** a publié un article sur x402 pour les paiements agentiques en services
  financiers -- signal que l'intérêt dépasse le seul écosystème crypto natif.
  Source : [AWS Industries blog](https://aws.amazon.com/blogs/industries/x402-and-agentic-commerce-redefining-autonomous-payments-in-financial-services/).
- **1Shot API** (gagnant CDP Grants, cf. #205 4/5) est un exemple direct d'usage
  x402 en production : une couche d'automatisation on-chain où des agents paient
  l'accès à une API directement on-chain.

## Conclusion actionnable

**Aucun concurrent direct trouvé sur le créneau précis d'ARIA** (analyse VC +
preuve + validation humaine) au sein de la pile Coinbase non plus -- confirme le
constat déjà posé dans la cartographie du 16/07 (aucun concurrent direct trouvé
nulle part dans cette recherche, deux passes indépendantes). La pile Coinbase
(Agentic Wallet/x402) est en revanche un candidat sérieux d'INFRASTRUCTURE pour le
pilote 10$ d'ARIA (déjà en cours de réflexion) -- ses garde-fous natifs recoupent
déjà le plan ARIA, ce qui est un signal de cohérence rassurant plutôt qu'une
nouvelle contrainte à intégrer.

## Branches ouvertes

- Réconcilier les deux chiffres de volume x402 (50M vs 165M transactions) à la
  source primaire avant toute citation externe.
- Chercher spécifiquement si un agent d'ANALYSE (pas juste d'exécution de paiement)
  tourne sur la pile x402/Agentic Wallet -- cette recherche n'a trouvé que des
  agents d'exécution (paiement, rendement, réservation), jamais un agent de
  diligence/analyse comparable à ARIA.
- Base MCP (déjà banqué dans la note #198 du même jour) pourrait recouper cette
  pile -- à vérifier ensemble si un jour creusé.
