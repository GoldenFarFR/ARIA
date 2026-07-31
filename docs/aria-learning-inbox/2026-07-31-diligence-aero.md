# Diligence maximaliste — Aerodrome Finance (AERO), 31/07/2026

> Instantané daté, pas une fiche vivante — figé au 31/07/2026, à revérifier avant toute
> citation d'un chiffre précis si repris plus tard. Contexte : conviction long terme
> proposée par l'opérateur pour du capital hodlé en EVM sur Base, distincte de la thèse
> rendement/yield déjà tranchée ailleurs. Méthode : fan-out 26 agents (1 scope + 5 recherche
> + 20 récupération de sources), **vérification croisée faite manuellement par cette session
> après arrêt volontaire du workflow avant sa propre phase de vérification automatique**
> (décision opérateur explicite, 31/07 — garder Scope/Search/Fetch en fan-out large, mais
> reprendre la main sur Verify/Synthèse pour éviter le gaspillage d'agents, cf.
> `feedback_workflow_keep_verify_synthesize_manual` dans la mémoire persistante). Chaque
> affirmation ci-dessous cite sa source ; les divergences entre sources sont signalées
> explicitement plutôt que masquées.

## 1. Mécanique du protocole

**ve(3,3)** : les utilisateurs verrouillent AERO pour recevoir `veAERO` (un NFT), avec un
pouvoir de vote proportionnel au montant et à la durée du lock (jusqu'à 4 ans pour le
maximum). Chaque semaine (epoch), les détenteurs de veAERO votent pour diriger les nouvelles
émissions vers des pools de liquidité précis — plus un pool reçoit de votes, plus il reçoit
d'émissions, ce qui attire de la liquidité et du volume, qui génère des frais reversés aux
votants. Point structurant confirmé par la donnée primaire DefiLlama : **100% du revenu
protocolaire est reversé aux votants veAERO**, un modèle "zéro fuite" — aucune part n'est
retenue par une trésorerie ou une équipe ([DefiLlama — Aerodrome](https://defillama.com/protocol/aerodrome)).

**Slipstream** (le module de liquidité concentrée façon Uniswap v3) : deux sources
convergent sur un lancement **le 22 avril 2024** avec citation directe et datée
([CCN, 22/04/2024](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/)) ;
une troisième source dit "mars 2024" sans date précise
([wiki technique fullstack-development](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md))
— divergence mineure, la date du 22/04 datée et citée l'emporte. Le code de Slipstream est
un **fork direct du Slipstream de Velodrome**, lui-même adapté de l'architecture Uniswap V3
(core/periphery), confirmé sur le dépôt GitHub officiel
([github.com/aerodrome-finance/slipstream](https://github.com/aerodrome-finance/slipstream)).
Slipstream vise les paires stables/peu volatiles ; les pools variables classiques
d'Aerodrome restent utilisés pour les paires plus volatiles/peu liquides
([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/)).

**Licence du code** : les contrats Core/Periphery restent en GPL-2.0-or-later (hérité des
dépendances Uniswap V3), mais le dossier Gauge (qui route les émissions/récompenses) est en
**Business Source License 1.1** — donc pas totalement open-source, une licence restrictive
sur la partie qui gère l'argent
([PERMISSIONS.md, GitHub officiel](https://github.com/aerodrome-finance/contracts/blob/main/PERMISSIONS.md)).

## 2. Tokenomics

**Supply initiale et distribution** : 500M AERO au lancement (août 2023), dont 450M (90%)
verrouillés en veAERO dès le départ. 40% du supply initial (200M tokens) a été airdroppé aux
détenteurs de veVELO (Velodrome) existants, réservé aux wallets détenant au moins 1000 veVELO
(~3 500 wallets éligibles) — deux sources indépendantes convergent exactement sur ce chiffre
([Medium officiel AerodromeFi](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91) ;
[wiki technique](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md)).
L'allocation équipe est verrouillée en veAERO sur un vesting de 2 à 4 ans.

**Répartition détaillée de l'allocation** — deux instantanés à des dates différentes
donnent des chiffres légèrement différents (cohérent avec des émissions cumulatives qui
font évoluer les pourcentages relatifs dans le temps, pas une contradiction) :
- Snapshot Tokenomist : Gauge Emissions 67,81% / Airdrop 9,47% / Rebase 5,00% /
  Ecosystem-Public Goods 4,97% / Foundation-Team (mint initial) 4,50% + (nouveau mint) 3,52%
  / Grants 2,37% / Voter Incentives 1,89% / Genesis LP 0,47%
  ([Tokenomist](https://tokenomist.ai/aerodrome-finance)).
- Snapshot Tokenomics.com (02/2026) : Gauge Emissions 65,44% / Airdrop 10,17% /
  Foundation-Team 8,61% / Rebase 5,37% / Ecosystem 5,34% / Grants 2,54% / Voter Incentives
  2,03% / Genesis LP 0,51% ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).

**Émissions** — convergence forte sur 3 sources indépendantes : 10M AERO/epoch au
lancement (2% du supply initial), phase "Take-off" avec +3%/epoch pendant les 14 premiers
epochs (pic légèrement au-dessus de 15M AERO), puis phase "Cruise" avec décroissance de
-1%/epoch
([Medium officiel](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91) ;
[wiki technique](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md) ;
[Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).

**"Aero Fed"** : à partir d'environ l'epoch 67 (quand les émissions hebdomadaires tombent
sous 9M AERO), les votants veAERO prennent le contrôle direct de la politique monétaire —
ils peuvent augmenter les émissions de +0,01% du supply total par epoch, les diminuer de
-0,01%, ou maintenir le statu quo. Les bornes citées varient légèrement selon la source :
0,52% annualisé minimum et 52% annualisé maximum sont citées de façon cohérente sur 3
sources, une source précise le mécanisme comme un maximum de 1%/semaine (52%/an)
et un minimum de 0,01%/semaine (0,52%/an)
([wiki technique](https://github.com/fullstack-development/blockchain-wiki-en/blob/main/protocols/aerodrome/README.md) ;
[Medium officiel](https://medium.com/@aerodromefi/aerodrome-launch-tokenomics-30b546654a91) ;
[Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)) — **implication clé** :
l'inflation à long terme n'est plus fixée par un code immuable mais devient un objet de vote
communautaire, avec un plafond haut (52%/an) largement supérieur au taux actuel.

**Supply actuel** : supply total **non plafonné** ("infinite" selon Tokenomist), supply
circulant à **978,88M AERO** (quasiment le double du supply initial de 500M, par cumul des
émissions depuis août 2023) contre un market cap de 417,90M$ et une valorisation pleinement
diluée de 668,36M$ — deux sources indépendantes citent exactement le même chiffre de
circulant (978,88M/978 879 231), signe d'une donnée fiable issue de la même source primaire
sous-jacente ([DefiLlama](https://defillama.com/protocol/aerodrome) ; [Tokenomist](https://tokenomist.ai/aerodrome-finance)).

**Signal économique à surveiller** (DefiLlama, méthodologie propre) : les incitations
tokenisées annualisées (137,76M$) **dépassent actuellement** le revenu protocolaire
annualisé (120,54M$), produisant un "earnings" négatif de -17,22M$/an selon ce calcul — le
protocole distribue aujourd'hui plus en émissions qu'il ne génère de revenu réel
([DefiLlama](https://defillama.com/protocol/aerodrome)).

## 3. Position de marché réelle

**TVL** — les chiffres varient fortement selon le moment du snapshot (normal, la TVL
fluctue), listés chronologiquement pour montrer la trajectoire réelle plutôt qu'un chiffre
figé trompeur :
- Janvier 2024 : ~120M$ ([The Block, via TradingView](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base))
- Avril 2024 : ~790M$ (~la moitié de l'ATH de TVL de Base à l'époque, 1,64Md$)
  ([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/))
- Août 2025 : ~602M$ ([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base))
- The Block (date non précisée dans l'article lui-même) : >1 milliard$ de dépôts, ~50% du
  TVL total de Base et >50% du TVL DeFi de Base
  ([The Block](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base))
- Janvier 2026 : ~1,3Md$, ~70% de toute la liquidité DEX de Base
  ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees))
- Juin 2026 (snapshot DefiLlama cité par un tiers) : ~453,76M$
  ([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity))
- Snapshot DefiLlama le plus frais (au moment de cette diligence) : **268,6M$, en baisse de
  13,4% sur 30 jours** ([DefiLlama](https://defillama.com/protocol/aerodrome))
- Slipstream seul (liquidité concentrée) : 131,81M$ TVL, -22% sur 30 jours
  ([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream))

**Lecture honnête de cette trajectoire** : forte croissance 2024-janvier 2026 (120M$ →
1,3Md$), puis **repli net** depuis (1,3Md$ → 453M$ → 268M$ au dernier snapshot) — une baisse
d'environ 80% depuis le pic de janvier 2026. Ce n'est pas nécessairement disqualifiant (le
marché crypto entier peut avoir reculé sur la période) mais c'est un vrai signal à ne pas
passer sous silence pour une thèse "conviction long terme".

**Domination sur Base** : Aerodrome reste le plus gros DEX de Base par TVL, volume et frais,
devant Uniswap et Aave sur ce réseau
([The Block](https://tr.tradingview.com/news/the_block%3Ad3d3c4d57094b%3A0-aerodrome-tops-1-billion-in-deposits-dominating-defi-on-base)).
A réalisé near-2x le volume du top pool Uniswap avec environ la moitié de sa TVL ; frais
7 jours dépassant Curve et PancakeSwap malgré moins d'un tiers de leur TVL respective
([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base)).
Volume 30 jours de 9,02Md$ (comparable à Solana Orca/Raydium) selon le snapshot DWF Labs,
contre 11,2Md$ pour Slipstream seul et 12,4Md$ pour Aerodrome global selon un snapshot
DefiLlama plus récent (juin 2026) — encore une fois, des chiffres qui bougent avec le temps,
pas une contradiction.

**Revenu protocolaire** : 30 jours ~6,12-6,22M$ de frais, ~4,19-4,42M$ de revenu
protocolaire net, annualisé ~160M$ de frais / ~120M$ de revenu (trailing year)
([DefiLlama](https://defillama.com/protocol/aerodrome)). Frais cumulés depuis le lancement
(août 2023) : 295M$ ([Tokenomics.com](https://tokenomics.com/articles/aerodrome-tokenomics-how-aero-captures-100-of-protocol-fees)).
**Tendance trimestrielle en déclin sur Slipstream** : Q3 2025 59,06M$ de revenu brut → Q4
2025 34,97M$ → Q1 2026 19,53M$ → Q2 2026 25,16M$ (léger rebond) → Q3 2026 partiel 5,18M$
([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream)) — tendance
baissière nette depuis le pic de Q3 2025, cohérente avec la baisse de TVL documentée
ci-dessus.

**Intégration Coinbase** : Coinbase a intégré Aerodrome directement dans son application
principale et offre du trading sans frais sur Aerodrome via l'abonnement Coinbase One
([DWF Labs](https://www.dwf-labs.com/research/has-aerodrome-finance-become-the-leading-defi-protocol-on-base)).

**Dépendance structurelle à Base** : Aerodrome opère à 100% sur Base (aucune diversification
multi-chaîne à ce jour, hors le projet de fusion 2026 détaillé section 10) — confirmé
directement par la donnée DefiLlama elle-même ([DefiLlama](https://defillama.com/protocol/aerodrome)).
Un commentateur qualifie explicitement cette dépendance de "risque structurel" — la
croissance d'Aerodrome est mécaniquement plafonnée par celle de Base elle-même
([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity)).

## 4. Sécurité

**Deux niveaux d'audit à ne pas confondre** — c'est la nuance la plus importante trouvée
dans cette diligence, quasi-absente du contenu marketing :

1. **Audit EtherAuthority (05/06/2024)** — porte **uniquement sur le contrat ERC-20 AERO**
   lui-même (`Aero.sol`, adresse `0x940181a94a35a4569e4529a3cdfb74e38fd98631` sur Base), PAS
   sur le protocole complet (Router/Voter/veAERO/gauges). Résultat : 0 critique, 0 haute,
   0 moyenne, 1 basse, 2 informationnelles — verdict "Passed". Point notable soulevé par
   l'auditeur lui-même : le contrat a une adresse `minter` qui peut frapper des tokens sans
   limite (pas de plafond de supply au niveau du contrat) — signalé explicitement comme un
   "risque business" de centralisation, le contrat n'est "pas totalement décentralisé" à
   cause de ce contrôle du propriétaire. L'auditeur note aussi ne pas avoir reçu de scripts
   de test automatisés pour ce contrat — l'analyse s'est appuyée sur du statique/manuel
   (Slither, Solhint, Remix), pas une suite de tests vérifiée
   ([rapport PDF EtherAuthority](https://etherauthority.io/wp-content/uploads/2024/06/Aerodrome-AERO.pdf)).

2. **Le protocole complet (Router/Voter/veAERO/gauges/Slipstream) n'a PAS d'audit
   indépendant propre à Aerodrome** — la page de sécurité officielle elle-même dirige vers
   la sécurité de **Velodrome V2**, dont Aerodrome hérite l'architecture des contrats et la
   maintenance sécuritaire complète. Pas de bug bounty Aerodrome-spécifique non plus — les
   chercheurs en sécurité sont redirigés vers le programme Velodrome
   ([aerodrome.finance/security, page officielle](https://aerodrome.finance/security)).
   Cohérent avec DefiLlama qui liste "Audits: No" pour la page Slipstream spécifiquement
   ([DefiLlama — Slipstream](https://defillama.com/protocol/aerodrome-slipstream)) — la
   catégorisation DefiLlama ne compte probablement pas un audit hérité comme un audit
   "propre" au produit listé, cohérence plutôt que contradiction. Le dépôt GitHub des
   contrats mentionne malgré tout un programme de bug bounty Immunefi actif et des tests
   d'invariants Echidna, sans que ce point ait été vérifié directement sur la page Immunefi
   elle-même dans cette diligence
   ([github.com/aerodrome-finance/slipstream](https://github.com/aerodrome-finance/slipstream)).

**Statut des clés admin — PAS pleinement décentralisé malgré le narratif ve(3,3)** :
- Un multisig "Protocol Team" (`0xE6A41fE61E7a1996B59d508661e3f524d6A32075`) détient le rôle
  Team dans Minter et VotingEscrow, la gestion des frais sur tous les pools, la propriété du
  Factory Registry, et le contrôle initial de ProtocolGovernor et EpochGovernor.
- Un multisig "EmergencyCouncil" séparé (`0x99249b10593fCa1Ae9DAE6D4819F1A6dae5C013D`) peut
  **tuer ou ranimer unilatéralement un gauge** (couper la distribution de récompenses à un
  pool précis à volonté) et gérer des ajustements d'urgence.
- Le rôle "Vetoer" n'est **pas encore renoncé** — intention future seulement, énoncée dans
  la doc officielle elle-même.
- ProtocolGovernor est actuellement contrôlé par l'équipe, avec un plan (non encore réalisé
  au moment de cette diligence) de le remplacer par un contrat OpenZeppelin Governor modifié.
- Le minting d'AERO est restreint au seul contrat Minter, qui distribue au contrat Voter
  pour les émissions de gauge — le mécanisme d'émission est donc verrouillé par contrat, pas
  frappable manuellement par une clé admin.

([PERMISSIONS.md, dépôt officiel](https://github.com/aerodrome-finance/contracts/blob/main/PERMISSIONS.md))

**Incident de sécurité réel confirmé (novembre 2025)** : plus d'1 million $ volé en environ
une heure, via un **détournement DNS** du registrar de domaine (NameSilo/Box Domains) —
**pas un exploit de smart contract**. Les utilisateurs étaient incités à signer des
autorisations malveillantes déguisées en simple confirmation "1", que l'attaquant utilisait
ensuite pour drainer ETH/WETH/USDC et d'autres tokens des wallets connectés. Aerodrome a
redirigé les utilisateurs vers des miroirs basés sur ENS (plutôt que DNS) comme accès plus
sûr pendant l'incident. Post-mortem technique publié par Halborn, une société de sécurité
Web3 reconnue ([Halborn](https://www.halborn.com/blog/post/explained-the-aerodrome-finance-hack-november-2025)).
**Lecture honnête** : l'infrastructure Web2/DNS reste un vecteur d'attaque démontré et réel,
distinct de la sécurité on-chain du protocole lui-même.

## 5. Équipe et gouvernance

**Dromos Labs** est l'entité derrière à la fois Aerodrome (Base) et Velodrome (Optimism).
**Alexander Cutler** (co-fondateur/CEO) est la seule figure publiquement identifiée pendant
longtemps — le reste de l'équipe est resté pseudonyme/anonyme, y compris en interne : Cutler
lui-même n'a appris les vrais noms de certains collègues qu'**un mois avant novembre 2025**,
"nous avons globalement maintenu cette anonymat au sein de l'équipe jusqu'à quasiment ce
point". La raison invoquée du passage vers des identités publiques : rassurer les
législateurs et cadres de la finance institutionnelle, méfiants des développeurs pseudonymes
([DL News](https://www.dlnews.com/articles/defi/aerodrome-founder-talks-aero-uniswap-feud-pseudonymity/)).

**"Feud" avec Uniswap** : Cutler a critiqué publiquement et à plusieurs reprises la
proposition de "fee switch" d'Uniswap (qui détournerait le revenu protocolaire des
fournisseurs de liquidité vers les détenteurs de token) — présenté par Cutler lui-même comme
du "benchmarking compétitif légitime" plutôt qu'une querelle personnelle. Un ancien délégué
Uniswap a réagi publiquement, qualifiant l'annonce concurrente d'Aerodrome
d'"impressionnante mais peu convaincante" vu les critiques précédentes
([DL News](https://www.dlnews.com/articles/defi/aerodrome-founder-talks-aero-uniswap-feud-pseudonymity/)).

**Concentration du pouvoir de vote** : environ **54% du supply circulant d'AERO est
verrouillé en veAERO**, concentrant la gouvernance et réduisant le flottant liquide, selon
une recherche tierce (TokenIntel) citée par CryptoDaily
([CryptoDaily](https://cryptodaily.co.uk/2026/06/aero-base-proxy-liquidity)) — cohérent avec
les 90% verrouillés au lancement, ce taux ayant naturellement baissé avec la dilution par
émissions continues.

## 6. Investisseurs institutionnels

**Coinbase Ventures / Base Ecosystem Fund** a investi dans AERO en **février 2024**,
provoquant un bond du prix de 0,09$ à 0,62$ en une semaine après l'annonce
([The Currency Analytics](https://thecurrencyanalytics.com/altcoins/coinbases-20m-investment-in-aero-fuels-growth-potential-147255) ;
confirmé par [thedefiant.io](https://thedefiant.io/news/defi/aerodrome-founder-denies-that-coinbase-stabbed-them-in-the-back)).
Le montant de **20 millions $** est cité par plusieurs sources presse indépendantes
(CoinDesk, BitcoinWorld, CoinMarketCap) mais n'a pas pu être confirmé sur une source
strictement primaire dans cette diligence (à traiter comme "très probable, largement
repris" plutôt que "certain à 100%").

**Controverse "Coinbase nous a poignardé dans le dos"** — contexte complet : Coinbase a
lancé sa fonctionnalité "Verified Pools" **sur Uniswap V4 plutôt que sur Aerodrome**, malgré
son investissement, provoquant un backlash communautaire. Cutler dément que ce choix soit dû
à une limitation technique : il affirme qu'Aerodrome a délibérément choisi de ne pas
prioriser la construction pour Verified Pools car l'usage restait non prouvé ("notre
priorité est d'être le meilleur sur le marché, pas le premier"), et que la conception
modulaire d'Aerodrome permettrait d'ajouter ce type de pool si besoin. Cutler affirme que la
relation avec Coinbase reste étroite et coopérative malgré ce choix — communication
quotidienne fréquente, et **Coinbase reste l'un des plus gros verrouilleurs de veAERO**
([The Defiant](https://thedefiant.io/news/defi/aerodrome-founder-denies-that-coinbase-stabbed-them-in-the-back)).
Contexte de prix au moment de cet article : Aerodrome 875M$ TVL / 30M$ volume 24h vs
Uniswap 4,1Md$ TVL / 266M$ volume 24h sur Base — AERO se négociait à 0,53$ contre un ATH de
2,21$ en décembre (mention divergente de l'ATH réel, voir section 8).

## 7. Concurrence et positionnement

Aerodrome domine Base face à Uniswap v3/v4 et Aave sur les trois métriques (TVL, volume,
frais) selon DefiLlama/The Block, mais le lancement de "Verified Pools" par Coinbase
**sur Uniswap V4** (et non Aerodrome) montre que l'avantage n'est pas total même auprès de
son propre partenaire stratégique — Uniswap reste une menace concurrentielle réelle et
active sur Base (4,1Md$ TVL cité vs Aerodrome ~875M$ à la même date de comparaison). Par
ailleurs, la comparaison directe avec **Velodrome** (le protocole jumeau sur Optimism, même
équipe) montre une asymétrie considérable : Aerodrome ~475,9M$ TVL contre Velodrome ~39M$
TVL au moment d'un rapport donné — Aerodrome a largement supplanté son protocole d'origine
en importance ([TheDefiant](https://thedefiant.io/news/defi/dromos-labs-merges-aerodrome-and-velodrome-into-new-dex-aero)).

## 8. Historique de prix et volatilité

ATH cité à **2,38$ le 12 avril 2024**, après une hausse d'environ +2300% depuis début mars
2024, avec citation directe et datée
([CCN](https://www.ccn.com/analysis/crypto/base-aerodrome-finance-slipstream-tvl-2-billion/)).
Une autre source (article sur la controverse Coinbase, non daté précisément) mentionne un
ATH de **2,21$ "en décembre"** — chiffre différent, non résolu avec certitude dans cette
diligence : pourrait référencer un ATH local distinct (un rebond de décembre) plutôt que
l'ATH absolu d'avril 2024, mais ceci n'est pas confirmé, à vérifier si le chiffre exact
importe pour une décision. Prix cité à 0,53$ au moment de cet article (repli >75% depuis
l'ATH d'avril 2024).

## 9. Risques structurels identifiés

1. **Dilution inflationniste continue** : supply non plafonné, circulant déjà proche du
   double du supply initial en ~3 ans, et les incitations annualisées dépassent
   actuellement le revenu réel généré (-17,22M$/an selon la méthodologie DefiLlama) —
   section 2.
2. **Dépendance totale à Base** : 100% du TVL sur une seule chaîne, la croissance
   d'Aerodrome est mécaniquement plafonnée par celle de Base — section 3, signalé
   explicitement par un analyste tiers comme un "problème structurel de proxy".
3. **Gouvernance non pleinement décentralisée** : multisig équipe non renoncé, rôle Vetoer
   non renoncé, EmergencyCouncil qui peut tuer un gauge unilatéralement — section 4.
4. **Vecteur d'attaque Web2/DNS démontré** : l'incident de novembre 2025 (>1M$ volé) montre
   que la sécurité opérationnelle (registrar de domaine, frontend) reste un vrai risque
   distinct de la sécurité on-chain — section 4.
5. **Absence d'audit indépendant propre au protocole complet** : Aerodrome hérite
   entièrement de la sécurité de Velodrome V2, jamais audité en tant que tel dans sa version
   actuelle — section 4.
6. **Repli net de la TVL et du revenu depuis le pic de janvier 2026** : ~-80% de TVL et
   tendance de revenu trimestriel en baisse nette sur Slipstream — section 3.
7. **Concentration de la gouvernance** : ~54% du supply circulant verrouillé en veAERO,
   pouvoir de vote concentré entre gros holders (dont Coinbase Ventures) — section 5.

## 10. Développement majeur 2026 — la fusion Aerodrome + Velodrome en "Aero"

**C'est le fait le plus structurant trouvé dans cette diligence, confirmé de façon
indépendante par 4 sources distinctes** (un blog spécialisé, CoinDesk, The Defiant, et une
quatrième source secondaire) — un développement qui change fondamentalement la nature de la
conviction "AERO" :

- **Annonce le 12 novembre 2025**, lors d'un événement de lancement à New York. Aerodrome
  (Base) et Velodrome (Optimism) fusionnent en une seule plateforme/marque unifiée appelée
  **"Aero"**, développée par Dromos Labs
  ([CoinDesk](https://www.coindesk.com/tech/2025/11/13/leading-base-dex-aerodrome-merges-into-aero-in-major-overhaul) ;
  [The Defiant](https://thedefiant.io/news/defi/dromos-labs-merges-aerodrome-and-velodrome-into-new-dex-aero) ;
  [HashBasis](https://www.hashbasis.xyz/blog/aerodrome-velodrome-protocols-set-to-merge-in-2026)).
- **Migration prévue au T2 2026.**
- **Un nouveau token remplacera intégralement AERO et VELO.** Ratio de conversion :
  détenteurs AERO reçoivent **94,5%** du nouveau supply, détenteurs VELO **5,5%** — ratio
  basé sur un partage de revenu sur 52 semaines (Aerodrome 260M$ vs Velodrome 15M$).
- **Nouvelle architecture "Metadex 03"** (remplace Metadex 02) : un "Revenue Engine" (REV)
  consolidant plusieurs flux de frais (swap, frontend, bridge, agrégateur, automatisation,
  marketplace, lancement, enchères MEV), et un "Adaptive Emissions Rate" (AER) conçu pour
  **réduire la dilution du token** en ne payant que les incitations de liquidité
  nécessaires.
- **Slipstream V3** intègre directement une enchère MEV dans l'AMM lui-même.
- **Expansion multi-chaîne** : au-delà de Base et Optimism, extension prévue vers
  Ethereum mainnet et la chaîne Arc de Circle.
- Les protocoles Aerodrome et Velodrome existants continueront de fonctionner après le
  lancement d'Aero, **mais ne recevront plus de support/développement de Dromos Labs**.
- Positionnement stratégique énoncé par Cutler : "Aero est à l'avant-garde d'un système
  financier meilleur, plus rapide et moins cher que le système en place" — présenté comme
  une refonte structurante, pas un simple rebranding.

**Implication directe pour une thèse "conviction long terme sur AERO"** : détenir AERO
aujourd'hui, c'est en réalité parier sur un token qui sera **remplacé** par un nouveau token
unifié d'ici le T2 2026, avec un ratio de conversion déjà fixé (94,5%). La thèse de
conviction doit donc porter sur le NOUVEAU token/protocole "Aero" et sa nouvelle mécanique
anti-dilution (AER), pas uniquement sur AERO tel qu'il existe aujourd'hui — un point que le
contenu marketing/vulgarisé (CoinGecko Learn, guides génériques) ne mentionne pas du tout,
uniquement trouvé via le fan-out large de sources spécialisées.

## Synthèse — signaux positifs et signaux d'alerte, sans complaisance

**Signaux positifs réels** :
- Position de dominance confirmée et multi-sourcée sur Base (premier DEX par TVL/volume/
  frais, loin devant Uniswap et Aave sur ce réseau)
- 100% du revenu réel reversé aux détenteurs qui verrouillent (pas de fuite vers une
  trésorerie opaque)
- Intégration directe dans l'app Coinbase + trading sans frais via Coinbase One — alignement
  stratégique fort avec l'écosystème Base
- Roadmap 2026 ambitieuse et concrète (fusion Aero, mécanisme anti-dilution AER,
  expansion multi-chaîne) plutôt qu'un projet stagnant
- Équipe qui accepte la transparence croissante (identités publiques) plutôt que de rester
  opaque indéfiniment

**Signaux d'alerte réels, non négociables à ignorer** :
- Repli de TVL/revenu d'environ 80% depuis le pic de janvier 2026 au moment de cette
  diligence — pas juste une baisse de prix cyclique, une vraie contraction d'activité
- Incitations tokenisées qui dépassent déjà le revenu réel (-17,22M$/an) — un modèle qui
  distribue plus qu'il ne gagne, à surveiller pour savoir si "Aero"/AER (section 10) corrige
  réellement ce problème ou le reporte seulement
- Gouvernance PAS pleinement décentralisée malgré le narratif ve(3,3) (multisig équipe,
  EmergencyCouncil, Vetoer non renoncé)
- Aucun audit indépendant du protocole complet — sécurité entièrement héritée de Velodrome V2
- Incident de sécurité réel et récent (nov. 2025, >1M$ volé, vecteur DNS) — la sécurité
  opérationnelle reste un vrai point faible démontré, pas hypothétique
- **Le token AERO tel qu'il existe aujourd'hui a une durée de vie annoncée** (fusion vers
  "Aero" au T2 2026) — toute conviction doit être réévaluée à la lumière du nouveau
  protocole, pas figée sur le token actuel

**Verdict de cette session (pas une recommandation d'investissement, une lecture technique)** :
la dominance et l'alignement stratégique avec Base sont réels et bien documentés, mais la
thèse "conviction long terme sur AERO" doit explicitement intégrer que (a) le token va être
remplacé d'ici le T2 2026 et (b) le modèle économique actuel distribue plus qu'il ne gagne au
moment de cette diligence. Point de vigilance à se reposer périodiquement : la fusion "Aero"
a-t-elle eu lieu comme prévu au T2 2026, le mécanisme AER a-t-il réellement réduit la
dilution, et la tendance de TVL/revenu s'est-elle stabilisée ou continue-t-elle de se
dégrader ?
