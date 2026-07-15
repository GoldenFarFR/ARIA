# Intégration Dune Analytics — plan de recherche (15/07, PAS ENCORE construit)

> **Statut : PLAN SEULEMENT.** Rien de ce document n'est codé. Recherche faite à la
> demande opérateur (« regarde s'il n'y a pas d'autre chose à extraire comme donnée
> au-delà des wallets » + « fais une recherche élargie »), à construire plus tard.

## 1. Pourquoi ce document existe

Dune Analytics a été identifié comme source **gratuite** (2 500 crédits/mois, palier
Free) pour sourcer des wallets candidats (cf. `docs/pilote-agent-wallet-10usd.md`
n'est pas le bon fichier — voir plutôt l'entrée CLAUDE.md du 15/07 sur
`wallet_candidate_sourcing.py`). En creusant plus large, Dune s'avère offrir bien
plus que juste des wallets : c'est un entrepôt de données on-chain complet,
interrogeable par SQL personnalisé.

Compte opérateur créé le 15/07 (`goldenfarfr` sur dune.com, palier Free). Clé API
générée et enregistrée comme serveur MCP dans cette session cloud
(`claude mcp add ... dune https://api.dune.com/mcp/v1`) — utilisable directement
comme outil par une session Claude Code, pas encore branché dans le code ARIA
lui-même (aria-core n'a aucun client `services/dune.py` à ce jour).

## 2. Écarté après vérification — ne pas reconsidérer sans nouvelle info

**Sim (ex-Echo)** — le produit Dune de wallet temps réel multi-chaînes (soldes,
transactions enrichies) — est **en cours de fermeture** : nouvelles inscriptions
désactivées, sunset annoncé au 1er août 2026. Ne pas construire dessus.

## 3. Branches confirmées, utiles à ARIA

### 3.1 Découverte de paires DEX (`/v1/dex/pairs/{chain}`)
Endpoint REST classique (pas du SQL) qui agrège les statistiques de paires à
travers tous les DEX d'une chaîne donnée, y compris Base. Deuxième source
**indépendante** de découverte de tokens, en plus de DexScreener/GeckoTerminal
déjà utilisés par `base_crawler.discover_top_pools`. Répond directement au
rendez-vous de vérification déjà noté au backlog (#134, « débit de scan élargi »).

### 3.2 Requêtes SQL personnalisées (Query Execution API) — la vraie pépite
Dune expose un moteur SQL complet sur des données on-chain indexées et
normalisées (`DuneSQL`). Deux façons de l'utiliser via API :
- **Execute SQL** (`/api-reference/executions/endpoint/execute-sql`) : exécute
  une requête SQL brute directement, sans avoir à la sauvegarder dans l'UI Dune
  d'abord. Le plus flexible pour un usage programmatique.
- **Create Query + Execute Query** : sauvegarde une requête réutilisable
  (`query_id`), puis l'exécute avec des paramètres (`execute-query`), récupère
  le résultat via `execution_id` (JSON, CSV, ou dataframe Pandas côté SDK
  Python). Résultats conservés 90 jours (`expires_at`).

Tables pertinentes pour Base :
- `dex.trades` — chaque segment de swap DEX (cross-chain, filtrable par
  `blockchain = 'base'`), utile pour reconstruire des patterns d'achat précoce.
- `erc20_base.evt_Transfer` (spellbook, convention
  `{protocole}_{chaîne}.{Contrat}_evt_{Événement}`) et `tokens.transfers`
  (vue unifiée cross-chain des transferts ERC-20).
- `tokens.erc20` — métadonnées token (nom/symbole/décimales/adresse).
- `prices.usd` — prix indexés par minute/heure (alternative/complément à
  GeckoTerminal pour la valorisation historique).

**Idée concrète, pas encore construite** : une requête SQL sur-mesure
« wallets ayant acheté dans la première heure d'un token Base qui a ensuite fait
≥Nx » — un filtre taillé exactement pour la thèse ARIA (VC précoce), potentiellement
plus pertinent que le classement générique Nansen (écarté, payant) ou que notre
propre historique interne (`wallet_candidate_sourcing.py`, limité aux tokens
qu'ARIA a déjà elle-même jugés gagnants).

### 3.3 Datasets normalisés (secondaire, pas urgent)
Stablecoins, DeFi/lending, NFT trades — pourrait nourrir un futur signal macro
(ex. flux de stablecoins entrants sur Base = liquidité qui arrive), dans le même
esprit que l'overlay macro déjà existant (`btc_cycles.py`). Rien d'urgent, banqué.

## 4. Coût et limites

- Palier Free : 2 500 crédits/mois. Exécution de requête : 10 crédits (medium,
  défaut) à 20 crédits (large). Export/lecture de résultats : 1 crédit ≈ 1 000
  points de données sur Free. Donc un budget de l'ordre de quelques centaines
  d'exécutions de requêtes moyennes par mois — largement suffisant pour un usage
  périodique (pas un usage temps réel à haute fréquence).
- Toujours vérifier le nom exact des champs contre un vrai appel réel avant de
  considérer une intégration terminée (norme de process actée le 14/07, incident
  Blockscout `address_hash`) — ne jamais faire confiance à un schéma deviné de
  mémoire.

## 5. Ce qui serait construit (une fois le « go » donné)

- `services/dune.py` (patron dôme habituel : throttle/backoff, lecture seule) —
  wrapper autour de l'API Execute SQL, réutilisable par plusieurs skills futurs.
- Une requête SQL dédiée (§3.2) pour le sourcing de wallets, en complément (pas
  en remplacement) de `wallet_candidate_sourcing.py` — à activer seulement si le
  débit de la source interne ARIA s'avère insuffisant une fois en prod (cf.
  décision opérateur du 15/07 : viser ≥5 tokens sourcés/semaine).
- Éventuellement, exploiter `/v1/dex/pairs/{chain}` comme second flux de
  découverte de tokens pour `base_crawler.py` (#134).

## 6. Branches ouvertes (banquées, pas creusées)

- Requête Dune pour croiser/valider les prix OHLCV (`prices.usd`) contre
  GeckoTerminal — pourrait renforcer `price_confirmation_ratio` (#161).
- Flux stablecoins Base comme signal macro additionnel.
- Le serveur MCP officiel de Dune (`https://api.dune.com/mcp/v1`), déjà
  enregistré dans cette session cloud, pourrait remplacer une partie du client
  `services/dune.py` prévu — à évaluer si un serveur MCP est exploitable
  directement depuis le code aria-core en production (probablement non — MCP
  est un protocole d'outils pour agents, pas pour un service backend ; le client
  REST/SQL classique reste la voie normale pour le code de production).
