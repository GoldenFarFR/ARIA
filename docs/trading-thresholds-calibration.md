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
| `_MAX_TOP10_HOLDER_PCT` | bonding_entry.py:134 | 80% | 24/07 | — |
| `_MIN_HOLDERS_FOR_CONCENTRATION_CHECK` | bonding_entry.py:163 | 50 | Relevé de 15→50 le 28/07 (échantillon 50 tokens + recherche, ratio jugé non-informatif avant ce seuil) | — |
| `_MIN_LIQUIDITY_USD` (bonding) | bonding_entry.py:179 | 10 000 $ | 24/07 | Workflow du 28/07 : Item #163 propose des signaux avant-coureurs de crash post-graduation qui pourraient affiner ce plancher |
| `_WEIGHT_DEV_SECURITY`/`_WEIGHT_PRODUCT_CONVICTION`/`_WEIGHT_TECHNICAL_SETUP`/`_WEIGHT_HOLDER_CONCENTRATION` | bonding_entry.py:207-210 | 35/35/15/15 | 24/07, "valeurs de départ à recalibrer une fois des résultats réels accumulés" (dixit le commentaire d'origine) | Une fois un vrai échantillon de trades bonding clôturés accumulé |
| `_SCORE_THRESHOLD` | bonding_entry.py:240 | 60/100 | 24/07 | Idem |
| `BONDING_SIZE_REDUCTION` | bonding_entry.py:123 | 0.5x | 24/07 | — |
| `_FALLBACK_TARGET_MULTIPLE` | bonding_entry.py:231 | 2.0x | 28/07, Item #152 (fallback target/invalidation pour permettre un achat sans signal technique) | — |
| `_FALLBACK_INVALIDATION_MULTIPLE` | bonding_entry.py:231 | 0.35 (perte max 65%) | 28/07, Item #152 pour le fallback ; élargi comme plancher volet 1 de l'Item #155 (le cas réel HOLO a montré des swings -55%/+122% comme bruit normal, justifiant cette largeur) | Un cas réel montrant que 65% est encore trop serré ou trop large |

## Bonding — stop de perte 3 volets et sortie (Item #154/#155, `paper_trader.py`)

| Constante | Valeur | Source/date | Revisiter si |
|---|---|---|---|
| `BONDING_TP_STAGES` | (1.0, 4.0, 11.5) = 2x/5x/12.5x prix | 28/07, recherche : cas réels 100x-11 900x mais tous -92% à -99,8% depuis le pic | — |
| `BONDING_TP_STAGE_FRACTIONS` | (0.45, 0.25, 0.20), ~10% moonbag jamais vendu | 28/07 | — |
| `BONDING_VELOCITY_DROP_PCT` | 40% | 28/07, Item #155 volet 2 | **Pas encore vérifié empiriquement** — le workflow empirique en cours (28/07) doit confronter ce chiffre à des cas réels |
| `BONDING_VELOCITY_WINDOW_MINUTES` | 30 min | 28/07, Item #155 volet 2 | Idem |
| `BONDING_LIQUIDITY_FLOOR_USD` | 10 000 $ | 28/07, miroir volontaire de `_MIN_LIQUIDITY_USD` (jamais un plancher de sortie sous le plancher d'entrée) | Si `_MIN_LIQUIDITY_USD` bonding change |
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
