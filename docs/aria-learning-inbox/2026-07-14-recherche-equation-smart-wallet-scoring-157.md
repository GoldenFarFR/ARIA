[VPS Research]

# Briques candidates pour l'équation « smart wallet » maison d'ARIA (#157)

## Contexte et cadrage

Objectif : nourrir la conception d'une formule de notation de wallet
**propriétaire à ARIA**, jamais copier une formule tierce. Cette veille
survole quatre angles (métriques quantitatives de trading, méthodologies
publiques des outils déjà regardés, recherche académique sur le clustering
et la détection de traders informés, projets open-source) pour en extraire
des **briques candidates sourcées**, pas une formule finale — la
pondération/synthèse se fait avec le commandement après.

**Vérification préalable** : `services/smart_money.py` existe déjà côté
ARIA (cf. veille du 2026-07-14 sur le sourcing smart-money) et implémente
déjà, en germe, deux des briques ci-dessous (cohérence temporelle, entrée
précoce contrôlée, sortie disciplinée, anti-wash-trading basique par
contrepartie dominante). Cette recherche vise à **enrichir** ce module
existant avec des critères supplémentaires sourcés, pas à le remplacer.

---

## Brique 1 — Win rate + PnL réalisé (FIFO), le socle universel

**Ce que ça mesure** : proportion de trades clôturés en gain, et gain net
réalisé en dollars sur les positions fermées.

**Comment le calculer depuis Blockscout/GeckoTerminal** : reconstruire
l'historique de transferts d'un wallet pour un token donné (déjà fait par
`smart_money.py`/`blockscout.py`), apparier achats/ventes en FIFO (achat le
plus ancien apparié à la vente la plus ancienne), valoriser chaque jambe au
prix on-chain du moment (GeckoTerminal OHLCV pour la fenêtre concernée).
`win_rate = trades_gagnants / trades_totaux`.

**Fiabilité connue / limites** :
- C'est la méthode que Nansen documente publiquement utiliser (label
  « Smart Trader » basé sur PnL réalisé + seuils de ROI + cohérence dans
  le temps) et que Zerion utilise aussi pour son PnL API (norme FIFO
  documentée) — **méthode largement validée dans l'industrie, pas
  exotique**.
- **Limite documentée par la veille Zerion précédente** (2026-07-14) :
  la fiabilité du PnL dépend entièrement de la qualité du prix on-chain
  utilisé pour valoriser chaque jambe — sur un token low-cap tout juste
  lancé, un prix de pool fin peut fausser le calcul. Cette brique hérite
  donc de la même limite si utilisée sur des tokens très jeunes/illiquides
  — pas un problème pour ARIA qui dispose déjà de GeckoTerminal OHLCV en
  interne (donc mieux positionnée que Zerion sur ce point précis).
- **Win rate seul est trompeur** : un guide indépendant (baransel.dev,
  cf. brique 4) souligne qu'un wallet peut avoir un taux de réussite élevé
  mais un P&L net négatif (beaucoup de petits gains, un gros échec) — à ne
  jamais utiliser isolément, toujours croisé avec le P&L net.

---

## Brique 2 — Rendement ajusté au risque à la baisse (analogue Sortino)

**Ce que ça mesure** : le rendement d'un wallet pondéré par la volatilité
de ses pertes uniquement (pas la volatilité des gains, qu'on ne veut pas
pénaliser) — distingue un wallet qui gagne régulièrement d'un wallet qui a
eu un seul coup de chance suivi de grosses pertes.

**Comment le calculer depuis les données déjà disponibles** : construire
une série de rendements par trade clôturé (à partir des mêmes données FIFO
que la brique 1), calculer l'écart-type des seuls rendements négatifs
(downside deviation), puis `(rendement_moyen − taux_sans_risque) /
downside_deviation`. Pas besoin de nouvelle source de données — dérivable
de la même série que la brique 1.

