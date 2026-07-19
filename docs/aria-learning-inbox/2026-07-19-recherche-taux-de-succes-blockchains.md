# Taux de succès réel (Web2-crossover) par blockchain — 19/07

Recherche demandée par l'opérateur (comparaison des ~20 meilleures blockchains sur
leur taux de projets qui deviennent de vrais leaders avec un impact Web2 réel — pas
juste un volume de lancements). Contexte de récupération : agent lancé plus tôt dans
la session, resté "en cours" 38 minutes sans jamais revenir (orphelin côté
orchestration — confirmé introuvable via `TaskStop` alors qu'il avait pourtant fini
d'écrire sa réponse complète, `stop_reason: end_turn`, dans son transcript). Contenu
récupéré directement depuis le transcript, jamais régénéré.

## Constat de méthode

Aucun jeu de données ne mesure directement un "taux de succès Web2 réel" par chaîne —
la barre (produit + usage réel + équipe crédible au-delà du cercle crypto) est trop
qualitative pour un tracker automatisé. Triangulation faite à partir de 3 mesures
adjacentes (survie brute, survie des projets soutenus par des VC, taux de diplomation
des usines à tokens) + preuves qualitatives (exemples nommés, concentration d'arnaques
par chaîne, filtres institutionnels).

## Statistiques de base

- **~20,2M tokens lancés (mi-2021 à fin 2025, GeckoTerminal)** : 53,2% morts (plus
  tradés), 86,3% de ces morts en 2025 seule (usines à tokens type pump.fun + crash de
  liquidations oct-2025). [CoinGecko](https://www.coingecko.com/research/publications/how-many-cryptocurrencies-failed)
- **Cohorte déjà filtrée par des VC (1181 projets, 2023-2024)** : 45,3% morts, 77%
  n'atteignent jamais 1000$/mois de revenu. Échec au-dessus de 33% sous 5M$ levés,
  chute fort au-dessus de 50M$. [ChainPlay](https://chainplay.gg/blog/study-half-vc-backed-projects-dead/)
- **Taux de diplomation pump.fun (Solana)** : 0,2%-1,4% selon la période — et
  "diplômé" veut juste dire franchir un seuil de market cap (69k$), pas un succès.
- **Étude pré-usines-à-tokens (2016-2021)** : seulement 18% définitivement morts —
  preuve que les usines automatiques ont cassé les statistiques récentes.
- Aucune source fiable trouvée pour les chiffres "taux de rug pull 35-50%" qui
  circulent sur les blogs SEO — à ignorer.
- **Estimation perso, pas mesurée nulle part** : si même les projets triés par des VC
  échouent à 45-57%, le vrai taux de succès Web2 (type Circle/Polymarket) sur TOUS les
  tokens jamais lancés est très probablement bien sous 1%.

## Volume ≠ taux

- Haut volume, bas taux : Solana via pump.fun, BSC (12% des tokens flagués arnaque,
  73-76% des rug pulls documentés concentrés là).
- Bas volume, taux apparent plus haut : Avalanche (subnets payants, filtre naturel),
  Base (fonds Coinbase sélectifs), Cardano/Polkadot (barrière technique haute — pas
  forcément de meilleurs projets pour autant).

## Classement par chaîne (synthèse triangulée, pas un classement publié)

**Tier A — taux apparent le plus fort, vrais crossovers Web2** : Ethereum (Circle/USDC
IPO NYSE +168%, Uniswap, Chainlink) · Avalanche (2Mds$+ d'actifs tokenisés Japon,
FIFA Coupe du monde 2026) · Base (distribution Coinbase 34,5M MAU, fonds sélectifs) ·
Polygon (le plus de partenariats Fortune-500 nommés, mais plusieurs pilotes ponctuels
arrêtés — ex. Starbucks Odyssey — et activité -28% mi-2024).

**Tier B — vraie infra, résultat mitigé/pas prouvé à l'échelle Web2** : Solana (élite
Fondation/VC solide — Jupiter, Helium, Pyth — mais plombée par la pire usine à tokens
du classement) · Cosmos (adoption technique réelle, capture économique faible) ·
Arbitrum/Optimism (forte DeFi, aucun crossover Web2 marquant trouvé) · Sui/Aptos
(bien financés, trop jeunes pour juger).

**Tier C — taux plus faible, concentration d'arnaques ou déclin documenté** : BNB
Chain (plus haute concentration d'arnaques documentée) · TON (portée Web2 énorme au
départ via Telegram — Notcoin 40M en 6 mois, Hamster Kombat 200M+ inscrits — mais
Hamster Kombat a perdu 96% de ses utilisateurs et >95% de la valeur de son token en
un an : meilleur exemple que "beaucoup de monde au départ" ≠ "durable") · Tron
(utilité réelle mais étroite, signaux de concentration jeux d'argent/illicite) ·
Cardano ("chaîne fantôme", peu d'activité malgré le code produit) · Polkadot (déclin
le plus net : contributeurs divisés par 2 en 2 ans, <5000 utilisateurs actifs/jour
en 2025).

## Ce qui ressort

Les chemins de lancement qui coûtent cher ou qui filtrent (subnets, fonds sélectifs,
subventions liées à du travail déjà livré) donnent moins de projets mais plus
crédibles. Les lancements permissionless instantanés donnent énormément de volume
mais le pire taux de qualité. TON montre un 3e cas : une distribution Web2 sans
friction peut créer une portée énorme vite, sans jamais créer de durabilité.

## Branches ouvertes

- **Pertinence directe pour le sourcing multi-chaînes du test 1M$ (#194)** : le
  pipeline momentum couvre déjà Base/Solana/Robinhood — cette recherche confirme que
  Solana mérite bien la doctrine "aussi stricte que sur Base" déjà actée (14/07/17/07),
  vu la concentration d'arnaques mesurée. Aucune action requise, juste une
  confirmation externe indépendante d'une décision déjà prise.
- **TON et le piège "reach ≠ durabilité"** : si un jour ARIA évalue un token distribué
  via Telegram/TON avec un buzz initial énorme, le cas Hamster Kombat (-96% en un an)
  est un exemple concret à citer dans une thèse pour ne pas confondre traction
  initiale et signal de qualité — pertinent pour `conviction_research.py` si jamais
  TON entre dans le périmètre multi-chaînes.
- **Filtres de lancement coûteux comme signal indirect de qualité** : Avalanche
  (subnets payants) et les fonds Base sélectifs corrèlent avec des projets plus
  crédibles — piste non creusée : est-ce qu'un jour `launchpad_discovery.py` pourrait
  pondérer un candidat selon le coût/la sélectivité de son launchpad d'origine, pas
  seulement bonding/direct ? Pas construit, juste noté.

## Ce qui est solide vs. estimé

**Bien sourcé/vérifiable** : 53,2%/20,2M tokens morts (CoinGecko) · 45%/1181 projets
VC (Chainplay) · taux de diplomation pump.fun · concentration d'arnaques BSC ·
effondrement Hamster Kombat (TON) · déclin des contributeurs Polkadot · partenariats
nommés Polygon/Avalanche.

**Synthèse/estimation, pas mesurée telle quelle** : le classement par tiers A/B/C
lui-même, l'estimation "bien sous 1%" de succès global, toute affirmation qu'une
chaîne précise a un TAUX (pas un volume) objectivement plus haut qu'une autre.
