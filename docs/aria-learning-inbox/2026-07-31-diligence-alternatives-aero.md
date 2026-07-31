# Diligence comparative — alternatives à AERO comme conviction long terme, 31/07/2026

> Instantané daté, pas une fiche vivante. Suite directe de
> `2026-07-31-diligence-aero.md` : après avoir écarté AERO comme token principal de
> détention (fusion de token déjà prévue, TVL en repli net, dilution nette négative), cette
> fiche compare 6 blue-chips DeFi/infra établis en EVM comme remplaçants candidats. Méthode :
> workflow custom (Scope+Search+Fetch, 37 agents — 1 scope + 6 search + 30 fetch), **arrêt
> volontaire avant toute phase Verify/Synthesize automatique** (doctrine actée le même jour,
> cf. `feedback_workflow_keep_verify_synthesize_manual`) — vérification croisée faite
> manuellement ci-dessous. Critères de sélection (les mêmes qui ont fait rejeter AERO) :
> (1) pas de fusion/remplacement de token déjà annoncé ou vécu, (2) pas de dépendance
> quasi-totale à un seul écosystème, (3) gouvernance réellement décentralisée, (4) modèle
> économique qui ne distribue pas structurellement plus qu'il ne gagne, (5) longue résistance
> multi-cycles, (6) achetable en EVM.

## UNI (Uniswap)