**Fiabilité connue / limites** :
- Le ratio de Sortino est présenté comme la mesure de référence pour ce
  cas précis dans la littérature crypto (« particulièrement pertinent en
  crypto, où la volatilité à la hausse est désirable mais le risque à la
  baisse doit être minimisé ») — repères indicatifs cités : « au-dessus de
  1.0 = bon, 1.5 = objectif réaliste pour un trader actif, 2.0+ =
  excellent » (échelle conçue pour des fonds/traders établis, pas
  calibrée pour un wallet meme-coin — **ces seuils numériques ne sont pas
  transférables tels quels à ARIA**, juste une indication d'ordre de
  grandeur).
- **Limite structurelle pour un wallet individuel (pas un fonds)** : le
  ratio de Sortino suppose un historique de rendements suffisamment long
  pour être statistiquement significatif — sur un wallet qui n'a fait que
  3-5 trades, le calcul est bruité et peu fiable. Nécessite un seuil
  minimal de trades avant d'être appliqué (à définir avec le
  commandement).
- Le Maximum Drawdown et le ratio de Calmar (rendement annualisé /
  drawdown max) sont cités comme complémentaires — pertinents surtout pour
  un historique long, moins adaptés à un wallet spécialisé meme-coin dont
  l'activité est épisodique par nature.

---

## Brique 3 — Entrée précoce + taille contrôlée, AVEC le garde-fou méthodologique trouvé cette veille

**Ce que ça mesure** : le wallet entre tôt après le lancement d'un pool
(signal de conviction ou d'information), avec une taille d'achat
raisonnable (pas un unique apport massif qui ressemble à un insider connu
ou à un bot de sniping automatisé).

**Comment le calculer depuis Blockscout/GeckoTerminal** : déjà implémenté
dans `smart_money.py` (`_EARLY_ENTRY_WINDOW_SECONDS`, `_LARGEST_BUY_SHARE_MAX`)
— fenêtre de 3 jours après création de la paire, part du plus gros achat
plafonnée à 70% du volume total d'achats du wallet sur ce token.

**Fiabilité connue / limites — découverte importante de cette veille,
directement actionnable pour affiner la brique existante** : une étude
récente (arXiv 2607.02795, analyse de 166 098 lancements de tokens sur
Pump.fun, 1,58M observations d'acheteurs) a détecté 1012 cohortes de
wallets coordonnés apparaissant systématiquement parmi les tout premiers
acheteurs sur plusieurs lancements distincts (le groupe le plus actif
apparaît parmi les 10 premiers acheteurs sur 42 lancements différents) —
**via union-find sur un graphe de co-occurrence entre lancements**, pas
juste une fenêtre temporelle sur un seul token. **Mise en garde
méthodologique centrale de cette étude, directement pertinente pour ARIA**
: en comparant ces cohortes à des « placebos » (wallets choisis
uniquement par fréquence de lancement, sans lien réel), les placebos
montraient un impact sur le flux d'acheteurs *encore plus fort* que les
vraies cohortes (+216,3% contre +132,3%) — **ce qui réfute une
interprétation causale forte** : les wallets coordonnés apparaissent
plus souvent sur des lancements qui attirent déjà un flux organique
élevé pour des raisons indépendantes (biais de sélection), pas
nécessairement parce qu'ils causent ce flux. Les auteurs recommandent un
appariement par score de propension sur les covariables de qualité du
lancement pour isoler un effet de coordination réel.
- **Conséquence directe pour ARIA** : une « entrée précoce » sur UN seul
  token n'est pas un signal fiable en soi (déjà le cas dans
  `smart_money.py`, qui exige `criteria_met >= 2` — cohérent avec cette
  mise en garde). Mais **le vrai signal de coordination robuste, d'après
  cette étude, est la récurrence d'un même wallet en position d'acheteur
  précoce sur PLUSIEURS lancements distincts** — une dimension que
  `smart_money.py` ne capture pas encore (il analyse un token à la fois).
  C'est une extension concrète et sourcée à proposer au commandement :
  **fréquence d'apparition en tant qu'acheteur précoce à travers
  l'historique de scan d'ARIA**, pas seulement sur le token courant.

---

## Brique 4 — Diversification / consistance à travers plusieurs tokens

**Ce que ça mesure** : un wallet profitable sur un seul token peut être un
coup de chance (ou un insider de ce projet précis) ; un wallet profitable
à travers plusieurs tokens indépendants est un signal de compétence
généralisable.

**Comment le calculer depuis les données déjà disponibles** : compter le
nombre de tokens distincts (parmi ceux qu'ARIA a scannés/absorbés via
`screened_token`) où ce wallet apparaît en position d'early buyer avec une
issue positive selon `thesis_journal` — extension naturelle de l'option
« maison » déjà documentée dans la veille sourcing smart-money du 13/07.

**Fiabilité connue / limites** :
- Nansen documente publiquement ce critère sous le nom « trading
  diversity » (nombre de tokens profitables, composition du portefeuille)
  comme un des trois piliers de sa méthodologie « Smart Money », mais
  **sans jamais publier les seuils numériques exacts** — seulement les
  compteurs d'adresses labellisées résultants (~850 adresses en label
  « All-Time Smart Trader », ~200 en « 30D Smart Trader ») —
  confirmation que c'est un critère jugé important par un acteur
  établi, sans que le seuil précis soit copiable (et il ne devrait pas
  l'être, cohérent avec la consigne « jamais copier une formule
  propriétaire »).
