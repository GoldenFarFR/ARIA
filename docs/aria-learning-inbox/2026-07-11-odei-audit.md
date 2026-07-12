[VPS Research]

# Audit ODEI / ODAI — diligence produit + token + évaluation du concept

Date : 2026-07-11
Méthode : recherches ciblées directes (WebSearch/WebFetch/API GitHub/API publique odei.ai), pas de fan-out multi-agents (règle projet appliquée : jamais d'orchestration lourde par défaut).
Sources : voir liens inline. Toutes les données on-chain/API ont été interrogées en direct le jour de l'audit — les chiffres bougent vite (micro-cap).

## Résumé exécutif

- **Le produit tourne réellement** (backend live, API publique fonctionnelle, données on-chain réelles) mais **le code source du "vrai" produit n'est pas public** — tous les repos GitHub "produit" sont des coquilles vides intentionnelles.
- **L'équipe est un seul homme identifié nommément** (pas anonyme), mais c'est une micro-structure solo, pas une équipe.
- **Le token a une tokenomie propre et vérifiable** (fair launch sans allocation équipe, supply fixe), mais une **traction utilisateur quasiment nulle** (74 comptes au total) et une **liquidité de micro-cap** (~100-450 k$ selon la source/le jour) — donc hautement spéculatif et fragile.
- **Le concept technique (world model persistant + gouvernance de policy + logs auditables) n'est pas une innovation** : c'est une instance bien exécutée d'un pattern déjà largement documenté et déjà disponible en open source (Neo4j + knowledge graph agent memory, Graphiti, Cognee, MAGMA, EverMemOS, etc., 2026). Le delta réel vs l'état de l'art public est faible. Le delta vs ce qu'ARIA a déjà (truth-ledger, memory/) est plus intéressant à documenter que ce qu'ODEI apporte en propre.

---

## 1. Produit réel : code vs marketing

**Fait vérifié.** L'organisation GitHub `odei-ai` (https://github.com/odei-ai) contient 12 repos publics : `.github`, `examples`, `mcp-odei`, `memory`, `odei-agentkit`, `odei-ai.github.io`, `odei-app`, `research`, `trust-protocol`, `web`, `wmaas-sdk`, `odei-sherlock-scope`.

**Fait vérifié (via API GitHub).** Sur ces 12 repos, **9 font exactement 2 Ko et n'ont qu'un seul commit intitulé "Initial public showcase"**, poussé le 26 mai 2026, jamais retouché depuis (`memory`, `odei-agentkit`, `trust-protocol`, `wmaas-sdk`, `mcp-odei`, `odei-app`, `research`, `examples`, `web`). Le contenu de ces repos (vérifié en lisant les README bruts) est un texte-type identique répété d'un repo à l'autre :

> "This repository is intentionally minimal. It exists to publish the project surface, security contact, and integration direction without exposing production infrastructure or private runtime code."

Autrement dit : **ce sont des placeholders assumés, pas du code produit**. Le "memory graph", le "trust-protocol", l'"agentkit", le "MCP server" — tout ce qui constitue le cœur technique revendiqué — n'existe nulle part en public. C'est une architecture fermée (closed-source SaaS), ce qui est un choix légitime en soi, mais qui contredit la mise en scène GitHub façon "projet ouvert avec plein de repos" : la présence de 9 repos vides donne une illusion de substance qu'une lecture du code dément immédiatement.

**Fait vérifié.** Le seul repo avec du contenu réel est `odei-sherlock-scope` (583 Ko, créé le 3 juin 2026) — mais ce n'est **pas du code ODEI propriétaire** : c'est un dossier de scope pour un audit de sécurité (marketplace Sherlock), contenant les ABI et le code source récupéré de contrats **Flaunch** (le protocole de "fair launch" tiers utilisé pour émettre le token ODAI : `agent-registry`, `bidwall`, `fee-escrow`, `memecoin-implementation`, `revenue-manager`, `treasury-action-manager`, plus le contrat `odai-token` et `odai-treasury`). C'est donc un artefact lié au token, pas au produit IA.

**Fait vérifié (API live testée directement).** `api.odei.ai` sert un **vrai backend fonctionnel**, pas juste une landing page :
- `/health` → uptime réel (~5,6 jours au moment du test), version `1.0.0`.
- `/openapi.json` → schéma OpenAPI v0.2.0 avec **37 endpoints réels** : `/api/worldmodel/*`, `/api/token/price`, `/api/token/holders`, `/api/intake/*`, `/.well-known/mcp.json`, `/.well-known/x402.json` (protocole de paiement agent-à-agent), etc.
- `/api/token/price` renvoie des données live cohérentes avec Dexscreener (prix, liquidité ~120 k$, volume 24h ~24 k$, market cap ~437 k$).
- `/api/token/holders` renvoie 3 795 holders (source Basescan) vs 6 005 (source Blockscout) — **écart signalé et documenté explicitement par l'API elle-même** (`"discrepancy":2210`), ce qui est plutôt un bon point de transparence technique.
- `/api/intake/stats` renvoie **74 comptes au total** (8 agents, 65 humains, 1 entreprise) — donné brut, non gonflé.

**Déduction.** Les pages "vision" (`api.odei.ai/worldmodel/`, `/network/`) sont bien des pages de démonstration/marketing (aucun schéma technique, aucun endpoint testable dans la page elle-même) — mais elles pointent vers une vraie API documentée séparément (`/openapi.json`), donc ce n'est pas du pur vaporware marketing : il y a un vrai runtime derrière, seulement la doc grand public est en mode vitrine plutôt que référence développeur.

**Verdict point 1** : Produit **partiellement réel** — backend/API vivants et cohérents avec les données on-chain, mais **le cœur du produit IA revendiqué (memory graph, policy engine, agent governance) n'est pas vérifiable dans le code** puisqu'il est fermé. Impossible de confirmer que "constitutional world model", "guardian enforcement" etc. font ce qu'ils prétendent — c'est une boîte noire.

---

## 2. Équipe

**Fait vérifié.** Fondateur identifié nommément : **Anton Illarionov**, exploitant en nom propre ("sole proprietor") à Budapest, Hongrie. Immatriculation citée sur `odei.ai/company/` : n° 61955583, Tax ID 91815600-1-42.
Page LinkedIn de l'entreprise trouvée : linkedin.com/company/odei-ai/ — taille déclarée "2-10 employés", mais **un seul nom apparaît réellement** dans le contenu récupéré.

**Incertitude non résolue.** Pas trouvé de profil LinkedIn personnel distinct pour Anton Illarionov avec historique professionnel antérieur vérifiable (le lien "Charles Odei" remonté par la recherche est une coïncidence de nom, sans rapport). Pas d'historique de contributions GitHub antérieur à ODEI identifié pour ce fondateur — impossible de confirmer un passé technique crédible en dehors du projet lui-même.

**Verdict point 2** : Équipe **non anonyme mais non-traçable en profondeur** — un seul nom, une structure d'entreprise individuelle réelle et déclarée (pas une coquille offshore anonyme), mais aucune preuve indépendante d'expérience antérieure. C'est mieux qu'un projet anonyme, très loin d'une équipe établie et vérifiable.

---

## 3. Token ODAI

**Fait vérifié (page `api.odei.ai/tokenomics/`).**
- Supply totale fixe : 100 000 000 000 (100 Md), sur Base.
- Allocation revendiquée : **0% équipe, 0% pré-vente**, 100% "fair launch" via **Flaunch V1.2** (protocole tiers), plafond 0,25% max par wallet pendant une fenêtre de 30 min à prix uniforme, verrouillage anti-dump pendant la fenêtre.
- Mint : "no unrestricted creator-controlled mint is asserted" — minting cadré par le protocole Flaunch/bridge uniquement. **Non vérifié indépendamment on-chain** (l'appel Basescan pour lire les fonctions du contrat a échoué faute de clé API — à refaire si une vérification on-chain indépendante est jugée nécessaire avant toute décision).
- Utilité revendiquée : accès réseau ODEI, alignement trésorerie (1% de frais de swap financent l'infra), monnaie de paiement pour les transactions d'agents crypto-natifs, sièges DAO à venir "upon app release" (donc pas encore effectif).

**Fait vérifié (Coinbase, CryptoRank, Dexscreener, WEEX).**
- La page Coinbase citée (`coinbase.com/price/odei-ai-base-...`) est une **page d'affichage de prix on-chain** (comme pour n'importe quel token indexé), **pas une preuve de listing/trading sur l'exchange Coinbase** — à ne pas confondre. Idem CryptoRank/CoinGecko : agrégateurs de prix, pas des cautions de qualité.
- **WEEX a bien annoncé un listing exclusif ODAI/USDT le 18 mars 2026** — ça, c'est un vrai listing d'exchange (WEEX est un exchange de second rang, pas Coinbase/Binance).
- Liquidité réelle observée : pool Uniswap V4 sur Base, ~100-120 k$ de liquidité, market cap ~440 k$-920 k$ selon la source et le moment (les chiffres varient fortement selon qui interroge quand — cohérent avec un micro-cap volatil, pas un signal de manipulation en soi mais un signal de fragilité).
- Prix en chute : -18,5% sur 7 jours selon WEEX au moment de la recherche, volume 24h en baisse de -65% un jour donné — activité de marché faible et instable.

**Verdict point 3** : Tokenomie **plus propre que la moyenne des tokens spéculatifs** (pas d'allocation équipe déclarée, fair launch, transparence sur les écarts de données), mais **utilité produit encore largement prospective** ("upon app release"), **liquidité et capitalisation de micro-cap**, **pas de vérification indépendante possible du mint authority on-chain** dans cet audit. Pas un signal de fraude caractérisée, mais un profil de risque élevé typique d'un token spéculatif à un stade très précoce.

---

## 4. Traction réelle

**Fait vérifié (API `/api/intake/stats`, donnée live, non déclarative marketing).** **74 comptes au total** : 65 humains, 8 agents, 1 entreprise. C'est un nombre extrêmement faible pour un projet qui communique depuis mai 2026 avec un token coté sur un exchange.

**Fait vérifié (GitHub).** Activité de code **quasi nulle** : tous les repos "produit" ont un seul commit datant du 26 mai 2026, aucun commit depuis. Le seul repo actif (`odei-sherlock-scope`) n'a que 2 commits, début juin, et ne contient pas de code produit.

**Fait trouvé.** Une mention "Proof of Usefulness" via un hackathon HackerNoon (`proofofusefulness.com/reports/odei`) — mention réelle mais dans un cadre de hackathon, pas une couverture presse indépendante.

**Verdict point 4** : Traction **très faible et non démontrée** au-delà du marketing/hype crypto sur X. Ni adoption produit significative, ni activité de développement soutenue, ni couverture presse sérieuse indépendante identifiée.

---

## 5. Évaluation du concept technique, indépendamment d'ODEI

Le concept revendiqué — world model persistant en graphe typé (Neo4j, ontologie à 6 couches : FOUNDATION/VISION/STRATEGY/TACTICS/EXECUTION/TRACK), boucle Observe→Decide→Act→Verify→Evolve, gouvernance par policy engine, exécution auditable — **correspond à un pattern d'architecture déjà largement documenté et déjà implémenté en open source en 2026** : Graphiti, Cognee, KARMA, MAGMA (Multi-Graph Agentic Memory Architecture), EverMemOS, avec des gains mesurés publiés (36-46% sur tâches multi-hop, -40% d'hallucinations vs baseline vectorielle pure selon les benchmarks cités dans la littérature 2026). Autrement dit : **ODEI n'invente pas ce pattern, il l'emballe en produit commercial avec un token attaché.**

**Delta réel vs état de l'art public** : faible. Le concept en lui-même (graph memory + policy + logs) est solide et reconnu comme la direction dominante de l'industrie pour la mémoire d'agents en 2026 — mais ce n'est un "plus" que si on ne l'a pas déjà, pas parce qu'ODEI ou n'importe quel concurrent l'a bien exécuté.

**Delta vs ce qu'ARIA a déjà** (`aria-ops/truth-ledger/`, `aria-ops/memory/`) :
- Le `truth-ledger` d'ARIA fait déjà une partie du travail de "faits canoniques versionnés dans le temps" (chaque entrée a un `id`, `created_at`, `canonical_id`, `supersedes`, `status: verified`) — c'est conceptuellement proche d'un world model typé avec provenance, en beaucoup plus léger (fichiers Markdown horodatés vs graphe Neo4j).
- Ce qu'ARIA n'a probablement pas encore et qui vaudrait la peine d'être regardé : (a) un vrai **moteur de policy/gouvernance d'actions** distinct du simple stockage de faits — c'est-à-dire une couche qui bloque/valide une action *avant* exécution selon des règles explicites, pas seulement un journal après-coup ; (b) une **structure de graphe relationnelle** (liens explicites entre entités/décisions/résultats dans le temps) plutôt qu'une collection de documents plats — utile si le volume de faits canoniques grandit et que les requêtes deviennent multi-hop ("qu'est-ce qui a changé depuis que X a été décidé, et qu'est-ce que ça a affecté ensuite ?").
- Ces deux manques ne nécessitent pas de s'inspirer du code d'ODEI (qui est de toute façon fermé/inaccessible) : ce sont des patterns publics et documentés (Graphiti, Cognee sont open source et directement réutilisables) — donc **le concept vaut la peine d'être exploré via ces implémentations ouvertes**, pas via ODEI.

**Verdict point 5** : **Oui, le concept vaut d'être exploré pour ARIA** — mais en s'inspirant de la littérature/l'écosystème open source déjà mature (Graphiti, Cognee, MAGMA, EverMemOS) plutôt que d'ODEI, qui n'apporte aucune implémentation publique consultable et n'a pas d'avance technique démontrée.

---

## Verdict final

**(a) Légitimité du projet ODEI** : **Ni scam caractérisé, ni projet solide.** Signaux positifs réels : fondateur nommé et enregistré légalement, backend réellement fonctionnel avec données on-chain cohérentes et transparentes (l'API documente même ses propres écarts de données), tokenomie sans allocation équipe déclarée. Signaux d'alerte réels : équipe d'une seule personne non vérifiable au-delà du projet, code produit "cœur" totalement fermé derrière des repos placeholders qui donnent une fausse impression d'ouverture, traction utilisateur quasi nulle (74 comptes), micro-cap illiquide et volatile, utilité du token encore largement conditionnelle au futur ("upon app release"). **Verdict : projet à très haut risque, stade pré-produit malgré un token déjà coté — à traiter comme un pari spéculatif sur l'exécution future d'un solo-founder, pas comme une infrastructure éprouvée.**

**(b) Le concept technique** : **Oui, vaut d'être exploré pour ARIA**, mais en allant chercher les implémentations open source de référence du pattern (Graphiti, Cognee, MAGMA, EverMemOS) plutôt qu'en regardant ODEI, dont le code n'est de toute façon pas consultable. Le gain principal identifié pour ARIA serait une couche de policy engine pré-exécution (pas juste un log a posteriori) et une structuration en graphe des faits du truth-ledger si son volume justifie des requêtes relationnelles multi-hop.

## Ce qui reste non vérifié / à refaire si une décision engageante est prise
- Vérification indépendante on-chain du mint authority et des fonctions du contrat ODAI (l'appel Basescan a échoué faute de clé API dans cet environnement).
- Recherche d'un historique professionnel ou technique antérieur d'Anton Illarionov en dehors d'ODEI.
- Vérification de la répartition réelle des 3 795-6 005 holders (concentration éventuelle sur quelques wallets).
