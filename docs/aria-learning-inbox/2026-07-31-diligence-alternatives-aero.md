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

## Synthèse — classement et verdict

| Candidat | Fusion de token | Gouvernance décentralisée | Modèle économique | Sécurité | Verdict |
|---|---|---|---|---|---|
| **AAVE** | Aucune prévue | Décentralisée dès 2020, timelock+guardian | Buyback financé par revenu réel | 65 audits, 1 seul incident (remboursé) | **Meilleur candidat** |
| **UNI** | Aucune prévue | Timelock 2-30j, DUNA (2025) | Burn déflationniste financé par revenu réel | 9 audits, bug bounty 15,5M$ | **2e candidat solide** |
| **LDO** | Aucune prévue | Dual Governance (partiel) | Frais réels (staking ETH) | 120 audits, incidents mineurs bien gérés | Solide mais conflit de gouvernance reconnu |
| **LINK** | Aucune prévue | Non documentée (trou réel) | ⚠️ Émissions > revenu réel (défaut AERO) | Aucun incident oracle, mais dilution Labs opaque | Défaut structurel identique à AERO |
| **CRV** | Aucune prévue | Address Provider = 1 individu | Émissions classiques | A subi un vrai hack (2023), score 6,5/10 | Point de centralisation + historique d'exploit |
| **MKR/SKY** | **DÉJÀ vécue** (Endgame) | — | Buyback + réduction d'émissions (positif) | Réserves gérées par simple EOA | **Disqualifié** |

**Recommandation de cette session** : **AAVE** est le candidat le plus solide sur l'ensemble
des critères qui ont fait rejeter AERO — gouvernance décentralisée depuis le plus longtemps
(2020), modèle économique qui distribue moins qu'il ne gagne (l'inverse exact du problème
AERO), présence directe sur Base, aucun projet de fusion de token. **UNI** est un second choix
solide avec un mécanisme déflationniste tout aussi sain. **MKR/SKY est à écarter directement**
— il reproduit déjà le défaut n°1 qui a fait rejeter AERO. **LINK mérite prudence** malgré sa
dominance de marché — son modèle de récompenses est structurellement financé par des
émissions, pas par le revenu réel, exactement le défaut identifié chez AERO.

**Limite de cette diligence, honnêtement signalée** : la liste des 6 candidats vient d'une
sélection a priori (mémoire du modèle + 4 noms suggérés par l'opérateur), pas d'une recherche
web de validation. Un tour de validation/démolition dédié est prévu en complément pour
vérifier qu'aucun meilleur candidat n'a été oublié.
