# Pivot critère d'entrée pour le test paper-trading 1M$ (#194)

> Décision opérateur explicite, 15/07, gravée dans `CLAUDE.md` (section « Pivot
> critère d'entrée pour le test 1M$ (#194) » + « Multi-chaînes, aucune limite Base »
> + « Philosophie du volume de données »). Ce document décrit l'implémentation ;
> lire `CLAUDE.md` pour le contexte/la décision elle-même.

## 1. Pourquoi

L'opérateur a montré en direct le classement trending DexScreener Base : des
dizaines de tokens réels, liquides, déjà en mouvement (PAMPU/MYRAD/aeon/GITLAWB/
LFI/BASEMATE/CNX/BOTCOIN/SAIRI/KellyClaude/ODAI/OVPP/SUPERGEMMA/TSG/ClawBank etc.).
Le filtre `safety_screen` (score≥70, liquidité≥30k$, holders connus — pensé pour
repérer un « vrai builder caché » pour la poche VC 85%) n'est pas le bon critère
pour un pari technique/momentum sur un token déjà liquide qui bouge — c'est un
métier différent d'un pari de conviction sur un builder précoce.

**Objectif du test : diagnostique, pas d'abord de rentabilité.** L'opérateur veut
pousser ARIA à faire des erreurs ou être surprise, pour comprendre comment elle
trade réellement avant d'affiner. Un pipeline permissif et rapide sert cet
objectif ; sur-filtrer par excès de prudence le dessert.

## 2. Ce qui NE change PAS (portée strictement respectée)

- `safety_screen.py`/`screened_pool.py`/`candidate_ranking.py` : **non modifiés**.
  La poche VC 85% (thèse builders précoces) continue de fonctionner exactement
  comme avant, pour tout capital réel futur.
- `wallet_guard.py` : **non touché**. Aucun chemin de ce pivot n'approche du
  capital réel.
- Bonding (Virtuals pré-graduation) : **différé**, décision opérateur explicite
  (« on verra plus tard »).
- Le garde-fou honeypot verrouillé par `test_coherence.py`
  (`test_honeypot_active_on_pool_screening`, `test_honeypot_active_on_vc_path`)
  reste intact et actif sur son chemin existant — ce pivot ajoute un SECOND
  chemin honeypot (`momentum_entry._check_honeypot`), il n'affaiblit pas le premier.

## 3. Nouveau critère d'entrée (`aria_core/momentum_entry.py`)

Ordre, du plus rapide/bloquant au plus lent/optionnel :