- Un guide indépendant de construction d'un wallet analyzer maison
  (baransel.dev) recommande explicitement d'éviter les wallets classés
  « top 5 » sur un classement de leaderboard (soupçon de manipulation du
  classement) et de privilégier les rangs 20-100, et de rechercher les
  wallets qui apparaissent comme top traders **sur plusieurs tokens
  différents** plutôt qu'un seul — cohérent avec le critère de
  diversification.
- DeBank publie un concept proche mais différent : le **TVF (Total Value
  of Funds)**, valeur cumulée de tout ce qui est passé par un wallet dans
  sa durée de vie (pas juste le solde courant) — un signal de volume
  d'activité historique, complémentaire à la diversification mais pas
  équivalent (un wallet à fort TVF peut être un simple gros dépensier,
  pas forcément profitable).

---

## Brique 5 — Sortie disciplinée (anti-dump) et gestion de position

**Ce que ça mesure** : un wallet qui vend en plusieurs tranches
disciplinées se distingue d'un wallet qui liquide tout d'un coup (souvent
signe de panique, de bot, ou d'un insider qui sort avant une mauvaise
nouvelle).

**Comment le calculer** : déjà en germe dans `smart_money.py`
(`disciplined_exit`, basé sur le nombre de ventes ≥2 ou 1 vente après ≥1
achat). Extension possible sourcée par cette veille : le Maximum Drawdown
appliqué non pas au marché mais **au wallet lui-même** — mesurer la perte
maximale entre le pic de valeur non réalisée d'une position et sa
liquidation effective, pour distinguer un wallet qui prend ses gains près
du sommet d'un wallet qui laisse fondre un gain latent avant de vendre.

**Fiabilité connue / limites** :
- Le Maximum Drawdown est cité comme une des 5 métriques clés
  d'évaluation de systèmes de trading automatisés, avec un repère
  indicatif « <15% = bonne préservation du capital » — encore une fois un
  repère calibré pour des fonds établis, à recalibrer pour des positions
  meme-coin à volatilité intrinsèquement bien supérieure.
- **Limite pratique reconnue dans la littérature générale** : le
  Drawdown/Sortino/Calmar ne prennent leur sens qu'en lecture combinée,
  jamais isolément — un point répété dans plusieurs sources indépendantes
  de cette veille (angle 1), cohérent avec la consigne du commandement de
  ne pas produire une formule finale mais des briques à pondérer
  ensemble.

---

## Brique 6 — Filtre anti-coordination / anti-wash-trading (garde-fou transversal, pas une brique de score positif)

**Ce que ça mesure** : exclut les faux signaux — wallets qui se
échangent entre eux (wash trading) ou groupes de wallets contrôlés par
une seule entité qui simulent une convergence « smart money ».