**Tokenomics — signal très positif** : la proposition de gouvernance "UNIfication" (adoptée
fin décembre 2025, 125,34M votes pour contre 742 contre) a brûlé 100M UNI du trésor **et**
activé un fee switch permanent qui redirige 20-25% des frais de trading vers un mécanisme de
burn continu — un modèle **déflationniste financé par le revenu réel**, l'exact inverse du
défaut identifié chez AERO (qui distribue plus qu'il ne gagne). Après mise en œuvre : volume
+52% (297M$), prix +5,2% ([ainvest.com](https://www.ainvest.com/news/uniswap-unification-deflationary-catalyst-uni-defi-2-0-2512/) ;
confirmé indépendamment par [CryptoRank](https://cryptorank.io/news/feed/0554c-uniswap-governance-proposal-burns-uni)).

**Gouvernance** : le contrat Timelock impose un délai obligatoire de 2 à 30 jours entre le
vote et l'exécution, avec fenêtre d'exécution de 14 jours — la configuration du Timelock
lui-même ne peut être changée que par ce même mécanisme (pas d'appel admin instantané)
([GitHub officiel](https://github.com/Uniswap/governance/blob/master/contracts/Timelock.sol)).
En août 2025, la gouvernance a adopté une structure légale DUNA (association à but non
lucratif décentralisée) qui préserve la décentralisation tout en permettant de signer des
contrats réglementairement ([Uniswap Developers](https://developers.uniswap.org/docs/ecosystem/governance/overview)).
**Zone d'ombre** : le rôle opérationnel exact de la Uniswap Foundation et le contrôle réel
des clés admin ne sont pas documentés sur cette page officielle elle-même.

**Sécurité** : 9 audits indépendants (OpenZeppelin, Certora, Trail of Bits), bug bounty
jusqu'à 15,5M$ (l'un des plus gros de tout le secteur DeFi). Une vulnérabilité de
réentrance sur le Universal Router déjà corrigée ; une faille wallet-niveau signalée mais
non confirmée par Uniswap au moment de la source ([Messari](https://messari.io/project/uniswap)).

**Track record** : lancé en novembre 2018 (V1) sur un grant de 100 000$ de l'Ethereum
Foundation, financement mené par Paradigm 6 mois après — l'un des plus anciens DEX encore
actifs. Multi-chaîne (pas mono-écosystème). **Aucune fusion de token prévue.**

## LINK (Chainlink)

**Position de marché** : ~70% de part de marché sur l'infrastructure oracle, ~75-100Md$ de
valeur totale sécurisée (TVS), CCIP actif sur 60+ chaînes sécurisant 33,6Md$ de valeur
cross-chain ([ainvest.com](https://www.ainvest.com/news/chainlink-executes-165m-quarterly-token-unlock-expands-oracle-network-integrations-2604/) ;
[SpotedCrypto](https://www.spotedcrypto.com/chainlink-link-2026-network-adoption-investment-thesis/)).

**⚠️ Défaut économique structurellement identique à celui d'AERO** : la documentation
officielle de Chainlink elle-même reconnaît que les récompenses de staking sont
**actuellement financées par des émissions de tokens, pas par le revenu réel du protocole**
— le passage vers un financement par les frais/revenus n'est qu'"attendu" à terme, pas
encore réalisé ([chain.link/economics/staking](https://chain.link/economics/staking) ;
[Chainlink Blog](https://blog.chain.link/chainlink-staking-v0-2-overview/)). C'est très
exactement le défaut n°4 identifié chez AERO.

**Dilution** : Chainlink Labs (l'entité fondatrice) détient environ 300M LINK (~30% du
supply genesis) **sans calendrier de vesting rendu public** — risque de dilution/pression
vendeuse non prévisible, à surveiller par tracking on-chain plutôt que par un calendrier
connu ([SpotedCrypto](https://www.spotedcrypto.com/chainlink-link-2026-network-adoption-investment-thesis/)).
Supply plafonné à 1 milliard mais circulant (~657M) bien en dessous du cap — écart FDV/market
cap important.

**Gouvernance — trou d'information notable** : aucune des sources fetchées ne documente de
structure DAO ni ne confirme si les clés admin sont renoncées ou tenues par une entité
précise — un vrai manque de transparence sur ce critère précis, à approfondir si ce candidat
est retenu.

**Sécurité** : aucun incident de sécurité documenté sur les oracles Chainlink eux-mêmes (les
cas cités — bZx, Synthetix — sont des exploits PRÉ-Chainlink sur d'autres protocoles, cités
comme argument commercial). Certification ISO 27001 / SOC 2 Type 1. Administration des feeds
toujours multisig ([7BlockLabs](https://www.7blocklabs.com/blog/chainlink-oracle-security-best-practices-design-documentation-and-real-world-incidents)).
Concurrence réelle de Pyth/RedStone sur les chaînes sensibles au coût.

## AAVE

**Le candidat le plus solide sur l'ensemble des critères.**

**Tokenomics — signal le plus positif du lot** : programme de rachat de 50M$/an
**intégralement financé par le revenu réel du protocole** (pas des émissions), inscrit dans
la gouvernance fin 2025, précédé d'un pilote ayant déjà racheté 94 000 AAVE pour 22M$ —
un historique réel, pas juste une promesse ([ainvest.com](https://www.ainvest.com/news/aave-governance-realignment-revenue-sharing-model-assessing-long-term-implications-token-holders-2601/)).
Supply quasi entièrement circulant (15,42M sur 16M max) — dilution future minime.

**Gouvernance — décentralisée précocement** : les clés admin des contrats
LendingPoolAddressProvider et TokenDistributor ont été transférées à la gouvernance dès
**octobre 2020** ([CryptoAdventure](https://cryptoadventure.com/aave-protocol-handovers-admin-keys-to-governance/))
— l'un des jalons de décentralisation les plus précoces de tout le secteur. La doc officielle
actuelle confirme : chaque changement passe par un vote on-chain (AIP), **"aucune entité, pas
même Aave Labs, ne peut agir seule"**, avec un timelock (1 jour standard, 7 jours pour les
changements de gouvernance) et un multisig-guardian communautaire 5-of-9 avec pouvoir de veto
([docs.aave.com](https://docs.aave.com/developers/deployed-contracts/security-and-audits)).
**Divergence à signaler** : un rapport tiers plus ancien (defiwatch) décrit un DAO Aragon
3-of-5 SANS timelock — probablement une description d'un état antérieur, la doc officielle
2026 (plus récente et plus détaillée) l'emporte, mais le point mérite d'être revérifié si ce
candidat est retenu pour de vrai ([defiwatch sur GitHub](https://github.com/chrisblec/defiwatch/blob/master/admin-key-config-and-opsec/project-reviews/aave.md)).

**Sécurité** : 65 audits/revues au total (Certora, Trail of Bits, Sherlock, ChainSecurity,
PeckShield, CertiK, Consensys Diligence), 6+ ans d'opération sans interruption, 4,4Md$ de
liquidations traitées sans aucune mauvaise dette. **Un seul incident réel recensé** :
12 mars 2026, exploit "CAPO Oracle Misconfiguration" sur Aave V3, perte de 862 000$
**intégralement restituée** ([DefiLlama](https://defillama.com/protocol/aave)).

**Position de marché** : 14,45Md$ TVL (23 chaînes, dont Base directement — première à
atteindre 1Md$ TVL sur 6 réseaux distincts incluant Base), part de marché du prêt DeFi
passée de 17% à 29% du TVL total en 2025, 61,5% de part de marché sur le prêt actif
([aave.com/blog](https://aave.com/blog/aave-2025-recap)). **Aucune fusion de token prévue.**

## MKR/SKY (MakerDAO / Sky)

**⚠️ Disqualifié — a DÉJÀ vécu exactement le défaut n°1 identifié chez AERO.** Le rebranding
MKR→SKY (plan "Endgame") a provoqué un rejet communautaire suffisant pour que le
co-fondateur Rune Christensen propose lui-même de **revenir en arrière** vers la marque
Maker/MKR seule — un vote formel a été programmé début novembre 2024 pour trancher entre 3
options (garder Sky, revenir à Maker/MKR, ou un hybride), signe d'une identité de token
encore activement instable au moment de cette diligence
([CryptoSlate](https://cryptoslate.com/sky-considers-reverting-to-makerdao-after-community-pushback/)).
Ce n'est pas un risque théorique comme pour AERO (fusion annoncée mais pas encore vécue) —
c'est un précédent déjà réalisé.

**Point de sécurité opérationnelle notable** : ~756M$ de réserves USDC gérées via une simple
adresse externe (EOA), pas un multisig ni un contrat intelligent — absence de
multi-signature ou de délai verrouillé, signalé publiquement (source anonyme, pas un
exploit réel constaté) ([TradingView/Cointelegraph](https://www.tradingview.com/news/cointelegraph:3879ef385094b:0-sky-faces-scrutiny-over-potential-756m-exploit-flaw/)).

**Signal positif malgré tout** : réduction des émissions de staking (-161,82M tokens sur 180
jours, vote du 27/02/2026) + 114,5M$ dépensés en rachat de ~1,83Md SKY, ~67% du supply
circulant actuellement staké
([CoinDesk](https://www.coindesk.com/markets/2026/03/05/sky-jumps-nearly-10-after-governance-vote-cuts-emissions-while-buybacks-tighten-supply)) —
la discipline financière s'améliore, mais ne compense pas le défaut n°1 déjà réalisé.

## CRV (Curve Finance)

A subi un vrai hack en juillet 2023 (~50-70M$ drainés via un bug du compilateur Vyper, pas un
défaut de conception du protocole lui-même) — DAO a voté un remboursement (71,77M CRV du
fonds communautaire, vesting 1 an). Score de sécurité tiers post-hack : **6,5/10**,
explicitement qualifié de "pas un outil d'épargne passive"
([Coin Bureau](https://coinbureau.com/review/curve-finance-crv)).

**Gouvernance — point de centralisation réel** : le contrat "Address Provider" (pièce
d'infrastructure admin clé) est contrôlé par **un individu, pas la DAO** — un vrai point de
transparence signalé par la source elle-même. Le mécanisme veCRV est en outre sujet à des
"guerres de bribes" et à une concentration de la gouvernance via Convex (méta-gouvernance)
([Coin Bureau](https://coinbureau.com/review/curve-finance-crv)). Supply plafonné à 3,03
milliards, distribué sur plusieurs années ([SwitcHere](https://switchere.com/guides/crv-transaction)).

## LDO (Lido)

**Position dominante et bien auditée** : sécurise plus de 25% de tout l'ETH staké sur
Ethereum — leader incontesté du liquid staking. 120 audits au total (99 pour Lido on
Ethereum, le plus récent d'avril 2026), programme continu depuis 2020 par des cabinets
reconnus (Certora, OpenZeppelin, ChainSecurity, Sigma Prime...), bug bounty Immunefi
([docs.lido.fi](https://docs.lido.fi/security/audits/)).

**Incidents réels mais mineurs et bien contenus** : (1) mai 2024, malware sur la machine d'un
opérateur de nœud (Numic) — 0 fonds utilisateur touchés, sortie volontaire des validateurs en
3 jours ; (2) octobre 2023, incident de slashing (28,677 ETH) causé par une mauvaise
configuration d'un opérateur (Launchnodes) — remboursé par l'opérateur lui-même sur ses
propres fonds ; (3) mai 2025, clé oracle compromise (Chorus One) — perte de seulement
1,46 ETH (frais de gas), 0 fonds utilisateur touchés, rotation de clé en urgence via vote DAO
([CoinDesk](https://www.coindesk.com/tech/2025/05/12/ethereum-staking-giant-lido-loses-just-14-eth-in-hacking-attempt)).
Aucun de ces 3 incidents n'est un échec du protocole lui-même — tous au niveau opérateur,
contenus rapidement.

**⚠️ Conflit de gouvernance reconnu par Lido lui-même** : les détenteurs LDO contrôlent la
gouvernance (paramètres de frais) tandis que les détenteurs stETH portent le risque réel de
slashing — un désalignement structurel explicitement documenté dans la doc officielle.
La "Dual Governance" (permet aux détenteurs stETH de bloquer/retarder une proposition)
atténue partiellement ce risque, sans le supprimer
([lido.fi/known-risks](https://lido.fi/how-lido-works/known-risks-and-mitigations)).

## Round 2 — validation par vraie recherche web (31/07, agent dédié, post-committé)

Un second tour, cette fois avec une VRAIE recherche web (pas la mémoire du modèle) pour
challenger le tableau ci-dessus et vérifier qu'aucun meilleur candidat n'a été oublié. Verdict
par candidat, chacun réévalué avec des faits datés de juillet 2026 :

- **AAVE — confirmé fort, renforcé.** "Aavenomics 3.0" (live le 27/06/2026) automatise des
  rachats sur marché ouvert **financés à 100% par le revenu réel** (~402M$ annualisé selon
  DefiLlama), retirant ~292 AAVE/jour de la circulation ; plus de 205 000 AAVE (1,28% du
  supply) déjà rachetés depuis avril 2025. Le budget a été réduit de 50M$ à 30M$/an en
  mars 2026 suite à une baisse de 25% du revenu des frais d'emprunt — signal de **discipline**
  (ajustement sur le revenu réel plutôt qu'une promesse intenable), pas un défaut
  ([The Defiant](https://thedefiant.io/news/defi/aave-confirms-aavenomics-3-0-live-buybacks-dao-spending-cut) ;
  [gouvernance Aave](https://governance.aave.com/t/arfc-buyback-program-budget-adjustment/24229) ;
  adresse vérifiée sur [BaseScan](https://basescan.org/address/0x63706e401c06ac8513145b7687a14804d17f814b)).
- **UNI — confirmé fort, se rapproche désormais d'AAVE.** Le fee switch a été étendu par la
  Proposition de gouvernance 100 (27/07/2026) à des pools v4 sur 7 réseaux ; mécanisme
  TokenJar (brûler UNI pour réclamer les frais) — économiquement équivalent à un rachat
  d'actions, jamais un dividende qui poserait un risque réglementaire. Revenu ~23M$ en 2026
  post-activation, +27M$/an supplémentaire estimé
  ([The Defiant](https://thedefiant.io/news/defi/uniswap-passes-unification-fee-switch-proposal)).
- **LINK — confirmé faible, le défaut tient toujours.** Chainlink Economics 2.0 est
  explicitement une TRANSITION en cours ("les récompenses de staking commenceront à passer
  des émissions vers les frais organiques") — pas encore assainie. Le pool de staking a
  grossi vers une cible de 75M LINK (contre 45M) — dilution qui continue tant que la
  transition n'est pas achevée ([chain.link/economics/staking](https://chain.link/economics/staking)).
- **MKR/SKY — disqualification aggravée.** La conversion forcée 1:24000 (sept. 2024) reste
  contestée en 2026 : un vote a maintenu la marque Sky (79,3%) puis Sky a ouvert un vote pour
  imposer une **pénalité de 1%** aux détenteurs n'ayant toujours pas converti leur MKR — preuve
  d'une migration forcée non terminée. Pire : une enquête indépendante montre que **4 entités
  seulement** représentent la quasi-totalité des votes ayant maintenu la marque Sky — échec
  net de gouvernance décentralisée, pas seulement un historique de fusion
  ([The Block](https://www.theblock.co/post/371401/sky-opens-vote-to-penalize-stragglers-delaying-mkr-to-sky-token-conversion) ;
  [The Block, concentration des votes](https://www.theblock.co/post/325096/just-four-entities-account-for-nearly-all-the-votes-to-keep-makerdaos-rebranding-to-sky)).
- **CRV — confirmé faible, deux problèmes non vus au premier tour.** (1) Convex contrôle à
  lui seul ~47% des votes veCRV, et des entités alignées au fondateur (Swiss Stake AG)
  figurent aussi parmi les plus gros verrouilleurs — gouvernance dominée par une poignée
  d'acteurs. (2) Le fondateur Michael Egorov a par le passé engagé des positions de levier
  personnelles ayant menacé la solvabilité du protocole (crise de 2023) — risque systémique
  lié à une personne, pas à un protocole vraiment distribué
  ([Blockworks](https://blockworks.co/news/curve-founder-faces-community-pushback-on-funding-proposal)).
- **LDO — confirmé faible (nouveau défaut structurel trouvé).** "Les détenteurs de LDO
  gouvernent une trésorerie, pas un flux de revenu" — désalignement explicite entre la
  domination du protocole (32Md$ TVL, 75,4M$ de revenu annualisé) et la valeur captée par le
  token (rachat de seulement 1,95M$ à ce jour). Le token se négocie à **-96%** de son sommet de
  2021 malgré des fondamentaux solides — symptôme direct de ce défaut
  ([ainvest.com](https://www.ainvest.com/news/ldo-lido-dao-research-2606/)).

**Aucun meilleur candidat trouvé** : l'agent a aussi vérifié GMX (récompenses de staking
suspendues, pas natif Base, un seul cycle), Synthetix (transition tokenomique instable,
incident de dépeg sUSD en 2026), Compound et Morpho (aucun mécanisme de capture de revenu
pour le token, Morpho en plus trop récent pour un track record multi-cycles) — tous échouent
sur au moins un des 6 critères.

## Round 3 — découverte de 3 nouveaux candidats (24 agents, vraie recherche web)

Un tour de découverte dédié (Scope avec WebSearch réel, pas la mémoire du modèle) a cherché
explicitement des candidats non encore évalués capables de rivaliser avec AAVE/UNI. Verdict
après vérification croisée manuelle des 18 sources récupérées :

- **Yearn Finance (YFI) — intéressant sur le papier, mais deux défauts réels.** Fair-launch
  (août 2020, zéro allocation VC/équipe), pas de fusion de token, rachat/burn financé par les
  performance fees des vaults, multisig limité à un rôle de veto (ne peut jamais proposer).
  Mais : **TVL concentré à 85,8% sur Ethereum** malgré une présence sur 7 chaînes dont Base —
  échoue le critère #2 (dépendance quasi-totale à un écosystème)
  ([DefiLlama](https://defillama.com/protocol/yearn-finance)). **Revenu réel bien trop faible** :
  seulement 776 911$/an de revenu protocolaire (10,53M$ de frais bruts annualisés) — un ordre
  de grandeur en dessous d'AAVE/UNI, TVL total modeste (181M$). Gouvernance pas pleinement
  décentralisée : le pouvoir de veto du multisig "ychad.eth" repose sur convention sociale,
  "non formalisé dans un accord légal ou du code"
  ([forum de gouvernance Yearn](https://gov.yearn.fi/t/yip-xx-convert-ychad-eth-into-a-borg/14531)).
  Historique d'incidents de sécurité récurrents (exploit majeur 2021, exploit flashloan 2023,
  incident de 300k$ en décembre 2025).

- **PancakeSwap (CAKE) — le plus solide économiquement des 3, mais deux vrais red flags.**
  34 mois consécutifs de déflation nette (juin 2026), 56M CAKE brûlés net (~14% du pic de
  supply), cible de -20% de supply d'ici 2030, vraiment multi-chaîne (10 chaînes dont Base)
  ([thecryptoupdates.com](https://www.thecryptoupdates.com/pancakeswaps-cake-token-burns-remove-56-million-tokens/)).
  Mais : **lien historique fort avec Binance** (financé par le Binance Accelerator Fund,
  construit sur BSC) — risque de centralisation similaire à ce qu'on cherche à éviter
  ([Gate.com](https://www.gate.com/learn/articles/what-is-pancakeswap-all-you-need-to-know-about-cake/3942)).
  **A déjà changé fondamentalement son modèle de tokenomics une fois** : retrait complet du
  système veCAKE (vote-escrow) en avril 2025 pour "Tokenomics 3.0" — pas une fusion de token à
  proprement parler, mais un signal d'instabilité structurelle répétée
  ([ChainPlay](https://chainplay.gg/blog/pancakeswap-launches-cake-3-with-major-governance-overhaul/)).

- **SushiSwap (SUSHI) — à écarter clairement.** Revenu réel effondré de deux ordres de
  grandeur depuis son pic 2021 (270 500$ de revenu mensuel cité en 2024, contre des millions
  au pic). **Restructuration en cours vers un écosystème multi-token**, éloignement du modèle
  DAO à un seul token SUSHI — viole directement le critère #1
  ([Cointelegraph](https://cointelegraph.com/news/sushiswap-replaces-dao-labs-model-multi-token-ecosystem)).
  Crise de liquidité documentée (décembre 2022, seulement 1,5 an de trésorerie de
  fonctionnement), vrai hack en mars 2023 (~3,3M$, code non audité)
  ([Halborn](https://www.halborn.com/blog/post/explained-the-sushi-swap-hack-march-2023)), et
  un épisode de malversation du fondateur (Chef Nomi a converti ~14M$ de fonds de
  développement en ETH en 2020, avant de les rendre sous pression communautaire).

**Conclusion du round 3** : aucun des 3 nouveaux candidats ne détrône AAVE/UNI. Yearn est le
plus proche en esprit (fair-launch, discipline économique) mais trop petit et trop concentré
sur Ethereum pour rivaliser sérieusement. PancakeSwap a un vrai mécanisme économique mais
porte un risque de centralisation historique (Binance) et un changement de tokenomics déjà
versé une fois. SushiSwap est clairement le plus faible des 3, à écarter.

## Synthèse — classement et verdict (mis à jour après le round 3)

| Candidat | Fusion de token | Gouvernance décentralisée | Modèle économique | Sécurité | Verdict |
|---|---|---|---|---|---|
| **AAVE** | Aucune prévue | Décentralisée dès 2020, timelock+guardian | Buyback financé par revenu réel, mais tendance récente en ralentissement | 65 audits, 1 seul incident (remboursé) | **Finaliste — moins cher aujourd'hui, tendance qui ralentit** |
| **UNI** | Aucune prévue | Timelock 2-30j, DUNA (2025) | Burn déflationniste, tendance récente en forte accélération (Prop. 100, 27/07/2026) | 9 audits, bug bounty 15,5M$ | **Finaliste — plus cher aujourd'hui, tendance qui accélère** |
| **PancakeSwap (CAKE)** | Pas de fusion, mais retrait complet du système veCAKE (avril 2025) | Lien historique fort avec Binance/BSC | Vrais burns financés par le revenu (34 mois de déflation nette) | Non vérifiée en détail | Écarté — risque de centralisation Binance |
| **Yearn Finance (YFI)** | Aucune, fair-launch 2020 | Multisig limité à un veto, mais pas formalisé légalement | Revenu réel bien trop faible (776k$/an) | Incidents récurrents (2021, 2023, 2025) | Écarté — dépend à 85,8% d'Ethereum, trop petit |
| **LDO** | Aucune prévue | Dual Governance (partiel) | ⚠️ Domination de marché sans capture de valeur pour le token | 120 audits, incidents mineurs bien gérés | Faible — nouveau défaut confirmé |
| **LINK** | Aucune prévue | Non documentée (trou réel) | ⚠️ Émissions > revenu réel, transition inachevée | Aucun incident oracle, dilution Labs opaque | Faible — défaut confirmé round 2 |
| **CRV** | Aucune prévue | Convex ~47% des votes + risque fondateur personnel | Émissions classiques | A subi un vrai hack (2023), score 6,5/10 | Faible — gouvernance concentrée confirmée |
| **SushiSwap (SUSHI)** | Restructuration multi-token en cours | Sushi Labs (conseil), pas une DAO simple | Revenu effondré (2 ordres de grandeur sous le pic 2021) | Vrai hack 2023 (3,3M$) + crise de liquidité 2022 | **Écarté clairement** |
| **MKR/SKY** | **DÉJÀ vécue** (Endgame), migration encore forcée en 2026 | 4 entités = quasi tous les votes | Buyback + réduction d'émissions (seul point positif) | Réserves gérées par simple EOA | **Disqualifié, aggravé round 2** |

**Recommandation finale de cette session** : **AAVE et UNI restent les deux seuls finalistes**
après 3 rounds de vérification (37 + 1 + 24 agents, tous avec vraie recherche web) — aucun
7e/8e/9e candidat parmi GMX/Synthetix/Compound/Morpho/Yearn/PancakeSwap/SushiSwap ne les
détrône. Les deux ont des profils opposés à départager, pas juste un score composite unique :
- **AAVE** : moins cher aujourd'hui sur le ratio valorisation/revenu réel (~13x), mais
  tendance récente en ralentissement (TVL -52% depuis le pic de novembre 2025, croissance du
  revenu qui décélère, budget de buyback déjà réduit face à une baisse de revenu).
- **UNI** : plus cher aujourd'hui (~43-52x), mais tendance récente en forte accélération
  suite à l'extension du fee switch (Proposition 100, 27/07/2026) — signal très récent, pas
  encore confirmé sur la durée.
- Rappel méthodologique (cf. mémoire `feedback_valuation_ratio_over_ath_distance`) : la
  distance à l'ATH est un mauvais critère de départage (biais d'ancrage sur un pic
  potentiellement irrationnel) — le ratio valorisation/revenu ET sa tendance/vitesse doivent
  être combinés, jamais un seul chiffre statique.
- Tous les autres candidats (LDO, LINK, CRV, PancakeSwap, Yearn, SushiSwap) portent un défaut
  structurel confirmé et documenté qui les place en retrait d'AAVE/UNI. MKR/SKY reste le seul
  disqualifié au sens strict.

## Relecture croisée — 3 LLM externes (ChatGPT, Gemini, Grok), 31/07

Le texte de diligence a été soumis à 3 LLM indépendants pour challenge critique. Les trois
confirment les 6 critères et ne trouvent aucun 7e/8e candidat oublié (ChatGPT et Gemini
vérifient et écartent explicitement Pendle, Ethena, Hyperliquid — ce dernier étant un L1/
AppChain, pas un token EVM standard, échouant le critère #6). **Mais les trois se contredisent
sur le verdict final AAVE vs UNI** — résultat en soi le plus important de cet exercice : il
n'y a pas de réponse objective unique.

**Point commun ajouté aux critères** : un 7e critère informel — le **"moat"** (avantage
concurrentiel durable) — un protocole facilement copiable n'est pas le même actif qu'un
protocole quasi impossible à déloger, même à revenu égal.

**Biais méthodologiques reconnus par les 3** :
- Revenu brut vs revenu net réellement capturé (déjà noté en round 2)
- Nature du revenu : AAVE taxe un capital immobile (rente stable, prévisible) vs UNI taxe un
  flux de volume (hyper-corrélé à la volatilité du marché) — comparer leur vélocité de
  croissance directement biaise mécaniquement en faveur d'AAVE (déjà mature) contre UNI
  (capture en cours de montée)
- Risque de décote réglementaire non pricé par un simple ratio (SEC) — **vérifié** : la SEC a
  clos son enquête sur Uniswap Labs en février 2025 sans poursuite, après une Wells Notice
  d'avril 2024 ([CoinDesk](https://www.coindesk.com/policy/2025/02/25/sec-drops-investigation-into-uniswap-will-not-file-enforcement-action)) —
  ce risque spécifique est donc largement retombé, pas un facteur différenciant actif
  aujourd'hui contrairement à ce que les LLM supposaient sans le vérifier.

**Correction factuelle apportée par cette session** (aucun des 3 LLM ne l'avait) : Unichain/
UniswapX/hooks v4, cités par ChatGPT et Gemini comme des sources de revenu déjà actives pour
UNI, ne génèrent **pas encore** de revenu réel capturé — le fee switch V4 n'a pas été activé,
DefiLlama enregistre zéro revenu protocolaire sur V4 en juin 2026. Ce sont des potentiels
futurs, pas des faits acquis.

**Verdicts divergents** :
- **Gemini → AAVE** : capture de valeur naturelle (spread de taux sur du crédit inélastique),
  pas besoin de "prendre" la valeur des LPs comme UNI doit le faire via son fee switch —
  "Banque centrale de facto de la DeFi".
- **Grok → UNI** (légèrement) : TAM du trading plus large et structurel que le lending
  overcollatéralisé, moat de liquidité/marque plus difficile à déloger, et surtout — angle
  neuf non exploré par cette session — **Aave fait face à une concurrence réelle de Morpho et
  du "modular lending"** qui pourrait éroder son moat avec le temps.
- **ChatGPT** : ne tranche pas, présente les deux comme des profils différents (value vs
  growth) légitimes chacun à sa manière.

## Plafond de marché et capacité d'évolution (demande opérateur, 31/07)

**AAVE — risque de mur réel (Morpho), mais réponse déjà engagée et traction déjà prouvée.**
Morpho (architecture modulaire : marchés isolés + vaults curés par des gestionnaires de
risque professionnels) menace structurellement le modèle "pool monolithique" d'Aave —
~51,3% de part de marché TVL pour Aave contre ~9,8% pour Morpho en janvier 2026, mais Morpho
a grossi de 2Md$ à plus de 10Md$ en 2025-2026 sur une adoption institutionnelle réelle
([Crypto Economy](https://crypto-economy.com/morpho-and-the-institutionalization-of-defi-lending-infrastructure/)).
Réponse d'Aave : **V4 introduit une "Unified Liquidity Layer"** combinant la profondeur du
pool monolithique avec l'isolation de risque façon Morpho — leur plus gros changement
architectural depuis V2, une preuve d'adaptation active, pas d'immobilisme. Extensions de
produit avec **traction déjà mesurée en dollars, pas des promesses** : GHO (stablecoin)
dépasse 500M$ de capitalisation (54% du circulant staké en sGHO) ; **Aave Horizon** (marché
RWA institutionnel, lancé août 2025) est devenu le plus gros marché de prêt adossé à des
actifs réels de toute la DeFi en quelques mois — 570M$+ de dépôts, partenaires sérieux
(VanEck, Circle, Ripple, WisdomTree, Hamilton Lane)
([The Block](https://www.theblock.co/post/346075/aave-horizon)). TAM du secteur lending :
54Md$+ de TVL sur 380+ protocoles/80+ chaînes — marge de croissance réelle, pas saturé.

**UNI — ambition plus large sur le papier, moins matérialisée à ce jour.** Hooks v4 : des
milliers de pools déjà déployés en 2026, vraie adoption développeur (frais dynamiques,
TWAMM, oracles personnalisés, "compliance gates" pour entités régulées)
([DEXTools](https://www.dextools.io/tutorials/what-is-uniswap-v4-hooks-customizable-amm-guide-2026)).
**Unichain** (leur propre L2, live depuis février 2025, blocs de 1s, ~95% moins cher
qu'Ethereum) : pari d'infrastructure ambitieux mais **non garanti** — en concurrence directe
avec Base/Arbitrum/Optimism pour attirer la même liquidité. Expansion institutionnelle
réelle mais moins chiffrée qu'Aave : déploiement prévu sur Arc (chaîne stablecoin Circle,
Q3 2026), BlackRock a routé un fonds Treasury tokenisé via Uniswap (un signal, pas un chiffre
agrégé). Le métier de base (trading/swap) reste structurellement plus commoditisé et
copiable que le crédit (un emprunteur ne bouge pas sa dette aussi facilement qu'un trader
change de DEX) — point déjà soulevé par Gemini lors de la relecture croisée.

## Exposition publique et partenariats de distribution (demande opérateur, 31/07)

**UNI a un catalyseur net et déjà confirmé** : Uniswap est l'AMM natif et l'infrastructure
DeFi centrale de la **Robinhood Chain** (nouveau L2 de Robinhood) — plus de 250M$ de volume
dès la première semaine de lancement. Un analyste de Standard Chartered affirme explicitement
que le marché **sous-estime** ce partenariat, qui positionne Uniswap comme couche de
liquidité par défaut pour les actions tokenisées/RWA (marché projeté à 4 000 milliards $
d'ici 2028) ([BitcoinWorld](https://bitcoinworld.co.in/standard-chartered-uniswap-robinhood-partnership-underestimated/) ;
[blog officiel Uniswap](https://blog.uniswap.org/robinhood-chain-is-live)). Robinhood
apporte des millions d'utilisateurs retail déjà captifs — un vrai canal de distribution à
grande échelle, pas une annonce théorique.

**AAVE mise sur du B2B/institutionnel et sa propre app, pas un partenariat de distribution
grand public équivalent** : une "Tokenized Asset Coalition" avec Coinbase/Circle (coalition
institutionnelle), cbBTC comme actif de lancement d'Aave V4 (intégration technique, pas
marketing), et une app mobile propre ("Aave App") ciblant le premier million d'utilisateurs
via un rollout grand public début 2026 — un effort réel mais qui repose sur leur propre
distribution, pas sur un partenariat avec un acteur déjà établi à des millions d'utilisateurs
comme Robinhood.

**Verdict sur cette dimension** : avantage net à UNI — catalyseur de distribution/volume
plus fort et déjà confirmé, renforçant son profil "growth", même si (comme pour Unichain/
hooks) c'est plus récent et moins éprouvé dans la durée que la traction déjà installée
d'AAVE sur GHO/Horizon.

**Conclusion finale de cette diligence** : la méthodologie et le filtrage (6+1 critères) sont
solides et confirmés par 3 relectures indépendantes. Le choix final entre AAVE (revenu déjà
mature et prouvé, extensions de produit déjà chiffrées en dollars, mais croissance plus
limitée et concurrence Morpho à surveiller) et UNI (revenu en accélération, catalyseur de
distribution Robinhood confirmé, mais paris d'extension — Unichain, hooks monétisés — encore
moins matérialisés) reste un vrai choix de profil — value vs growth — sans réponse objective
unique, à trancher selon l'horizon et la tolérance à parier sur une capture de valeur pas
encore pleinement prouvée.

## Round 4 — élargissement au top 200 toutes chaînes (31/07, recherche directe WebSearch)

Suite à l'ouverture explicite de l'opérateur à détenir le capital sur n'importe quelle
blockchain ("sa me derange pas de hold notre capital sur solana ou une autre blockchain sa
reste du transfert rare"), un premier workflow de criblage sur le top 200 (par capitalisation)
a été lancé puis interrompu par l'opérateur ("stop tous") en cours de Search — seuls les
résultats déjà en cache ont été récupérés. Sur demande explicite de l'opérateur ("fais toi
meme tes recherhce"), les 8 candidats les plus prometteurs issus du criblage partiel ont
ensuite été vérifiés par recherche directe (WebSearch, pas un nouveau workflow multi-agents) :
HYPE, PYTH, OP, STX, JUP, GNO, INJ (+ AVAX/SOL/TAO déjà couverts par ailleurs). RAY
(Raydium/Solana), RUNE (THORChain) et GRT (The Graph) restaient en cours de Search au moment
de l'arrêt — pas encore vérifiés directement, à traiter dans un futur round si besoin.

| Candidat | Capture de valeur vérifiée | Point fort | Red flag confirmé |
|---|---|---|---|
| **HYPE** (Hyperliquid) | La plus forte de toute la diligence — Assistance Fund : 97% des frais → rachat continu, ~1,3Md$/an de revenu, intensité de rachat ~7%/an du market cap (4-5x Ethereum/BNB) | Financé à 100% par le vrai revenu, pas par émission/trésorerie | **Sérieux** : incident de manipulation de prix (nov. 2025, ~4,9M$, retraits/pont temporairement suspendus) + validateurs de la Fondation encore à ~49,3% du stake + critiques publiques sévères de figures reconnues ("arrêtons de prétendre qu'Hyperliquid est décentralisé" — Arthur Hayes ; comparaison à "FTX 2.0" — CEO de Bitget) |
| **INJ** (Injective) | Mécanisme le plus ANCIEN et éprouvé du lot — burn auction actif depuis décembre 2021 (renommé "Community BuyBack" oct. 2025), cadre déflationniste renforcé (IIP-617) voté à 99,96% | Track record long, pas une mode 2026 ; large consensus de gouvernance | Modéré : pouvoir de vote concentré chez des validateurs institutionnels (risque de centralisation typique Cosmos-SDK) |
| **PYTH** (Pyth Network) | Réel mais récent — Pyth Reserve lancé déc. 2025, 33% du revenu mensuel → rachat marché ouvert ; Pyth Pro (produit institutionnel) déjà 1M$ ARR à son 1er mois, projeté 50M$ ARR sous 12-18 mois | Partenariat Nasdaq confirmé — vrai canal de distribution institutionnel | Chiffres encore modestes en absolu ; gros "cliff unlock" le 19/05/2026 (fin de vesting 30 mois) — risque de dilution/pression vendeuse |
| **OP** (Optimism) | Réel mais modeste — gouvernance a approuvé (84,4%) un rachat à 50% du revenu net du séquenceur Superchain, pilote 12 mois depuis février 2026, ~8M$/an disponibles | Écosystème Superchain (inclut Base) | Chiffre nettement plus petit que HYPE/AAVE/UNI ; tokens rachetés vont d'abord en trésorerie, **pas automatiquement brûlés** — rien de garanti au-delà du pilote |
| **STX** (Stacks) | Mécanisme unique et réel — rendement en vrai BTC (Proof-of-Transfer) depuis janvier 2021, 4200+ BTC distribués aux stakers | Lien direct et vérifié à Bitcoin, historique long | La nouvelle phase de rendement BTC (PoX-5) est encore **centralisée/permissionnée** (capacité plafonnée ~3000 BTC, contrôlée par "Stacks Endowment", partenaires institutionnels "curated" — pas encore décentralisée, PoX-6 futur doit lever ce contrôle mais n'est pas encore fait) |
| **GNO** (Gnosis) | Historique le plus long du lot (2017) — vrai débat démocratique fonctionnel : GIP-150 (redemption pro-rata) rejetée, GIP-151 (fenêtre de rachat limitée) approuvée en juin 2026, distribution réelle de trésorerie actée | Gouvernance qui fonctionne réellement (contraste net avec JUP ci-dessous) | Mécanisme one-off (liquidation partielle ponctuelle sur un débat précis "retour sur la levée 2017"), pas un modèle récurrent comme un buyback continu |
| **JUP** (Jupiter) | Mécanisme de rachat/burn réel sur le papier | — | **Disqualifiant** : gouvernance DAO **entièrement suspendue** depuis fin 2025 pour "rupture de confiance" documentée par plusieurs sources (The Block, DL News, CoinMarketCap) — équipe/fondateurs ~20% du supply total, 220M JUP votés en bonus pour le co-fondateur Ming Ng, package salarial de 7M$ pour 4 nouveaux employés, une seule wallet d'équipe a représenté 4,5% des votes sur une proposition. Échoue clairement le critère #3 (gouvernance décentralisée) — plus sévère que MKR/SKY ou CRV |

**Verdict Round 4** : **JUP écarté** — capture de gouvernance par l'équipe, cas le plus flagrant
de toute la diligence à ce jour. **HYPE a le mécanisme de capture de valeur objectivement le
plus puissant** du panel entier (dépasse même AAVE/UNI en intensité de rachat), mais le risque
de gouvernance/centralisation est réel, documenté, et porté par des voix reconnues du secteur —
à ne jamais minimiser. **INJ ressort comme le compromis le plus solide sur la durée** parmi ces
nouveaux candidats : mécanisme éprouvé depuis 2021 (pas une mode récente comme HYPE/PYTH/OP),
consensus de gouvernance très large (99,96%), sans red flag aussi sérieux que HYPE ou JUP —
mais reste, comme AAVE/UNI, une question de profil (track record long et régulier vs mécanisme
plus jeune mais plus intense) plutôt qu'une réponse tranchée à la place de l'opérateur. Aucun
de ces candidats ne "bat" objectivement AAVE/UNI sur l'ensemble des 6+1 critères — chacun a un
red flag propre (gouvernance JUP, centralisation HYPE/STX-phase-actuelle/validateurs INJ,
mécanisme one-off GNO, chiffres encore modestes OP/PYTH) qui n'a pas d'équivalent aussi grave
chez AAVE ou UNI. **Rappel permanent** : ceci reste une lecture technique/structurelle de faits
vérifiés, jamais une prédiction de prix ni un conseil en investissement personnalisé.

## Round 5 — les 3 derniers candidats du criblage top-200 (31/07, recherche directe WebSearch)

Traitement des 3 candidats restés en Search au moment de l'arrêt du workflow (RAY, RUNE, GRT).

| Candidat | Capture de valeur vérifiée | Point fort | Red flag confirmé |
|---|---|---|---|
| **RAY** (Raydium) | Mécanisme réel et fort, comparable en intensité à UNI/AAVE — 12% des frais de trading → rachat continu, envoyé vers une adresse de burn publique. ~196M$ dépensés cumulés, ~71M RAY rachetés (~26,4% du circulant, fin août 2025) ; 54M$ brûlés en un seul mois (janvier 2025, >10% du circulant à l'époque). Revenu brut ~9,1M$/mois (annualisé >109M$/an) contre seulement ~1,9M RAY d'émission/an | Rachat/burn financé par du revenu réel, ratio émission/rachat très favorable | Aucune donnée trouvée sur la structure de gouvernance réelle (clés admin, timelock) — à vérifier avant toute conviction ferme ; incohérence de données déjà relevée plus tôt dans cette diligence (un chiffre de supply circulant très supérieur au max supply théorique sur une source) — fiabilité des chiffres à confirmer par recoupement |
| **RUNE** (THORChain) | Pas de mécanisme de rachat/burn substantiel trouvé — modèle basé sur des émissions dynamiques ("Incentive Pendulum") vers nodes/LPs, pas une capture de valeur par revenu réel comme RAY/UNI/AAVE | Fonction réelle (pont cross-chain natif) | **Disqualifiant** : exploit de sécurité de ~10,8M$ le 15/05/2026 (3e incident majeur du protocole), halt d'urgence du réseau, RUNE -11 à -15%, reprise après 5 semaines de pause — échoue le critère #4 (pas de vraie capture de valeur) ET soulève un doute sérieux sur la robustesse technique répétée |
| **GRT** (The Graph) | Mécanisme faible/neutre — burns (taxe de curation/délégation + 1% des frais de requête) qui compensent l'essentiel de l'émission sans la dépasser nettement : "inflation nette proche de zéro, légèrement déflationniste" en période de demande saine — pas un moteur déflationniste actif comme RAY/UNI/HYPE | "Horizon" (déc. 2025) = plus gros changement architectural de l'histoire du protocole, signe d'évolution active | Pas de red flag disqualifiant trouvé, mais pas non plus de signal de capture de valeur convaincant — capture de valeur trop faible pour rivaliser avec les autres candidats déjà retenus |

**Verdict Round 5** : **RUNE écarté** — 3e incident de sécurité majeur du protocole en plus d'une
absence de vrai mécanisme de capture de valeur pour le token, cumul de deux défauts structurels
sérieux. **GRT ni disqualifié ni convaincant** — mécanisme de capture de valeur trop faible
(quasi-neutre) pour rivaliser avec HYPE/INJ/AAVE/UNI/RAY sur ce critère précis. **RAY est le
candidat le plus solide de ce dernier lot** sur la capture de valeur pure (rachat/burn aussi
intense que RAY/UNI en proportion), mais avec un vrai trou de diligence non résolu : aucune
donnée trouvée sur sa gouvernance réelle (clés admin/timelock), à combler avant toute conviction
ferme — et l'incohérence de données déjà repérée sur ce token appelle à la prudence sur la
fiabilité des sources disponibles.

**Bilan des 10 candidats top-200 traités (Round 4 + 5)** : sur 10 candidats vérifiés au-delà
d'AAVE/UNI, 2 disqualifiés pour de vrais red flags structurels (JUP — capture de gouvernance ;
RUNE — incidents de sécurité répétés + absence de capture de valeur), et aucun ne dépasse
objectivement AAVE/UNI sur l'ensemble des 6+1 critères. RAY et INJ ressortaient comme les plus
sérieux du lot restant — trou de gouvernance RAY comblé ci-dessous.

## Complément — gouvernance et supply RAY vérifiées (31/07, demande opérateur explicite)

**Supply : incohérence résolue, aucune anomalie réelle.** Le chiffre "269,3 milliards" relevé
en Round 4 était une erreur de lecture d'une source antérieure — circulant réel confirmé :
**269 103 895 RAY** (~269,1 millions) sur un supply max/total de **555 000 000 RAY** (~48,5%
déjà en circulation), émission résiduelle ~1,9M RAY/an ([Tokenomist](https://tokenomist.ai/raydium)).
Cohérent et sans signal d'alerte.

**Gouvernance : PAS une DAO décentralisée — contrôle par multisig d'équipe, red flag confirmé.**
Vérifié directement dans la doc officielle Raydium et par recoupement ([Raydium Docs — Access
Controls](https://docs.raydium.io/raydium/protocol/security/access-controls) ;
[Squads](https://squads.xyz/blog/solana-multisig-program-upgrades-management)) :
- L'autorité de mise à niveau/admin du programme AMM est sous un **multisig Squads 3-sur-4** —
  3 signataires suffisent pour changer le code du protocole.
- La trésorerie est gérée par un **multisig séparé 3-sur-5**, portée plus étroite, **sans
  timelock du tout**.
- Sources contradictoires sur l'existence d'un vrai timelock côté programme : une source
  récente cite 24h, mais la doc officielle plus ancienne indique explicitement l'**absence** de
  mécanisme de timelock — Solana n'a pas de programme timelock natif, et Raydium ne "réplique"
  ce comportement que via un vote de gouvernance, pas une contrainte on-chain automatique.
- Tous les programmes Anchor partagent une **seule clé admin (Pubkey) codée en dur** pour le
  contrôle d'accès au niveau instruction — pas de vote de détenteurs de token RAY sur les
  changements de protocole, contrairement à AAVE/UNI/INJ qui ont tous une vraie DAO on-chain.

**Verdict** : RAY **échoue clairement le critère #3** (gouvernance réellement décentralisée) —
c'est un contrôle d'équipe via multisig (3/4 et 3/5 signataires), pas une gouvernance
communautaire avec vote de détenteurs de token. Comparable en gravité à MKR/SKY côté
concentration de pouvoir, sans même l'alibi d'un vote DAO formel que ce dernier a.

**Correction méthodologique (31/07, captures CoinGecko/Token Terminal fournies par l'opérateur)
— le chiffre de revenu du Round 4 était erroné.** Le "9,1M$/mois" utilisé en Round 4 pour
estimer un revenu annualisé de >109M$/an était en réalité un chiffre de **frais bruts**, pas le
revenu net réellement capturé — exactement le piège frais-bruts-vs-revenu-net déjà documenté
sur AAVE plus haut dans cette fiche (section « Round 2 »).
Données réelles (Token Terminal, 31/07) : **frais 24h = 100 312$** vs **revenu de projet 24h =
12 574$** (ratio ~8:1, cohérent avec les 12% des frais qui vont réellement au rachat RAY).
Revenu réel annualisé correct : 12 574$ × 365 ≈ **4,6M$/an** (pas 109M$/an). Ratio
valorisation/revenu réel recalculé : market cap 163,3M$ ÷ 4,6M$/an ≈ **35,6x** — comparable à
UNI (~43-52x), **pas un avantage de valorisation réel** contrairement à ce que suggérait le
chiffre erroné.

**Incohérence supplémentaire relevée (même captures)** : le widget "Tokenomique" (Tokenomist)
affiche 144,3M RAY en circulation contre 269,3M sur l'onglet "Présentation" de CoinGecko — deux
chiffres différents pour le même token sur la même page, probablement deux périmètres de
mesure distincts (offre totale déjà en circulation vs tranches débloquées selon le calendrier
de vesting Team/Community/Seed) jamais clarifiés par la source. Signal de qualité de données
plus faible que sur AAVE/UNI/INJ.

**RAY est donc déclassé sur DEUX fronts, pas un seul** : gouvernance (multisig d'équipe, pas de
DAO on-chain) ET valorisation (aucun avantage réel une fois le vrai revenu isolé du bruit des
frais bruts) — la capture de valeur ne sert à rien si 3 personnes peuvent changer les règles du
jour au lendemain, et le prix à payer n'est même pas plus avantageux qu'UNI pour ce risque.

**Conclusion mise à jour** : **aucun des 10 candidats top-200 vérifiés ne détrône INJ** sur ce
sous-groupe (HYPE explicitement écarté par l'opérateur pour risque de centralisation/incident ;
RAY déclassé sur gouvernance ET valorisation réelle ; RUNE disqualifié pour hacks répétés ;
PYTH/OP/STX/GNO/GRT trop jeunes, trop modestes, ou mécanisme insuffisant). Le choix final reste
entre AAVE et UNI (les deux finalistes historiques de cette diligence) et, en 3e position pour
un profil plus risqué mais éprouvé dans le temps, INJ — un vrai choix de profil, pas une case à
cocher.

**Complément supply INJ vérifiée (31/07, capture CoinGecko fournie par l'opérateur)** : supply
à **100 000 000 INJ, plafond dur** — calendrier de vesting entièrement terminé depuis 2023
(100% déjà en circulation, aucune tranche verrouillée restante), contrairement à RAY qui a
encore 410,7M de tokens verrouillés sur 555M (74% du supply total pas encore débloqué — vrai
risque de dilution future). Le mécanisme de burn (60% des frais dApp → rachat/burn hebdomadaire,
cf. Round 4) a déjà réduit ce supply de **~7,19M INJ brûlés** (~7,2% du max supply, fin juillet
2026) ([tokenomist.ai](https://tokenomist.ai/injective-protocol)) — INJ est donc structurellement
**déflationniste net**, pas seulement "sans nouvelle émission". Point supplémentaire en faveur
d'INJ face à RAY, en plus de la gouvernance et de la valorisation déjà tranchées ci-dessus.

**Précision technique (demande opérateur explicite, 31/07) — INJ n'est PAS nativement EVM.**
INJ est un token Cosmos-SDK (consensus Tendermint), pas un ERC-20 natif Ethereum. Injective a
récemment déployé un support EVM natif directement sur sa propre chaîne (architecture
"MultiVM" unifiant WASM + EVM + bientôt Solana VM), mais le token INJ lui-même reste géré
nativement via le module Cosmos Bank — une version wrapped (wINJ) existe pour interagir avec
la partie EVM de la chaîne
([The Block](https://www.theblock.co/post/378418/injective-rolls-out-native-evm-support-on-its-high-performance-cosmos-based-chain) ;
[Injective — MultiVM Token Standard](https://injective.com/blog/multivm-token-standard-wrapped-inj)).
Conséquence pratique : pas d'adresse MetaMask/Base classique pour détenir de l'INJ natif — passe
par un wallet Cosmos (Keplr) ou reste simplement sur un exchange centralisé. Friction
opérationnelle réelle à noter si INJ est retenu, sans être un facteur disqualifiant compte tenu
de l'ouverture déjà actée de l'opérateur à détenir du capital hors EVM.

**Nuance sur le critère #1 (pas de fusion/remplacement de token) — migration ERC-20 → natif
tout juste conclue (31/07, adresse Ethereum vérifiée par l'opérateur).** Il a existé un contrat
INJ ERC-20 historique sur Ethereum mainnet
([`0xe28b3b32b6c345a34ff64674606124dd5aceca30`](https://etherscan.io/token/0xe28b3b32b6c345a34ff64674606124dd5aceca30),
confirmé officiel — GitHub `InjectiveLabs/injective-token-contract`), utilisé par les grands
exchanges avant le mainnet natif Injective. **Migration finale vers le token natif (couche EVM
de la chaîne Injective elle-même, pas Ethereum) terminée le 22/07/2026** — Kraken a cessé de
supporter la version ERC-20 depuis le 27/07/2026 (il y a 4 jours au moment de cette diligence),
Coinbase a basculé vers le format natif ([CryptoBriefing](https://cryptobriefing.com/injective-migration-native-inj/) ;
[Kraken support](https://support.kraken.com/articles/injective-protocol-conversion-to-native-inj-token)).
**Différence claire avec le cas disqualifiant MKR→SKY** : aucun ratio de conversion punitif
trouvé, pas de rebranding de ticker/nom, pas de controverse communautaire identifiée — supply
et ticker INJ inchangés de bout en bout. Reste à noter honnêtement : c'est une migration de
contrat qui vient de se conclure CETTE SEMAINE MÊME, pas un non-événement — un facteur de
récence à surveiller (transition encore fraîche, tout exchange/wallet n'a peut-être pas encore
basculé), même si elle ne disqualifie pas INJ sur le fond de ce critère.

## Verdict final sur INJ — ratio valorisation/revenu appliqué (31/07, demande opérateur explicite)

Avant de valider INJ comme éventuel remplaçant d'AAVE/UNI, application de la même méthode
qu'ailleurs dans cette diligence (ratio market cap/revenu réel annualisé).

**Revenu réel INJ vérifié** : ~3-3,4M$/an (Token Terminal, 12 derniers mois) — burn de juin
2026 valorisé à >315k$, cohérent avec ce rythme annualisé
([CoinGecko](https://www.coingecko.com/learn/injective-2026-convergence-report) ;
[Messari](https://messari.io/project/injective-protocol)). **Market cap** : ~4,91$ ×
~92,8M en circulation (net du burn) ≈ 455M$. **Ratio valorisation/revenu ≈ 134x.**

| | AAVE | UNI | INJ |
|---|---|---|---|
| Ratio valorisation/revenu | ~13x | ~43-52x | **~134x** |
| Revenu réel annualisé | ~116M$+ | ~48-58M$ | ~3-3,4M$ |

**INJ est en réalité le candidat le PLUS CHER des trois relativement à son revenu réel** — pas
le moins cher. Le mécanisme de burn est solide en proportion (60% des frais, consensus 99,96%,
supply déflationniste net déjà vérifié ci-dessus), mais l'échelle absolue du revenu généré par
le protocole reste très modeste comparée à AAVE/UNI (et même à RAY, ~4,6M$/an).

**Conclusion définitive de cette diligence** : **INJ ne détrône ni AAVE ni UNI** sur la méthode
déjà validée par l'opérateur (ratio valorisation/revenu réel, cf. mémoire dédiée). Ses vrais
points forts (supply hard cap déjà entièrement débloqué, consensus de gouvernance large,
mécanisme de burn ancien et éprouvé) restent réels, mais à ce prix il représente un pari sur la
croissance FUTURE du revenu (thèse RWA/finance institutionnelle d'Injective), pas une valeur
déjà prouvée. **Le choix final de cette diligence reste entre AAVE et UNI** — INJ peut rester
une diversification hors-EVM en 3e position si un pari sur une croissance de revenu non encore
matérialisée est recherché, mais ne remplace pas les deux finalistes historiques.

## Décision opérateur (31/07) — UNI retenu comme 3e/4e pilier de conviction long terme

**Adresse contrat vérifiée** : `0x1f9840a85d5af5bf1d1762f925bdaddc4201f984` — contrat officiel UNI
sur Ethereum, confirmé sur [Etherscan](https://etherscan.io/token/0x1f9840a85d5af5bf1d1762f925bdaddc4201f984).
Contrat original depuis la genèse du token (novembre 2020, "Introducing UNI") — **jamais migré**,
à la différence d'INJ qui vient de changer de contrat le 22/07/2026.

**Choix acté** : UNI plutôt qu'AAVE — profil growth (revenu en accélération suite à l'extension
du fee switch du 27/07/2026, catalyseur de distribution Robinhood Chain déjà confirmé et
matérialisé en volume) contre le profil value d'AAVE (moins cher sur le ratio valorisation/
revenu, ~13x contre ~43-52x pour UNI, mais tendance de revenu qui ralentit nettement). Choix de
profil assumé par l'opérateur, cohérent avec l'ensemble de cette diligence — rappel permanent :
lecture technique/structurelle de faits vérifiés, jamais une prédiction de prix ni un conseil en
investissement personnalisé.

**Friction opérationnelle réelle trouvée (31/07) — pas de wrap officiel d'UNI sur Base.**
Vérifié directement sur la page multi-chaînes CoinGecko : UNI existe sur Ethereum, Unichain
(leur propre L2), Optimism, Arbitrum, Polygon, BNB Chain, Avalanche, Gnosis Chain, Near,
Harmony, Energi, Sora — **mais pas Base**. Une adresse "UNI" existe bien sur BaseScan
(`0xcac25237b1a55b2fff5a3c5b4219ab07f920890e`), vérifiée et **confirmée être un impersonateur** :
38 milliards de supply max (vs 1 milliard réel), seulement 50 holders, prix à 0,00$, aucun lien
vers la documentation/gouvernance officielle Uniswap — **à ne jamais utiliser**. Conséquence
pratique : détenir UNI nécessite de passer par Ethereum mainnet (frais de gas plus élevés que
Base) ou un des L2 listés ci-dessus (Optimism/Arbitrum/Unichain), pas la simplicité "tout sur
Base" dont bénéficie cbBTC.

**Recalcul du ratio valorisation/revenu (31/07, données CoinGecko/Token Terminal fraîches)** :
frais 24h 1 436 996$, revenu de projet 24h 204 033$ → revenu annualisé ≈ 74,6M$/an ; market cap
≈ 4,34$ × 624,9M ≈ 2,71Md$ → ratio ≈ **36x**, cohérent avec la fourchette ~43-52x déjà établie
(légèrement plus favorable sur cet instantané précis). **À surveiller, pas à ignorer** : frais
et revenu tous deux en repli sur 24h (-30,4% et -14,0% vs la veille) — signe possible que le pic
du 27/07 (~325k$/jour, extension du fee switch) était ponctuel plutôt qu'un nouveau plateau
soutenu ; la tendance ne se confirmera que sur plusieurs semaines, pas un seul jour.

## Clôture (31/07)

**Décision finale actée par l'opérateur** : UNI devient le 3e/4e pilier de conviction long
terme du capital personnel de l'opérateur (distinct du capital de trading ARIA), aux côtés de
BTC (cbBTC) et ETH. **Annonce publique prévue sur X dans plusieurs semaines** (calendrier
opérateur, pas encore fixé) — aucune action de communication engagée à ce stade, cette fiche
reste un document de diligence interne tant que l'annonce n'est pas décidée et exécutée par
l'opérateur (campagne marketing outward-facing = gatée opérateur, cf. `CLAUDE.md`).