1. **Honeypot GoPlus (SEUL garde-fou dur conservé)** — rejet immédiat et
   **fail-closed** si GoPlus est indisponible ou non couvert sur la chaîne (à
   l'inverse du reste du pipeline, permissif par conception). Coût quasi nul,
   décision opérateur explicite après question directe posée.
2. **Prix + meilleure paire** (DexScreener, `fetch_token_pairs`) — sans paire
   liquide, pas de décision possible (`None`, jamais un signal fabriqué).
3. **R/R positif obligatoire** — golden pocket (retracement 0,618–0,786) +
   divergence haussière RSI, `entry_signals.detect_entry` (déjà construit,
   jamais câblé comme porte d'entrée avant ce chantier). Sans OHLCV exploitable
   (chaîne non couverte par GeckoTerminal) ou sans setup golden-pocket+divergence,
   **HOLD** — jamais un objectif cible/invalidation inventé.
4. **Alignement technique** (EMA12/26, MACD, patterns de bougies bullish —
   `indicators.py`/`candlestick_patterns.py`) : signaux **supplémentaires**, pas
   des portes individuelles bloquantes. Exiger l'accord simultané de tous les
   indicateurs rendrait le pipeline aussi restrictif que ce qu'il remplace.
5. **Décision** :
   - R/R franc (≥ 1,5) **+** au moins 1 signal d'alignement technique → **BUY**
     déterministe, aucun appel LLM (vitesse).
   - R/R positif mais faible (1,0 ≤ R/R < 1,5) → confirmation LLM **légère**
     (`chat_with_context`, ~10 tokens de réponse, pas un `/vc` complet) —
     indisponible/erreur → **HOLD** par défaut, jamais un BUY inventé faute de
     réponse.
   - R/R < 1,0 → **HOLD**, aucun appel LLM (rejet rapide).
6. **Buzz (bonus, jamais bloquant)** : présence dans `token-boosts`/
   `token-profiles` DexScreener récents, déjà utilisée comme SOURCE de
   candidats (§4) plutôt que comme un filtre séparé. `radar_x.py`/
   `market_sentiment.py` ne sont **pas** branchés ici — ce sont des systèmes
   asynchrones à état (écrivent dans `screened_pool`/suivent BTC/ETH), pas des
   fonctions de requête synchrone par contrat. Un futur chantier pourrait les
   adapter ; hors scope de cette itération.

## 4. Sourcing multi-chaînes (`discover_momentum_candidates`)

- **Base** : réutilise `base_crawler.discover_base_tokens` (pools nouveaux +
  tendance GeckoTerminal, aucune sémantique VC — fonction de découverte pure,
  ne passe jamais par `token_absorber`/`safety_screen`).
- **Multi-chaînes** : nouveaux endpoints DexScreener (`services/dexscreener.py`),
  construits sur la spec OpenAPI OFFICIELLE vérifiée (récupérée en direct depuis
  la vraie source, pas devinée —
  `docs/aria-learning-inbox/2026-07-15-dexscreener-openapi-spec-verifiee.yaml`) —
  `token_profiles_latest`/`token_profiles_recent_updates`/`token_boosts_latest`
  d'abord (fraîcheur — signaux qui COMMENCENT à se former), `token_boosts_top` en
  dernier (classement déjà avancé). **Aucune clé API nulle part** sur tout
  DexScreener (confirmé sur la spec officielle) — aucune gestion de clé prévue.
- **Pré-filtre de liquidité PAR LOT** (`_batch_liquidity_prefilter`, appliqué en
  fin de sourcing) : `/tokens/v1/{chainId}/{tokenAddresses}` accepte jusqu'à 30
  adresses séparées par des virgules en UN SEUL appel (300 req/min) — bien plus
  efficace que d'évaluer chaque candidat en entier (honeypot + OHLCV + TA) avant
  de découvrir qu'il n'a même pas de liquidité exploitable. Groupé par chaîne,
  corrèle chaque paire renvoyée à son contrat via le nouveau champ
  `PairSnapshot.base_address` (absent avant ce correctif — le batch renvoie des
  PAIRES, pas indexées par adresse token demandée). Un candidat absent de la
  réponse batch (chaîne mal couverte, appel en échec) est **conservé tel quel** —
  ce pré-filtre ne rejette jamais par excès de prudence, seul un résultat
  POSITIVEMENT défavorable (liquidité connue et sous le plancher) élimine.
- **Chaînes acceptées** (`DEFAULT_CHAINS = ("base", "solana", "robinhood")`) —
  **volontairement limitées aux chaînes VÉRIFIÉES** ce soir (GoPlus + DexScreener
  répondent HTTP 200) : accepter n'importe quelle chaîne renvoyée par DexScreener
  casserait le seul garde-fou dur sur toute chaîne que GoPlus ne couvre pas.
  Étendre cette liste seulement après vérification GoPlus réelle (même doctrine
  que ce soir : curl direct avant d'accepter, jamais supposé).
- **Endpoints délibérément non utilisés** (confirmés non pertinents pour #194) :
  `community-takeovers/*`, `ads/*`, `orders/v1/*` — pas de signal momentum/sécurité
  directement exploitable pour ce pipeline. `metas/trending/v1`+
  `metas/meta/v1/{slug}` sont implémentés dans `dexscreener.py`
  (`metas_trending`/`meta_by_slug`, narratifs tendance type « AI ») mais **pas
  encore branchés** dans `discover_momentum_candidates` — un narratif est un
  signal de CONTEXTE (plusieurs tokens à la fois), pas un contrat individuel ;
  les intégrer demanderait une décision de conception (comment pondérer un
  narratif chaud vs un token individuel) hors scope de ce correctif.

## 5. Généralisation `paper_trader.py`

- Nouvelle colonne `chain` (`paper_position`, défaut `'base'`) — chaque position
  se souvient de sa chaîne d'origine pour son suivi ultérieur.
- `open_position(..., chain: str = "base")`.
- `_default_price_lookup(contract, *, chain="base")` — généralisé, utilise
  désormais DexScreener directement (`services/dexscreener.fetch_token_pairs`,
  déjà multi-chaînes) plutôt que `scan_base_token` (spécifique Base, et plus
  lourd : honeypot+TA+mint-authority complets pour un simple prix de suivi).
- **Choix de conception : le contrat d'appel `price_lookup(contract)`/
  `analyzer(contract)`/`candidates: list[str]` reste STRICTEMENT inchangé** pour
  tout appelant qui injecte le sien (tous les tests existants passent sans
  modification). La prise en compte de la chaîne se fait par une table de
  correspondance contrat→chaîne construite au sourcing/à l'ouverture, consultée
  uniquement quand `run_paper_cycle` utilise SES PROPRES défauts internes
  (`price_lookup is _default_price_lookup`). Plus sûr qu'un changement de
  signature global : zéro régression sur l'existant, testé.
- **Pivot du défaut** (`run_paper_cycle`) : quand **ni `candidates` ni
  `analyzer`** ne sont fournis par l'appelant (le cas réel du heartbeat,
  `run_paper_cycle(notifier=...)`), le défaut devient le pipeline momentum
  (`discover_momentum_candidates` + `evaluate_momentum_entry`) au lieu de
  `candidate_ranking.top_candidates()`/`_default_analyzer` (VC-thesis). Tout
  appelant qui fournit SES PROPRES `candidates` OU `analyzer` garde le
  comportement historique — le pivot est scopé au vrai chemin par défaut
  uniquement, réversible en un point d'insertion unique.

## 6. Limite connue (non corrigée dans cette itération)

`risk_guard.evaluate_portfolio_risk` (coupe-circuit de drawdown, #186) appelle
`portfolio_summary(price_lookup=price_lookup)`, qui appelle `price_lookup(contract)`
**sans** le kwarg `chain` (ce call site n'a pas été touché — hors scope de ce
chantier, territoire #186). Pour une position ouverte sur une chaîne non-Base, le
prix de marquage utilisé par le calcul d'équity globale du coupe-circuit dégrade
donc vers `cost_usd` (position non-repriced) plutôt que le prix réel de sa chaîne —
dégradation SÛRE (pas de crash, pas de prix inventé) mais moins précise. Généraliser
`portfolio_summary`/le coupe-circuit à leur tour est un suivi possible, pas fait ici.

## 7. Vérification

- `test_coherence.py` (garde-fou CI honeypot + registre des écritures externes) :
  vert, non touché par ce chantier.
- Tests dédiés : `test_dexscreener_client.py` (nouveaux endpoints),
  `test_momentum_entry.py` (sourcing, honeypot fail-closed, R/R obligatoire,
  alignement technique, confirmation LLM), `test_paper_trader.py` (colonne
  `chain`, price_lookup généralisé, pivot du défaut, non-régression sur l'appel
  explicite).
