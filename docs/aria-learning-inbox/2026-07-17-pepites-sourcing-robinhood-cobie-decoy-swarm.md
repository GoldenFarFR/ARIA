[VPS Research]

# Sourcing narratif de pépites (17/07) — Robinhood Chain confirmée, essaim de décoys BRIAN/COBIE/EMILIE démasqué

## Contexte

Dispatch opérateur explicite du 17/07 : sortir du radar mécanique habituel
(DexScreener trending déjà consulté par le pipeline `momentum_entry.py`) et
chercher des pépites via narratifs émergents (X/Twitter, presse crypto,
annonces), avec vérification de légitimité au même niveau d'exigence que
`safety_screen`/`momentum_entry` — honeypot GoPlus comme seul garde-fou dur,
mais **aucune donnée de sécurité vérifiable = piste écartée**, y compris sur
une chaîne récente comme Robinhood (même doctrine que Solana, gravée 17/07
dans CLAUDE.md).

**Cette session n'a pas d'accès direct à X/Twitter** (pas de connecteur
navigateur actif) — la recherche narrative est passée par la presse crypto
(WebSearch), qui reprend et sourcé les mêmes annonces/tweets. Chaque piste
retenue a ensuite été vérifiée par un **vrai appel `curl` en direct** contre
GoPlus (chain_id explicite) et DexScreener (jamais une lecture d'article
seule) — même discipline que les notes précédentes de ce dossier.

---

## 1. Le narratif qui a lancé la recherche : transition de leadership Base app (Pollak → Cobie)

Confirmé par 3+ sources indépendantes (Coindesk, The Block, Decrypt,
Cointribune, cryptobriefing) : le 15/07/2026, Jesse Pollak a publiquement
admis l'échec du pari social de l'app Base ("definitively wrong"), cédé la
direction de l'app grand public à Jordan "Cobie" Fish (ex-fondateur du
launchpad ICO Echo, racheté ~375M$ par Coinbase), et recentré Base sur
trading/paiements/agents IA. **Déjà gravé dans CLAUDE.md le 16/07** — rien
de neuf sur le fond, cette note ne fait qu'ancrer ce narratif dans le temps
avant de traiter ce qu'il a directement engendré on-chain.

## 2. VERDICT NÉGATIF — la vague BRIAN/COBIE/EMILIE est un essaim de décoys, pas une pépite

Repérée par l'opérateur hier soir sur DexScreener. Vérifiée en direct,
**verdict : à éviter, pas juste "à surveiller avec prudence"**.

**Preuve 1 — noms de contrat génériques masqués derrière des tickers
narratifs** (`curl api.gopluslabs.io/api/v1/token_security/8453`) :
- `0xb200000000000000000000FdE12D4C5b4d14E901` — `token_symbol: "COBIE"`
  mais `token_name: "Base Man"` (nom de contrat générique, pas "Cobie").
  **`holder_count: "2"`** — deux adresses seulement, dont une contient 33,8%
  de l'offre. Aucune donnée honeypot/mintable/owner exploitable (champs
  vides) — GoPlus n'a manifestement pas pu analyser ce contrat en
  profondeur (pool Uniswap V4 atypique via un `pool_manager` partagé).
- `0xB200000000000000000000Ab5A2f0563Fc131d8c` — `token_symbol: "EMILIE"`,
  `token_name: "Coinbase Woman"`. **`holder_count: "0"`.**
- Même schéma sur la variante `Emilie Choi` / `0x91e2ce85...` VLAD (voir
  point 3) : nom de contrat sous-jacent générique ("Robinhood Man") avec un
  ticker narratif collé dessus.

**Preuve 2 — grinding d'adresse en série (fabrication automatisée)** : au
moins 6 contrats COBIE et 5 contrats EMILIE distincts trouvés sur Base,
presque tous préfixés `0xB200000000000000000000...` (recherche DexScreener
`q=cobie`/`q=emilie`, 30 paires retournées pour chacun) — un préfixe de
vanity address partagé par des dizaines de tickers différents n'arrive pas
par hasard, c'est un déploiement automatisé/factory qui exploite le
narratif du moment en masse, pas une communauté organique qui se coordonne
sur UN contrat.