**Comment le calculer depuis Blockscout** :
- **Anti-wash-trading (déjà implémenté)** : `smart_money.py` calcule déjà
  la part des échanges concentrés sur une contrepartie dominante hors LP
  (`_dominant_counterparty_share`) — méthode cohérente avec la littérature
  académique (Cong, Li, Tang & Yang, NBER/SSRN — « Crypto Wash Trading » :
  distributions de premier chiffre significatif anormales, arrondi de
  taille, motifs comportementaux robustes pour détecter les échanges
  fictifs sur les exchanges non régulés ; Victor & Weintraud, « Detecting
  and Quantifying Wash Trading on DEX » — sur IDEX/EtherDelta, plus de 30%
  des tokens tradés avaient été sujets à du wash trading, détecté en
  sommant les volumes par compte impliqué pour repérer les trades qui
  n'entraînent aucun changement de position nette).
- **Extension sourcée par cette veille — clustering multi-wallets d'une
  même entité** : la littérature sur le clustering d'adresses Ethereum
  (Victor, « Address Clustering Heuristics for Ethereum », FC 2020)
  documente des heuristiques précises et réutilisables : réutilisation
  d'une même adresse de dépôt (la plus efficace, permettant de clusteriser
  17,9% des adresses actives observées, révélant 340 000+ entités
  contrôlant plusieurs adresses), participation multiple aux mêmes
  airdrops, et auto-autorisation de transfert de token. Ces heuristiques
  sont directement applicables à ARIA pour vérifier qu'un groupe de
  wallets « convergents » (déjà le critère `>= 2 smart_wallets` dans
  `smart_money.py`) ne sont pas en réalité **une seule et même entité**
  déguisée en plusieurs wallets — actuellement non vérifié.

**Fiabilité connue / limites** :
- Toutes les sources s'accordent : ces heuristiques réduisent le risque de
  faux positifs mais n'éliminent jamais les faux négatifs (une entité
  suffisamment prudente peut éviter tous les motifs connus) — à traiter
  comme un filtre probabiliste, jamais une certitude.
- La mise en garde de la brique 3 (biais de sélection type « placebo »)
  s'applique aussi ici : un filtre trop agressif sur la coordination
  pourrait exclure de vrais wallets compétents qui, par coïncidence
  statistique normale, se retrouvent parfois sur les mêmes lancements
  precoces sans être liés.

---

## Note transversale sur la recherche académique en « informed trading » traditionnelle

Deux résultats de la littérature sur les marchés traditionnels, transposés
avec prudence (pas de preuve qu'ils s'appliquent tels quels à la crypto,
mais utiles comme cadre de pensée pour la conception de la formule) :

