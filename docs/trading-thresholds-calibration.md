# Calibration des seuils de trading — inventaire complet (28/07)

> Référencé depuis `CLAUDE.md`. Pendant du fichier `docs/api-rate-limit-calibration.md`
> (débits d'API) mais pour les SEUILS DE DÉCISION eux-mêmes — gates durs, poids de score,
> multiplicateurs de sizing, régimes. Objectif explicite (décision opérateur, 28/07) : à
> chaque vague de nouvelle information (recherche, incident, revue croisée), il faut
> pouvoir recalibrer PROPREMENT plutôt que de fouiller des dizaines de commentaires
> dispersés — et pouvoir refaire l'exercice dans quelques mois sans repartir de zéro.
>
> Ce fichier est un inventaire de référence, mis à jour à CHAQUE recalibration, dans le
> MÊME commit que le changement de code. Il ne remplace jamais le commentaire source à
> côté de chaque constante — les deux doivent rester cohérents (garde-fou mécanique :
> `test_coherence.py::test_trading_thresholds_match_calibration_doc`, voir en bas de ce fichier).
>
> Format par ligne : **Constante** — fichier:ligne — **valeur actuelle** — source/date —
> **revisiter si** (critère explicite de déclenchement d'une révision, jamais "un jour peut-être").

## Momentum — gates durs d'entrée (`momentum_entry.py`)

| Constante | Fichier:ligne | Valeur | Source/date | Revisiter si |
|---|---|---|---|---|
| `_MIN_LIQUIDITY_USD` | momentum_entry.py:160 | 50 000 $ | Décision opérateur 21/07 (abaissé de 100k, un premier chiffre de 30k avait été appliqué par erreur puis corrigé) | Le funnel montre ce gate comme goulot dominant sur plusieurs cycles consécutifs |
| `_MIN_LIQUIDITY_USD_FEAR` | momentum_entry.py:171 | 100 000 $ | Regime Switch, 20/07 (revue croisée Gemini, feu vert opérateur "200k mais à garder à l'œil") — x2 du plancher nominal | Après plusieurs mois de données réelles en régime Peur (note opérateur explicite : "à revérifier dans le temps") |
| `_MIN_LIQUIDITY_USD_SCALPING` | momentum_entry.py:183 | 15 000 $ | 26/07, décision opérateur après un vrai funnel (18/40 candidats rejetés sur ce seul gate en un cycle) | Nouveau funnel montrant scalping toujours affamé, ou au contraire trop permissif |
| `_RR_MIN_FOR_DIRECT_BUY` | momentum_entry.py:190 | 2.0 | 18/07, décision opérateur "plus sélectif" | Taux de faux positifs/négatifs observé sur les achats directs vs LLM |
| `_RR_AMBIGUOUS_FLOOR` | momentum_entry.py:191 | 1.0 | Inchangé depuis l'origine | — |
| `_ALIGN_SCORE_MIN_FOR_DIRECT_BUY` | momentum_entry.py:196 | 2 | 18/07, même décision que `_RR_MIN_FOR_DIRECT_BUY` | Idem |
| `MAX_VOLUME_TO_LIQUIDITY_RATIO` | momentum_entry.py:212 | 20.0x | 17/07, incident réel BRIAN (-17,9%, -8 962$, ratio ~91x observé) | Un nouveau cas confirmé de wash-trading sous ce seuil |
| `_MAX_PRICE_CHANGE_24H_PCT` | momentum_entry.py:227 | 200% | 17/07, incident TSG (+533%/24h) | Un vrai breakout légitime rejeté à tort et confirmé après coup |
| `_PARABOLIC_RESCUE_MAX_PCT` | momentum_entry.py:236 | 350% | 22/07, task #3 (rescue smart-money entre 200-350%) | — |
| `_MIN_VOLUME_24H_USD` | momentum_entry.py:425 | 500 $ | **ESSAI EN COURS depuis le 20/07** (abaissé 5000→1000 le 20/07, étendu 1000→500 le 21/07 sur 2 diagnostics funnel successifs) | "À réévaluer une fois l'effet sur le débit réel d'achats observé" (note explicite dans le code, jamais tranché définitivement) |
| `_MIN_VOLUME_TO_LIQUIDITY_RATIO` | momentum_entry.py:445 | 1% | 21/07, coordonné avec `_MIN_LIQUIDITY_USD` (jonction exacte à 500$) | Si `_MIN_LIQUIDITY_USD` change, vérifier que la jonction reste cohérente |
| `_MAX_TOP_HOLDERS_CONCENTRATION_PCT` | momentum_entry.py:458 | 80% (top 10) | 19/07, revue croisée Gemini, "seuil extrême assumé, pas une calibration fine" | — |
| `_RVOL_CONFIRMATION_MULTIPLIER` | momentum_entry.py:515 | 3.0x | — | — |

## Risk guard — sizing, coupe-circuits, régimes (`risk_guard.py`)

| Constante | Fichier:ligne | Valeur | Source/date | Revisiter si |
|---|---|---|---|---|
| `RISK_CAP_PCT` | risk_guard.py:58 | 2% | Plafond dur, pire cas | — |
| `CONVICTION_RR_THRESHOLD` | risk_guard.py:99 | 2.5 | — | — |
| `MIN_ALLOC_MULTIPLIER`/`MODERATE_ALLOC_MULTIPLIER`/`MAX_ALLOC_MULTIPLIER` | risk_guard.py:124-126 | 0.4 / 0.7 / 1.0 (paliers faible/modéré/fort, base 5%) | — | — |
| `FUNDAMENTAL_WEAK_THRESHOLD` | risk_guard.py:138 | 4.0/10 | — | — |
| `FUNDAMENTAL_REJECT_THRESHOLD` | risk_guard.py:153 | 2.5/10 | 25/07, incident réel CHECK (-27,3%, -7 374$, score fondamental 2.0 acheté quand même) | Un nouveau cas limite trouvé entre 2.5 et 4.0 |
| `REGIME_FEAR_SIZE_MULTIPLIER` | risk_guard.py:362 | 0.5x | Regime Switch 20/07 | — |
| `PRICE_IMPACT_RATIO` | risk_guard.py:420 | 2.0 | Règle AMM standard | — |
| `SOFT_DRAWDOWN_PCT`/`HARD_DRAWDOWN_PCT` | risk_guard.py:563-564 | -10% / -20% | — | — |
| `HARD_CONSECUTIVE_LOSSES` | risk_guard.py:565 | 5 | — | — |
| `MACRO_CIRCUIT_BREAKER_LOSS_PCT` | risk_guard.py:844 | -15% (équité combinée 3 poches) | 27/07, plan 3-poches | — |

## Market sentiment — Regime Switch (`skills/market_sentiment.py`)

| Constante | Fichier:ligne | Valeur | Source/date | Revisiter si |
|---|---|---|---|---|
| `_RSI_EUPHORIA` | market_sentiment.py:71 | 75.0 | — | — |
| `_RSI_OVERSOLD` | market_sentiment.py:72 | 30.0 | — | — |
| `_DRAWDOWN_CAPITULATION_PCT` | market_sentiment.py:77 | -35% | — | — |

## Bonding — gates durs + score composite (`bonding_entry.py`)

| Constante | Fichier:ligne | Valeur | Source/date | Revisiter si |
|---|---|---|---|---|
| `_MAX_DEV_HOLDING_PCT` | bonding_entry.py:129 | 5% | 24/07 | — |
| `_MAX_TOP10_HOLDER_PCT` | bonding_entry.py:156 | 100% (score-scale ceiling) | **Corrigé 28/07, Item #167** : était 80% (un seuil de rejet dur) jusqu'à ce qu'un workflow empirique (~380 candidats réels) trouve que top10_holder_pct ne descend JAMAIS sous ~93,8% même à 1000+ holders réels — le gate rejetait 100% des candidats qui atteignaient le seuil d'échantillon. Retiré comme gate dur, devenu le plafond (0 point) d'une échelle de score continue | — |
| `_TOP10_HOLDER_PCT_SCORE_FLOOR` | bonding_entry.py:177 | 90% (score plein) | **Nouveau 28/07, Item #167** — marge de sécurité sous le meilleur cas empirique observé (93,8%) | Une fois plus de résultats réels accumulés sur cette échelle |
| `_MIN_HOLDERS_FOR_CONCENTRATION_CHECK` | bonding_entry.py:185 | 50 | Relevé de 15→50 le 28/07 (échantillon 50 tokens + recherche, ratio jugé non-informatif avant ce seuil) | — |
| `_MIN_LIQUIDITY_USD` (bonding) | bonding_entry.py:214 | 5 000 $ | **Corrigé 28/07, Item #167** : était 10 000 $ (24/07) jusqu'à ce qu'un workflow empirique (50 lancements Base récents) trouve que la liquidité à ce stade est BIMODALE (92,7% des tokens à ~9 591 $, 7,3% à ~20 311 $, artefacts de config de lancement, pas un signal de marché) — l'ancien plancher rejetait 98% du flux réel sur un chiffre qui ne mesure rien | Workflow du 28/07 : Item #163 propose des signaux avant-coureurs de crash post-graduation qui pourraient affiner ce plancher |
| `_WEIGHT_DEV_SECURITY`/`_WEIGHT_PRODUCT_CONVICTION`/`_WEIGHT_TECHNICAL_SETUP`/`_WEIGHT_HOLDER_CONCENTRATION` | bonding_entry.py:242-245 | 35/35/15/15 | 24/07, "valeurs de départ à recalibrer une fois des résultats réels accumulés" (dixit le commentaire d'origine) | Une fois un vrai échantillon de trades bonding clôturés accumulé |
| `_SCORE_THRESHOLD` | bonding_entry.py:275 | 60/100 | 24/07 | Idem |
| `BONDING_SIZE_REDUCTION` | bonding_entry.py:142 | 0.5x | 24/07 | — |
| `_FALLBACK_TARGET_MULTIPLE` | bonding_entry.py:266 | 2.0x | 28/07, Item #152 (fallback target/invalidation pour permettre un achat sans signal technique) | — |
| `_FALLBACK_INVALIDATION_MULTIPLE` | bonding_entry.py:267 | 0.35 (perte max 65%) | 28/07, Item #152 pour le fallback ; élargi comme plancher volet 1 de l'Item #155 (le cas réel HOLO a montré des swings -55%/+122% comme bruit normal, justifiant cette largeur) | Un cas réel montrant que 65% est encore trop serré ou trop large |
| `_MAX_SUPPLY_PCT_BY_TIER` | bonding_entry.py:284 | 5%/2,5%/1% (strong/moderate/weak) | **Nouveau 28/07, Item #156** — plafond de sizing additionnel : jamais une part disproportionnée de la supply fixe d'un token en bonding, peu importe le budget-risque/impact-prix déjà appliqués génériquement | Une fois des résultats réels accumulés sur ce plafond |
| `_MAX_SUPPLY_PCT_DEFAULT` | bonding_entry.py:290 | 1% (le plus conservateur) | 28/07, Item #156 — fail-closed si le palier de conviction est inconnu | — |
| `_STALENESS_DAYS_THRESHOLD` | bonding_entry.py | 30 jours | **Nouveau 28/07, Item #161** — début de la décote "déclin organique" (un bonding non-gradué au-delà de ce délai est statistiquement moins susceptible de décoller) | Une fois des résultats réels accumulés |
| `_STALENESS_MAX_DAYS` | bonding_entry.py | 45 jours | 28/07, Item #161 — décote maximale atteinte à ce délai | Idem |
| `_STALENESS_MAX_PENALTY_PCT` | bonding_entry.py | 50% | 28/07, Item #161 — jamais plus qu'une réduction de moitié du score composite, quel que soit l'âge | Idem |
| `_STALENESS_WAIVER_POSTING_CADENCE` | bonding_entry.py | "active" | 28/07, Item #162 — un catalyseur daté réel (cadence de publication X active, `conviction_research.py`) annule la décote entièrement | — |
| `_BTC_LATE_CYCLE_SIZE_MULTIPLIER` | bonding_entry.py | 0.7x | **Nouveau 28/07, Item #165** — levier macro long terme (cycles de halving, `skills/btc_cycles.py`), distinct du Regime Switch court terme déjà appliqué génériquement ; jamais un bonus en début de cycle, seulement une réduction en phase distribution/baisse | Une fois des résultats réels accumulés sur ce levier |

## Bonding — mécanisme d'ordre limite (Item #158, `limit_orders.py`)

| Constante | Fichier:ligne | Valeur | Source/date | Revisiter si |
|---|---|---|---|---|
| `LIMIT_ORDER_WATCH_TRIGGER_MULT` | limit_orders.py:58 | 1.10x | 23/07 | — |
| `LIMIT_ORDER_EXPIRY_HOURS` | limit_orders.py:59 | 3h | 23/07 | — |
| `BONDING_LIMIT_ORDER_MIN_LIQUIDITY_USD` | limit_orders.py:70 | 20 000 $ | 28/07, Item #158 — proxy de market cap (même doctrine que `bonding_entry._MIN_LIQUIDITY_USD`), plus haut que le plancher d'entrée (5 000 $) : un ordre en attente sur un bonding tout juste au-dessus du plancher est trop instable pour qu'attendre une réversion de prix ait du sens | Une fois des résultats réels accumulés sur des ordres limites bonding |

## Bonding — stop de perte 3 volets et sortie (Item #154/#155, `paper_trader.py`)

| Constante | Valeur | Source/date | Revisiter si |
|---|---|---|---|
| `BONDING_TP_STAGES` | (1.0, 4.0, 11.5) = 2x/5x/12.5x prix | 28/07, recherche : cas réels 100x-11 900x mais tous -92% à -99,8% depuis le pic | — |
| `BONDING_TP_STAGE_FRACTIONS` | (0.45, 0.25, 0.20), ~10% moonbag jamais vendu | 28/07 | — |
| `BONDING_VELOCITY_DROP_PCT` | 40% | 28/07, Item #155 volet 2 | **Pas encore vérifié empiriquement** — le workflow empirique en cours (28/07) doit confronter ce chiffre à des cas réels |
| `BONDING_VELOCITY_WINDOW_MINUTES` | 30 min | 28/07, Item #155 volet 2 | Idem |
| `BONDING_LIQUIDITY_FLOOR_USD` | 10 000 $ | 28/07 ; **n'est plus un miroir exact depuis l'Item #167** (`_MIN_LIQUIDITY_USD` d'entrée est descendu à 5 000 $, ce plancher de sortie a été délibérément laissé inchangé, toujours strictement AU-DESSUS de l'entrée — jamais l'inverse) | Si ce plancher de sortie doit lui aussi bouger |
| `BONDING_LIQUIDITY_DROP_CUMULATIVE_PCT` | 50% | 28/07, miroir du plancher VC (`VC_LIQUIDITY_DROP_INVALIDATION_PCT`) | — |
| `BONDING_LIQUIDITY_SUDDEN_DROP_PCT` | 30% | 28/07, miroir du plancher VC (`VC_LIQUIDITY_SUDDEN_DROP_PCT`) | — |

## Position management générique (`paper_trader.py`)

| Constante | Valeur | Source/date | Revisiter si |
|---|---|---|---|
| `TRAIL_STOP_PCT` | 15% | Fallback historique, avant l'ATR | — |
| `ATR_TRAIL_MULTIPLIER` | 2.5x | 19/07, revue croisée Gemini ("2-3x, standard industrie") | — |
| `MIN_ATR_TRAIL_PCT`/`MAX_ATR_TRAIL_PCT` | 5% / 40% | 19/07 | — |
| `VC_MIN_LIQUIDITY_FLOOR_USD` | 30 000 $ | 20/07, même plancher que `safety_screen.py` (85% VC) | — |
| `VC_LIQUIDITY_DROP_INVALIDATION_PCT` | 50% | 20/07 | — |
| `VC_TAKE_SEED_MULTIPLE` | 2.0x | 20/07 | — |
| `VC_LIQUIDITY_SUDDEN_DROP_PCT` | 30% | 22/07, task #4 | — |
| `MAX_CONSECUTIVE_LOSSES_PER_CONTRACT` | 2 | 20/07, revue croisée externe (motif : incident BRIAN du 17/07) | — |
| `SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT` | 3 | 26/07, Item #101 | — |

## Pistes de recalibrage ouvertes (issues de la recherche du 28/07, pas encore tranchées)

- **Item #161** — signal de "déclin organique" (staleness) : le workflow du 28/07 propose ~30-45 jours sans graduation, à affiner (la fenêtre réelle de pic pourrait être encore plus courte, 24-48h selon les données Virtuals elles-mêmes).
- **Item #165** — `btc_cycles.py` existe déjà mais reste non branché à un levier de sizing ; proposition workflow : multiplicateur 0.7x en phase tardive de cycle, jamais un bonus.
- **Item #166** — pattern "second pump 1-2 mois après" : pas encore vérifié, risque de biais de survivance identifié.

## Gaps de complétude connus (audit du 28/07, pas encore comblés)

Cet inventaire couvre `momentum_entry.py`/`risk_guard.py`/`market_sentiment.py`/`bonding_entry.py`/
`paper_trader.py`, mais PAS encore de façon exhaustive même dans ces fichiers, et pas du tout
certains autres. Trouvé par un audit dédié (28/07) — noté explicitement plutôt que de prétendre
une couverture complète :
- **Polymarket (0% couvert)** : `polymarket_thesis.py`/`polymarket_paper_trader.py`/
  `polymarket_risk_guard.py` — toute une classe d'actif (Item #108) avec ses propres seuils
  (`MIN_WIN_PROBABILITY=0.85`, `KELLY_FRACTION=0.25`, `MAX_BET_PCT=0.05`, etc.) absente d'ici.
- **`entry_signals.py` (0% couvert)** : le golden pocket/RSI lui-même (`RSI_DIVERGENCE_MIN/MAX`,
  `_FIB_RATIOS`) — partagé par momentum ET bonding, cœur du pipeline, actuellement le moins
  documenté ici.
- **`safety_screen.py` (0% couvert)** : le crible VC (liquidité 30k$, score min 70, concentration
  30%, taxe max 15%).
- **Gaps partiels** dans les fichiers déjà couverts : `risk_guard.py` (budgets de risque par
  palier de conviction, seuils de tier), `paper_trader.py` (plafonds `MAX_POSITIONS*` par poche,
  poche satellite, Breakeven Hard Floor), `market_sentiment.py` (seulement 3 des ~10 seuils du
  classifieur de régime).
- **`DAILY_TRADE_FLOOR`** : trouvé à 30 dans le code, alors que CLAUDE.md le décrit encore comme
  "5 trades/jour" — divergence à clarifier avec l'opérateur avant même de l'ajouter au registre.

À compléter (voir Item backlog dédié) — jamais silencieusement présenté comme exhaustif d'ici là.

## Garde-fou mécanique

`test_coherence.py::test_trading_thresholds_match_calibration_doc` vérifie que chaque constante listée
ci-dessus existe bien dans le code avec la valeur documentée ici — un changement de valeur
sans mise à jour de ce fichier casse la CI, plutôt que de dériver silencieusement (même
doctrine que le registre des actions externes, `test_external_write_actions_registered_in_allowlist`).
