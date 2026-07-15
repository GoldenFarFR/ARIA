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

## 7. Vérification technique demandée le 15/07 — BLOQUÉE, cause identifiée précisément

Avant de construire quoi que ce soit sur `dex.trades` / `tokens.transfers` /
`prices.usd`, l'opérateur a demandé un vrai test live (pas une supposition sur
la doc) pour confirmer que ces tables contiennent des données Base récentes et
fiables. **Le test n'a pas pu être exécuté** — mais la cause est identifiée
avec précision plutôt que devinée.

**Ce qui a été vérifié** :
- Le serveur MCP `dune` est bien enregistré dans cette session
  (`claude mcp list` le confirme, type HTTP, URL `https://api.dune.com/mcp/v1`).
- Il est marqué **« Needs authentication »** — aucun outil `mcp__dune__*` n'est
  chargeable via `ToolSearch`, confirmé par deux requêtes différentes (query
  `"dune"` puis `"dune sql query execute"`), les deux sans résultat.
- **Cause trouvée dans `~/.claude.json`** : la configuration du serveur MCP
  `dune` contient bien une entrée `x-dune-api-key`, mais sa valeur est le
  **texte littéral `"ta_nouvelle_cle"`** — un placeholder, pas une vraie clé.
  Ce n'est donc pas un problème d'attente de connexion OAuth ni un problème
  Dune côté serveur : **la clé API n'a jamais été renseignée**, malgré la
  mention au §1 ci-dessus d'une clé « générée et enregistrée » le 15/07.
- Recherche exhaustive d'une clé alternative stockée ailleurs (variables
  d'environnement, fichiers `.env*` sur toute la machine, répertoires
  `aria-dune-client`) : **aucune vraie clé trouvée nulle part sur cette
  machine.**
- Cette session (non-interactive) ne peut pas exécuter le flux
  d'autorisation OAuth/interactif nécessaire pour corriger une config MCP —
  seule une session interactive (`/mcp` ou `claude mcp add` avec la vraie
  clé) peut le faire.

**Action corrective concrète, à faire par l'opérateur (pas par ce VPS)** :
régénérer/copier la vraie clé API sur `dune.com` (compte `goldenfarfr`,
palier Free) et remplacer le placeholder `"ta_nouvelle_cle"` par sa valeur
réelle dans la config du serveur MCP `dune` — après quoi une prochaine
session VPS pourra exécuter le vrai test (`execute-sql` sur `dex.trades`
filtré `blockchain = 'base'`, sur une adresse de token Base connue) et
rapporter un résultat réel plutôt qu'une supposition.

### 7bis. RÉSOLU le 15/07 (passage suivant) — test live exécuté avec succès

La clé a été corrigée entre-temps (les outils `mcp__dune__*` sont apparus
disponibles en cours de session). Test réel exécuté sur WETH Base
(`0x4200000000000000000000000000000000000006`), les trois tables
confirmées **vivantes et fiables** :