**Preuve 3 — auto-promotion "community takeover" + volumes disproportionnés
par rapport à la liquidité réelle** (`token-boosts/latest/v1`, DexScreener) :
le boost payant de `0x02C4347ECE55Fe108c9A29e96221615f13070791` s'auto-décrit
littéralement `"COBIE is the community takeover honoring..."` — un vrai
projet ne se présente pas comme un "takeover" du ticker d'un autre. Sur les
snapshots DexScreener successifs, plusieurs contrats COBIE/EMILIE affichent
un volume 24h **20 à 27x supérieur à leur liquidité** (ex. EMILIE
`0xB200...Ab5A2f0563Fc131d8c` : liq $79k pour vol24h $2,15M) — signature
classique de wash-trading pour gonfler artificiellement le classement
trending, pas une adoption réelle.

**Recommandation pour le pipeline** : ne traiter aucun contrat de cette
vague comme candidat, quel que soit son classement DexScreener trending
tant qu'il n'existe pas UN contrat officiellement rattaché à Cobie/Emilie
Choi elle-même (aucune preuve de ce type trouvée). Le narratif "Base app
change de leadership" est réel ; les tokens qui en surfent la vague sont,
sur l'échantillon vérifié, un piège à visibilité plutôt qu'un signal.

## 3. Robinhood Chain — chaîne confirmée VIABLE pour le pipeline, deux candidats vérifiés

Robinhood Chain a lancé son mainnet public le ~01/07/2026. Volume DEX
cumulé revendiqué >5,5Md$ depuis le lancement, 10 des 12 tokens trending
DexScreener (mi-juillet) sont sur cette chaîne — confirme que c'est un vrai
pôle d'activité neuf, pas un effet d'annonce. **Rappel** : `robinhood` est
déjà dans `DEFAULT_CHAINS` de `momentum_entry.py` (`chain_id 4663` déjà
mappé côté GoPlus) mais n'avait, à ma connaissance des notes existantes,
jamais été vérifié de bout en bout avec de vrais candidats — cette note
comble ce trou.

**CASHCAT** — `0x020bfC650A365f8BB26819deAAbF3E21291018b4` (Robinhood
chain). GoPlus (vérifié en direct) : `is_honeypot: 0`, `is_mintable: 0`,
`is_open_source: 1`, `hidden_owner: 0`, `is_blacklisted: 0`,
`selfdestruct: 0`, buy/sell/transfer tax tous à `0`. **`holder_count:
31 557`** (vraie distribution, pas un chiffre gonflé) ; le créateur ne
détient que `creator_percent: 0.000001` de l'offre. Liquidité $1,5-2,7M,
volume 24h $16-35M, FDV ~60,8M$. C'est le leader confirmé de la chaîne
(déjà cité par la presse comme tel) — plus un "leader établi safe" qu'une
pépite précoce à ce stade, mais la première preuve concrète que le garde-fou
honeypot fonctionne réellement sur Robinhood chain en pratique.

**VLAD ("Robinhood Man")** — `0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b`
(tribut à Vlad Tenev, CEO Robinhood). GoPlus : mêmes vérifications propres
(`is_honeypot: 0`, `is_mintable: 0`, `is_open_source: 1`, `hidden_owner: 0`,
`is_blacklisted: 0`, pas de taxe), `holder_count: 900` (réel, sans commune
mesure avec les 2 holders de COBIE), `owner_percent: 0`,
`creator_percent: 0,796%` (faible). **Réserve honnête** : volume 24h
observé jusqu'à $1,49M pour une liquidité de $29k (~51x) sur un snapshot —
signal de momentum extrême/pump, pas un problème de sécurité contractuelle
mais un vrai risque de timing d'entrée. Contrat propre, mais candidat à
faire passer par le filtre TA/R-R de `momentum_entry.py`, pas à traiter
comme un signal d'entrée en lui-même.

**Écartés faute de couverture GoPlus (doctrine appliquée, pas une
supposition)** — `token_security/4663` n'a renvoyé aucune donnée pour :
SPACEHOOD (`0xFe7E19CbCe2f896C6C528BC355bAF5a768291E18`), BRODIE
(`0x45F82AC5d507e988f7406935da8eEfe495a360e0`), les deux variantes
ROBINHOOD, ainsi que NUVOLETTA et CASHDOG repérés sur les boosts
DexScreener (`$CASHDOG... follow in the footsteps of $CASHCAT` —
explicitement un clone du token ci-dessus). Même chaîne, mêmes outils,
résultat différent de VLAD/CASHCAT : ces contrats sont probablement trop
récents/trop petits pour être indexés par GoPlus. Conforme à la règle
opérateur — pas de donnée de sécurité vérifiable, donc pas une piste, quel
que soit le narratif ou le volume affiché.