- Une étude 2025 (34 pays, 3,7M transactions d'insiders) montre que
  **combiner plusieurs indicateurs en une mesure composite surpasse
  systématiquement n'importe quel signal pris isolément** — validation
  indépendante, hors crypto, du principe déjà appliqué par ARIA
  (`criteria_met >= 2` dans `smart_money.py`) et par toutes les briques
  ci-dessus (jamais un critère seul, toujours une combinaison).
- La littérature sur les marchés crypto eux-mêmes (Félez-Viñas, Johnson &
  Putniņš, SSRN — « Insider trading in cryptocurrency markets ») documente
  des run-ups de rendement anormaux avant des annonces de cotation
  officielles, détectés via l'analyse combinée des annonces d'exchange et
  des données de transaction on-chain — confirme que la méthode générale
  (repérer un comportement on-chain anormal AVANT un événement de marché
  public) est validée académiquement pour la crypto spécifiquement, pas
  seulement en finance traditionnelle.

---

## Synthèse — 6 briques candidates, aucune formule finale

| # | Brique | Déjà dans `smart_money.py` ? | Source principale |
|---|---|---|---|
| 1 | Win rate + PnL réalisé (FIFO) | Non (comportement qualitatif seulement) | Nansen, Zerion, littérature trading |
| 2 | Ratio type Sortino (risque à la baisse) | Non | Littérature crypto risk-adjusted return |
| 3 | Entrée précoce + taille contrôlée, **+ récurrence multi-lancements** | Partiellement (fenêtre 3j, taille max) | `smart_money.py` + arXiv 2607.02795 (extension) |
| 4 | Diversification multi-tokens | Non | Nansen (« trading diversity »), baransel.dev, DeBank (TVF) |
| 5 | Sortie disciplinée / drawdown wallet | Partiellement (`disciplined_exit` binaire) | `smart_money.py` + littérature Drawdown/Calmar |
| 6 | Anti-wash-trading + anti-clustering d'entité | Partiellement (contrepartie dominante) | `smart_money.py` + Cong et al., Victor & Weintraud, Victor (clustering) |

**Aucune pondération ni seuil final proposé ici** — conformément à la
consigne. Les deux extensions les plus actionnables identifiées par cette
veille (récurrence multi-lancements pour la brique 3, heuristique de
clustering d'entité pour la brique 6) sont directement greffables sur le
module `smart_money.py` existant sans le réécrire — décision d'intégration
laissée entièrement au commandement.

## Sources

- [Nansen — Smart Money 101](https://academy.nansen.ai/articles/2132837-smart-money-101)
- [Nansen — Smart Money: How It Works](https://eco.com/support/en/articles/14800361-nansen-smart-money-how-it-works)
- [Zerion — Onchain PnL API (méthode FIFO)](https://zerion.io/blog/onchain-pnl-api-how-to-track-profit-and-loss-for-wallets-and-tokens/)
- [DeBank — Web3 Ranking](https://debank.com/ranking)
- [XBTO — Sharpe, Sortino & Calmar Ratios: Crypto Metrics Guide](https://www.xbto.com/resources/sharpe-sortino-and-calmar-a-practical-guide-to-risk-adjusted-return-metrics-for-crypto-investors)
- [Nurp — 5 Key Metrics for Evaluating Automated Trading Systems](https://nurp.com/algorithmic-trading-blog/5-key-metrics-automated-trading-systems/)
- [arXiv 2607.02795 — Coordinated Sniper Cohorts on Pump.fun](https://arxiv.org/abs/2607.02795)
- [Victor & Weintraud — Detecting and Quantifying Wash Trading on DEX (Berkeley DeFi)](https://berkeley-defi.github.io/assets/material/Detecting%20and%20Quantifying%20Wash%20Trading.pdf)
- [Cong, Li, Tang & Yang — Crypto Wash Trading (NBER)](https://www.nber.org/system/files/working_papers/w30783/w30783.pdf)
- [Victor — Address Clustering Heuristics for Ethereum (FC 2020)](https://www.ifca.ai/fc20/preproceedings/31.pdf)
- [Félez-Viñas, Johnson & Putniņš — Insider trading in cryptocurrency markets (SSRN)](https://ssrn.com/abstract=4184367)
- [baransel.dev — Track Smart Money on Solana: Build Your Own Wallet Analyzer](https://baransel.dev/post/track-smart-money-solana-build-wallet-analyzer/)
- [1f1n/Dragon (GitHub) — wallet PnL/winrate data collection](https://github.com/1f1n/Dragon)
- Code ARIA vérifié : `packages/aria-core/src/aria_core/services/smart_money.py`,
  `AGENTS.md` (§ méthode smart-money) — lecture directe, 2026-07-14
- Note liée : `docs/aria-learning-inbox/2026-07-14-veille-sourcing-smart-money-base.md`,
  `docs/aria-learning-inbox/2026-07-14-veille-zerion-pnl-fiabilite-low-cap.md`

## Frontières confirmées respectées

Aucun code touché, aucune formule finale produite ni implémentée. Chaque
brique est sourcée (méthodologie publique documentée, article académique,
ou lecture directe du code ARIA existant) — aucune donnée propriétaire
tierce utilisée, seulement du contenu marketing/documentation publique
(cohérent avec la consigne : s'inspirer de la méthode publiée par Nansen/
DeBank/Zerion n'est pas utiliser leurs données). Limites et risques de
faux positifs signalés explicitement pour chaque brique, jamais présentés
comme résolus. Synthèse/pondération laissée entièrement au commandement.