- **`dex.trades`** — 10 trades Base réels des dernières minutes
  (`block_time` jusqu'à 19:22:05 UTC, requête lancée ~19:2x UTC), projets
  `uniswap` et `0x API` mélangés, paires WETH/USDC, WETH/ZORA, WETH/VIRTUAL,
  WETH/BRIAN, etc. Coût réel : **0,015 crédit**. **Réserve qualité de
  donnée trouvée** : `amount_usd` est `null` sur plusieurs lignes issues du
  projet `0x API` (agrégateur) — la valorisation USD n'est pas garantie sur
  100% des lignes, à filtrer/vérifier avant tout calcul agrégé qui
  suppose `amount_usd` toujours renseigné.
- **`prices.usd`** — prix WETH Base par minute confirmé à jour à la seconde
  près (dernière minute avant l'appel, 1921,43$, cohérent minute par
  minute sur 5 lignes). Coût : 0,104 crédit.
- **`tokens.transfers`** — transferts WETH Base réels dans les dernières
  24h, avec mint/burn visibles (`from`/`to` = adresse zéro). Coût : 0,042
  crédit.

**Total dépensé pour valider les trois tables : 0,161 crédit sur un quota
mensuel de 2500** (`getUsage` confirmé) — négligeable, largement dans le
budget d'un usage périodique décrit au §4. **Verdict : les trois tables
sont bien fiables et à jour pour Base, le plan §3.2 peut être construit
sans réserve technique restante — seule la réserve `amount_usd` null sur
certaines lignes `0x API` doit être gérée dans le code futur
(`COALESCE`/filtre, ne jamais supposer une valeur).**

**Contraste utile** : par comparaison, deux services testés dans le radar de
ce soir (GoPlus Security, Clanker — voir
`docs/aria-learning-inbox/2026-07-15-radar-goplus-clanker-webacy.md`) ont pu
être vérifiés par un vrai appel `curl` en direct, sans aucune clé API —
la fiabilité du process de vérification n'est pas en cause ici, seul le
Dune MCP est bloqué, et pour une raison précise et corrigible.

## 8. Vérification live des DEUX requêtes composées mergées (§3.2/§5, 15/07)

Le §7bis avait vérifié les tables brutes (`dex.trades`/`prices.usd`/
`tokens.transfers`) isolément, pas les deux requêtes SQL composées (CTE)
finalement mergées dans `services/dune.py`
(`build_early_buyer_multiple_query`, `build_recent_base_pairs_query`). Test
demandé séparément — fait ce soir via le serveur MCP `dune` (clé
fonctionnelle, confirmé), paramètres modestes de test, performance `small`.

**Schéma `dex.trades` confirmé colonne par colonne** (via `searchTables`,
avant tout appel Execute SQL) : `blockchain`, `taker`, `token_bought_address`,
`token_bought_amount`, `amount_usd`, `block_time` — **tous les noms de
colonnes supposés par le code sont exacts**, aucune divergence. Point non
documenté auparavant : `token_bought_address`/`token_sold_address`/`taker`
sont typées `varbinary` en interne, mais l'API Execute SQL les restitue déjà
en hex `0x...` dans le JSON de résultat — **aucun décodage supplémentaire
nécessaire côté `dune.py`**, confirmation utile pour l'intégration future.

### 8.1 `build_early_buyer_multiple_query(min_multiple=2.0, lookback_days=7)`

Exécution réussie, **aucune erreur SQL** :
[`https://dune.com/queries/7992486`](https://dune.com/queries/7992486),
118 839 lignes au total, **coût réel : 7,337 crédits**.

Échantillon (10 premières lignes, triées par `peak_multiple` décroissant) :
adresses wallet/token bien formées (`0x` + 40 hex), `launch_time` récent et
cohérent (10-14 juillet 2026, dans la fenêtre `lookback_days=7`).

**RÉSULTAT ABERRANT TROUVÉ (à corriger avant tout usage en prod)** : les
`peak_multiple` des lignes en tête sont numériquement absurdes —
`1.0119556163573924e+22` (≈10 sextillions x) et `505220219833540400`
(≈505 quadrillions x) sur les 9 premières lignes. Aucun token réel ne fait
un tel multiple. Cause probable identifiée par inspection des valeurs
intermédiaires : `launch_price_usd` vaut `3.597860287502912e-14` sur la
ligne la plus aberrante — un prix quasi nul, cohérent avec une division
`amount_usd / token_bought_amount` où `token_bought_amount` est un montant
infinitésimal (jambe de test/dust, ou un token à très grand nombre de
décimales mal normalisé) sur LE PREMIER trade jamais vu de ce token
(`token_launch_price`, `MIN()` sur un seul point de mesure, pas une médiane
ni un filtre de montant minimum). Le diviseur `NULLIF(token_bought_amount, 0)`
protège bien contre la division par zéro exacte, mais pas contre une
division par un montant proche de zéro — **c'est le vrai bug à corriger**,
pas une erreur SQL (la requête tourne et retourne un résultat syntaxiquement
valide, donc `EXECUTE_SQL_LIMIT_1` seul n'aurait pas suffi à l'attraper --
il fallait un vrai test avec des paramètres réels et une inspection des
valeurs, pas seulement du schéma).

Piste de correction (pas implémentée ce soir, hors scope de cette tâche de
vérification) : soit un plancher minimum sur `token_bought_amount`/
`amount_usd` avant de calculer un prix unitaire, soit remplacer
`MIN(block_time)`/prix-au-premier-trade par une médiane sur les N premiers
trades, soit plafonner `peak_multiple` à une valeur jugée déjà extraordinaire
(ex. 1000x) et traiter tout dépassement comme suspect à vérifier manuellement
plutôt qu'un signal brut.

### 8.2 `build_recent_base_pairs_query(min_volume_usd=5000.0, lookback_hours=48)`

Exécution réussie, **aucune erreur SQL** :
[`https://dune.com/queries/7992498`](https://dune.com/queries/7992498),
109 lignes au total, **coût réel : 2,932 crédits**.

Échantillon (10 premières lignes, triées par `volume_usd` décroissant) :
adresses token bien formées, `launch_time` dans la fenêtre 48h demandée
(14-15 juillet 2026), `volume_usd` de 478 153$ à 88 912 609$, `trade_count`
de 949 à 24 838 — **valeurs toutes plausibles, aucune ligne vide ni
aberrante trouvée sur cette requête**. Contraste net avec §8.1 : cette
requête ne divise jamais deux montants d'un même trade (`SUM(amount_usd)`
brut), donc pas exposée au même piège de division par un montant
infinitésimal.

### 8.3 Coût total de cette vérification

7,337 + 2,932 = **10,269 crédits** sur le quota mensuel de 2 500 (0,4%) —
négligeable, cohérent avec le budget décrit au §4.

### 8.4 Verdict

- **`build_recent_base_pairs_query`** : verdict positif sans réserve
  technique restante — schéma confirmé, résultats plausibles, coût minime.
- **`build_early_buyer_multiple_query`** : requête syntaxiquement correcte
  et exécutable, schéma confirmé, MAIS **un vrai bug de qualité de donnée
  trouvé en direct** (multiples aberrants par division sur un prix
  quasi-nul) — **NE PAS considérer cette requête comme fiable en prod avant
  correction** (cf. piste ci-dessus). Documenté ici plutôt que corrigé
  silencieusement : correction = tâche séparée, pas dans le périmètre de
  cette vérification (lecture/documentation seulement, aucun autre fichier
  touché ce soir).