## 4. Solana — rien de neuf retenu, un exemple de discipline utile

Narratif "AI cults/synthetic cultures" (GOATSEUS/GOAT) : le token GOAT
réel est **ancien** (octobre 2024, déjà >13M-163M$ FDV selon la paire),
pas une pépite fraîche — les articles "trending cette semaine" semblent
recycler un narratif daté, écarté.

**$ANSEM ("Black Bull")** — `9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump`
(Solana), FDV ~200M$, liquidité $2M+, largement couvert par la presse
(Ansem a lui-même airdropé ~7M$ de tokens à 700+ wallets fin juin). **Vérifié
en direct avant tout jugement** : `token_security/solana` sur ce contrat
renvoie `code: 1` ("OK") mais **`result: null`** — aucune donnée GoPlus
disponible, même schéma que les tokens pump.fun frais déjà documentés le
17/07 dans CLAUDE.md (`code:1, result vide`). **Écarté**, malgré les
200M$ de FDV et la couverture presse abondante — preuve concrète que "gros
market cap + narratif viral" n'est pas un substitut à une donnée de
sécurité vérifiable. Aucun autre candidat Solana solide trouvé ce passage
(la plupart des tokens boostés du moment sont du bruit de memecoin
générique — chats, grenouilles, raccoons — sans narratif différenciant
vérifiable).

## Résumé actionnable pour le pipeline / l'opérateur

| Candidat | Chaîne | Contrat | GoPlus | Verdict |
|---|---|---|---|---|
| CASHCAT | robinhood | `0x020bfC650A365f8BB26819deAAbF3E21291018b4` | Propre, 31 557 holders | Leader établi, sûr, plus "momentum confirmé" que pépite précoce |
| VLAD ("Robinhood Man") | robinhood | `0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b` | Propre, 900 holders | Sûr techniquement, risque de timing (vol/liq ~51x) — passer par le filtre TA/R-R |
| COBIE ("Base Man") | base | `0xb200000000000000000000FdE12D4C5b4d14E901` | 2 holders, décoy | **À éviter** |
| EMILIE ("Coinbase Woman") | base | `0xB200000000000000000000Ab5A2f0563Fc131d8c` | 0 holder, décoy | **À éviter** |
| ANSEM (Black Bull) | solana | `9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump` | Aucune couverture GoPlus | Écarté (doctrine fail-closed) malgré 200M$ FDV |

## Branches ouvertes (banquées, pas creusées)

- **Robinhood Chain mérite un vrai cycle de scan actif dans le pipeline
  momentum** (pas seulement listé dans `DEFAULT_CHAINS`) — c'est la chaîne
  la plus active en volume neuf de la semaine et le garde-fou honeypot y
  fonctionne, vérifié ce soir pour la première fois avec de vrais
  candidats.
- **Le pattern "essaim de décoys avec vanity-prefix + nom de contrat
  générique"** repéré sur BRIAN/COBIE/EMILIE (et son écho "Robinhood Man"
  côté VLAD) pourrait être généralisable comme heuristique de détection
  automatique future : un contrat dont `token_name` ne correspond pas au
  narratif affiché par le `token_symbol`/les métadonnées de marché est un
  signal d'alerte à bas coût, jamais exploité aujourd'hui dans
  `safety_screen.py`/`momentum_entry.py`. Non implémenté ce soir (recherche
  seulement), à évaluer si le pattern se reproduit sur le prochain
  narratif chaud.
- **LootPad** (`"the launchpad for Robinhood Chain that actually p[ays]..."`,
  vu dans les boosts) — launchpad natif Robinhood Chain, pas diligencé,
  piste à creuser si la chaîne se confirme comme un pôle d'activité
  durable.
- **Aucun accès X/Twitter direct dans cette session** — toute la recherche
  narrative est passée par la presse crypto qui reprend X. Un futur accès
  navigateur (`claude-in-chrome`) permettrait de voir les narratifs se
  former en direct, avant que la presse ne les reprenne (mentionné comme
  limite explicite le 16/07 dans CLAUDE.md, toujours vraie).
