# HANDOFF — Pipeline momentum (sourcing, garde-fous, sizing, sortie)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Pour le processus complet à jour : section "Processus d'achat momentum — réponse de
> référence" dans CLAUDE.md (toujours à revérifier contre le code avant de la citer).

[DEPLOYE] Sujet    : owner_change_balance jamais revérifié pendant la détention + JitoSOL non exclu du sourcing
Date : 2026.07.24 / Probleme : audit 5-agents -- (1) le véto owner_change_balance (GoPlus, ajouté 22/07 à l'entrée après l'incident CNX) n'était jamais recomparé pendant la détention (paper_trader_risk.rescan_open_position lisait honeypot/cannot_sell/hidden_owner/can_take_back_ownership mais jamais owner_change_balance, meme famille de trou deja corrigee une fois le 17/07 pour le ratio volume/liquidite) ; (2) reference_tokens_excluded() n'excluait aucun token de liquid-staking (LST) -- une vraie position papier ouverte sur JitoSOL (bridge), profil blue-chip hors thèse momentum, confirmait le trou déjà documenté comme "hors scope" dans smart_money.py.
Solution : EntrySecuritySnapshot + rescan_open_position comparent désormais owner_change_balance comme les 4 autres signaux (paper_trader_risk.py) ; nouveau registre _LST_ADDRESSES_BY_CHAIN (smart_money.py, JitoSOL Base confirmé) exposé via reference_tokens_excluded() (momentum_entry.py) -- tests dédiés miroirs des cas honeypot/WETH déjà couverts (cf. historique git 24/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : EURC (stablecoin EUR) acheté comme position momentum via le mode plancher
Date : 2026.07.24 / Probleme : vérification de routine des transactions (demande opérateur) -- une position papier réelle ouverte sur EURC (Circle, stablecoin EUR, contrat confirmé via DexScreener/prix cohérent EUR/USD), R/R 1.1 en mode plancher. _STABLECOIN_ADDRESSES_BY_CHAIN ne couvrait que USDC/USDbC/DAI/USDT sur Base -- la propre diligence de conviction d'ARIA avait identifié EURC comme "stablecoin officiel Circle... réserves euro 1:1" dans la thèse elle-même, sans que ça bloque l'achat (même famille de trou que JitoSOL ci-dessus, trouvé le même jour).
Solution : EURC ajouté à _STABLECOIN_ADDRESSES_BY_CHAIN["base"] (smart_money.py) -- exclu via reference_tokens_excluded() comme les autres stablecoins. Position déjà ouverte laissée en cours (gérée normalement par son stop/target, pas de risque de sécurité identifié) -- cf. historique git 24/07. Point ouvert signalé séparément : position soeur msETH (id=6), potentiel fondamental noté 1.0/10 par la diligence d'ARIA elle-même ("contrats multiples incohérents") -- identité exacte du token pas encore confirmée, pas ajouté à un registre sans certitude.

------------------------------------------------------------

[CODE] Sujet    : Cooldown adaptatif WebSocket (4h) + fix WETH scanné en boucle (repli x402 gaspillé)
Date : 2026.07.22  /  Probleme : décision opérateur ("un contrat n'a pas besoin d'être scanné toutes les 60 secondes, toutes les 4h suffit") + bug réel trouvé via le journal x402_spend_log — WETH (predeploy Base, jamais un candidat spéculatif) découvert et évalué en boucle toutes les 10-20 min depuis minuit, déclenchant un repli x402 payant (0,002$/appel) sur holder_concentration à chaque fois (Blockscout gratuit échoue systématiquement sur ce contrat précis).
Solution : (1) DEDUP_TTL_SECONDS (15min, anti-spam de frame) inchangé, nouveau RESCAN_COOLDOWN_SECONDS (4h) — un candidat déjà vu ne redéclenche pas d'évaluation SAUF si son prix a bougé de plus de 10% (RESCAN_PRICE_MOVE_THRESHOLD_PCT) depuis le dernier passage, comparé sans appel réseau dédié (prix déjà en main via _batch_liquidity_prefilter, étendu pour le remonter) ; (2) reference_tokens_excluded() (momentum_entry.py) réutilise les registres déjà vérifiés de smart_money.py (stablecoins Base + wrapped natives) pour exclure WETH/USDC/etc. des DEUX points d'entrée de découverte (discover_momentum_candidates ET momentum_websocket._ingest_frame, qui a son propre chemin d'ajout de candidat) — momentum_entry.py, momentum_websocket.py (cf. historique git 22/07)

------------------------------------------------------------

[CODE] Sujet    : Crible unifié VC/Swing (tâche #1) -- fusion complète, DORMANT, pas encore branché
Date : 2026.07.22  /  Probleme : le pivot #194 (15/07) avait remplacé le filtre VC-thesis par un critère purement technique pour le test 1M$ -- décision opérateur du 22/07 : construire un jugement unique qui évalue conviction fondamentale (poche VC, 6 mois-2 ans, x20-x100) ET setup technique (poche Swing) sur le MÊME candidat, cumulables sur un même contrat (option "fusion complète" choisie explicitement après comparaison avec une alternative plus simple à deux jugements séparés).
Solution : nouveau module unified_entry.py (evaluate_unified_entry) -- garde-fous durs extraits de momentum_entry dans une nouvelle fonction publique evaluate_hard_gates (honeypot inclus, zéro régression vérifiée sur 205+328 tests, evaluate_momentum_entry inchangé et toujours actif), contexte riche via scan_base_token (TA + smart money qualité-prioritaire + fondamentaux, include_honeypot=False pour ne jamais refaire l'appel GoPlus), nouveau prompt LLM qui juge horizon vc/swing/les_deux/aucun, sizing swing resté déterministe (risk_guard, LLM confirme seulement). paper_trader.has_open()/_get_open() acceptent un paramètre optionnel strategy (rétrocompatible) -- prérequis pour le cumul. **DORMANT** : run_paper_cycle utilise toujours momentum_entry.evaluate_momentum_entry via _default_momentum_analyzer, comportement de production inchangé pour l'instant. Reste à faire avant activation : sizing par poche + plafond de concentration partagé (cumul), boucle _run_paper_cycle_locked adaptée pour consommer 0-2 signaux par candidat, badge VC/Swing dans les 4 points d'affichage (alertes achat/vente + /feedback+/ledger + diagnostic API), tests d'intégration bout-en-bout, stress-test multi-agents (reporté à une session dédiée) — unified_entry.py, momentum_entry.py, paper_trader.py (commit cc8de148).

------------------------------------------------------------

[DEPLOYE] Sujet    : Incident réel BRIAN — triple-achat, -18 561$
Date : 2026.07.17  /  Probleme : rien n'empêchait de racheter un contrat dont on venait de se faire sortir en perte — BRIAN racheté 3x en ~2h30, 92% des pertes réalisées sur ce seul contrat
Solution : liste noire persistée (amorcée avec BRIAN) + plafond ratio volume24h/liquidité (20x, anti-wash-trading — BRIAN avait un ratio ~91x) — momentum_blacklist.py (d45b6c9e)

------------------------------------------------------------

[DEPLOYE] Sujet    : Cycle heartbeat de découverte découplé de la surveillance de position
Date : 2026.07.22  /  Probleme : décision opérateur explicite ("un contrat n'a pas besoin d'être scanné toutes les 60 secondes, toutes les 4h suffit") — le cycle heartbeat unique (15min) mélangeait recherche de nouveaux candidats ET surveillance des positions déjà ouvertes (stop suiveur, re-scan sécurité), rendant impossible de ralentir l'un sans l'autre.
Solution : nouveau paramètre `skip_new_entries` sur `run_paper_cycle` (symétrique de `skip_position_management` déjà existant côté WebSocket) — `paper_trade_cycle` (15min, inchangé) ne gère plus QUE les positions ouvertes ; nouveau cycle `momentum_discovery_cycle` (60min, même gate `ARIA_PAPER_TRADING_ENABLED`) ne cherche plus QUE de nouveaux candidats. Vérifié avant de choisir 1h : sur les 6 sources de découverte (`base_crawler`/Birdeye/4 flux DexScreener boosts+profiles), les 4 flux DexScreener sont déjà redondants avec le WebSocket temps réel (#196, push continu) — seuls `base_crawler`/Birdeye n'ont aucun équivalent temps réel, d'où le choix de ralentir plutôt que supprimer ce cycle — paper_trader.py, heartbeat.py (cf. historique git 22/07)

[DEPLOYE] Sujet    : 2e cas TSG, pas capté par les garde-fous ci-dessus
Date : 2026.07.17  /  Probleme : +533%/24h puis -48,6%/6h puis +56,6%/1h, ratio wash-trading sous le seuil
Solution : plafond _MAX_PRICE_CHANGE_24H_PCT = 200% — rejette un token déjà monté de plus de 200%/24h, jamais sur un mouvement négatif (retracements achetés délibérément) — momentum_entry.py (cf. historique git 17/07)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Doctrine Solana
Date : 2026.07.17  /  Probleme : —
Solution : reste au même standard de sécurité que Base, jamais assoupli (décision opérateur explicite) — cf. docs/HANDOFF_GOPLUS.md

------------------------------------------------------------

[DEPLOYE] Sujet    : Garde-fou de re-entrée assoupli
Date : 2026.07.19  /  Probleme : "achat unique" bloquait aussi la re-entrée sur un contrat déjà clôturé (gain ou perte), trop strict selon l'opérateur
Solution : ne s'applique plus qu'aux positions EN COURS — un contrat clôturé redevient un candidat normal dès qu'un signal BUY se profile. Le pattern BRIAN reste couvert par la liste noire + le ratio volume/liquidité, indépendants de ce gate — momentum_entry.py, paper_trader.py (cf. historique git 19/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Plancher de liquidité — 5k$ jamais un vrai rejet dur
Date : 2026.07.19  /  Probleme : le plancher n'était qu'une préférence de découverte, aucun rejet dur n'existait réellement dans evaluate_momentum_entry
Solution : rejet dur systématique ajouté (100k$ anti-scam, décision opérateur), positionné avant le ratio wash-trading — momentum_entry.py (63a0d825)

------------------------------------------------------------

[DEPLOYE] Sujet    : Plancher de liquidité rebaissé
Date : 2026.07.21  /  Probleme : 100k$ trop restrictif, funnel de rejet dominé par insufficient_liquidity
Solution : rebaissé à 50k$ — momentum_entry.py (8812d86f)

------------------------------------------------------------

[DEPLOYE] Sujet    : Âge minimum de paire + profil projet payé
Date : 2026.07.20  /  Probleme : Fibonacci/RSI sur quelques heures = pattern matching sur du bruit, pas assez d'historique
Solution : gates durs ajoutés — âge minimum 14j, profil DexScreener Enhanced Token Info ou listing CoinGecko exigé — momentum_entry.py (f0df8ff9)

------------------------------------------------------------

[DEPLOYE] Sujet    : Conflation volume/liquidité dans les thèses /vc
Date : 2026.07.20  /  Probleme : le LLM confondait les deux métriques dans sa rédaction (ex. "volume 24h signale une liquidité insuffisante")
Solution : règle explicite ajoutée au prompt système — jamais présenter l'une comme preuve de l'autre — vc_analysis.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Marathon de revue croisée (7 rounds, 20-21/07)
Date : 2026.07.20→21  /  Probleme : trop dense pour une ligne par correctif
Solution : TP1 reconnecté au R/R réel, anti-mèche du stop suiveur, sizing par palier de conviction, Regime Switch macro (Peur/Neutre/Euphorie), Breakeven Hard Floor, sizing hybride risque/ATR — cf. historique git, commits 24b4243c→d54c3513a235

------------------------------------------------------------

[DEPLOYE] Sujet    : Réordonnancement — honeypot GoPlus en dernier des gates gratuits
Date : 2026.07.21  /  Probleme : GoPlus (ressource rare) tournait en 2e position, avant les filtres gratuits
Solution : déplacé juste avant l'OHLCV, jamais dépensé sur un candidat déjà rejeté gratuitement — momentum_entry.py (cf. historique git 21/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Diagnostic 0 trade pendant ~51h
Date : 2026.07.22  /  Probleme : funnel de rejet consulté — GoPlus cassé (cf. docs/HANDOFF_GOPLUS.md) dominant sur la fin de la fenêtre, mais volume_too_low/pair_too_young dominaient déjà avant la panne GoPlus
Solution : confirmé via /funnel comme bon outil de diagnostic — pas de nouveau correctif, juste un diagnostic

------------------------------------------------------------

[CODE] Sujet    : Retry différé des candidats `pending` + plafond anti-boucle-infinie (ancien pipeline VC-thesis pré-#194)
Date : 2026.07.11  /  Probleme : un candidat en échec mou (`pending`, fraîcheur du token plutôt qu'un signal malveillant confirmé) n'était jamais délibérément rescanné — seule une redécouverte fortuite déclenchait un nouveau passage ; et sans plafond, un candidat qui n'atteint jamais `active` serait retenté toutes les 24h pour toujours.
Solution : `screened_pool.list_stale_pending()` + `base_crawler.retry_stale_pending()` (rescan délibéré après 24h, réutilise le même `token_absorber.absorb()`) ; colonne `retry_count` + `abandon_stale_pending()` bascule en `rejected` définitif au-delà de 5 tentatives OU 7 jours — screened_pool.py / base_crawler.py (#105/#108, cf. historique git 11/07). Étendu au pipeline bonding le même mois (#107, `bonding_absorber.retry_stale_bonding_pending()`).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Désaccord de source de liquidité entre découverte et scan (ancien pipeline VC-thesis pré-#194)
Date : 2026.07.12  /  Probleme : `discover_top_pools()` filtrait sur la liquidité GeckoTerminal (`reserve_in_usd`), mais le scan réel (`acp_onchain_scan.scan_base_token`) source sa liquidité via DexScreener — deux fournisseurs pas garantis d'accord, plusieurs candidats passaient le seuil de découverte puis échouaient à 0$ de liquidité au scan.
Solution : Colonne `source` ajoutée à `screened_token` pour distinguer l'origine d'un candidat (observabilité) ; plancher de liquidité de découverte relevé avec marge — base_crawler.py (cf. historique git 12/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Sizing risque/ATR plafonné au mauvais palier de conviction
Date : 2026.07.20 / Probleme : size_by_risk_budget() recevait toujours MAX_ALLOC_MULTIPLIER (5%) comme plafond, quel que soit le palier reel (fort/modere/faible) - un signal modere ou faible pouvait recevoir la meme mise qu'un signal fort des que le stop ATR etait assez serre.
Solution : conviction_mult (deja calcule pour le repli sans ATR) sert desormais de plafond pour les deux chemins de sizing - risk_guard.py (commit 3555309adeb6)

------------------------------------------------------------

[DEPLOYE] Sujet    : Plafond de concentration par chaine devenu un plafond global
Date : 2026.07.20 / Probleme : CONCENTRATION_CAP_PCT=40% categorise les positions momentum par momentum-{chain} ; une fois DEFAULT_CHAINS resserre a Base seule, tout retombait dans un seul seau - le plafond de diversification devenait de facto un plafond global du portefeuille de trading (400 000$).
Solution : categorie vide (neutralise le garde if not category) tant qu'une seule chaine est active dans DEFAULT_CHAINS - se reactive automatiquement des qu'une 2e chaine rejoint le sourcing - momentum_entry.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : WebSocket momentum n'envoyait aucune notification Telegram a l'achat
Date : 2026.07.20 / Probleme : momentum_websocket.py::_drain_once appelait run_paper_cycle() sans jamais transmettre le notifier (methode liee a l'instance Heartbeat, inaccessible depuis ce chemin) - tout achat via le WebSocket restait muet, seule la vente (geree par le heartbeat) notifiait.
Solution : _notify_telegram_trading extrait en fonction libre telegram_bot.send_trading_notification (reutilisable sans instance), passee comme notifier par les deux chemins - heartbeat.py / momentum_websocket.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Gates age minimum de paire + profil projet paye
Date : 2026.07.20 / Probleme : Fibonacci/RSI calcules sur une paire trop jeune (quelques heures) = pattern-matching sur du bruit ; aucun filtre sur la legitimite declaree du projet (site/reseaux).
Solution : rejet dur si paire < 14 jours (pair_created_at DexScreener) ou si aucun profil paye DexScreener Enhanced Token Info / listing CoinGecko trouve - momentum_entry.py (cf. historique git 20/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Incident BRIAN — triple-achat wash-trading (-18 561$ papier), 4 garde-fous durs
Date : 2026.07.17  /  Probleme : Un token (BRIAN) a ete achete 3 fois d'affilee par le pipeline momentum sur un pattern de wash-trading/re-entree non detecte a l'epoque, perte reelle -18 561$ (capital papier).
Solution : 4 garde-fous durs ajoutes — liste noire persistee (momentum_blacklist.py), plafond ratio volume24h/liquidite (20x, anti wash-trading), garde-fou de re-entree sur un contrat deja cloture, plafond de variation de prix 24h (_MAX_PRICE_CHANGE_24H_PCT, 200%) — cf. historique git 17/07

------------------------------------------------------------

[DEPLOYE] Sujet    : Authentification GeckoTerminal — chiffre de débit non vérifié a causé 79% de HTTP 429
Date : 2026.07.19  /  Probleme : le correctif #211 (18/07) affirmait un palier de 100 req/min avec clé Demo sur /onchain, jamais vérifié en conditions réelles soutenues (confondu avec un autre palier CoinGecko sans rapport) — le vrai palier est ~30 req/min. Le throttle accéléré à 0.65s/appel a produit 666 échecs 429 contre 176 succès en ~2h, cause probable du silence total du Cycle #2 paper-trading.
Solution : throttle authentifié réaligné sur le rythme non-authentifié déjà éprouvé (recalibré depuis par l'audit multi-services du 21/07 — toujours vérifier la valeur courante avant de citer un chiffre) — geckoterminal.py (commit f62a6fa8). Leçon actée : un chiffre de débit "vérifié via la doc officielle" doit être confirmé en conditions RÉELLES SOUTENUES, pas par un simple test curl ponctuel.

------------------------------------------------------------

[DEPLOYE] Sujet    : Divergence RSI haussière non détectée au-delà des deux derniers pivots
Date : 2026.07.19  /  Probleme : bullish_rsi_divergence() (entry_signals.py) ne comparait que les deux DERNIERS pivots bas de la fenêtre — une divergence formée entre le pivot le plus récent et un pivot plus ancien de la même fenêtre restait invisible. Diagnostiqué sur 8 candidats réels : 0/8 divergences détectées avant le fix.
Solution : compare désormais le dernier pivot à CHAQUE pivot antérieur (strict sur-ensemble de l'ancien comportement, même définition du signal) — affecte tous les appelants (momentum_entry.py, acp_onchain_scan.py/vc, market_sentiment.py, arena_signal.py) — entry_signals.py (cf. historique git 19/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : R/R affiché calculé sur une bougie OHLCV périmée, pas sur le prix d'exécution réel
Date : 2026.07.19  /  Probleme : detect_entry() calculait le R/R depuis le close de la dernière bougie OHLCV, alors que le prix réellement exécuté vient d'une source indépendante (DexScreener temps réel) — divergence de plusieurs % observée sur un trade réel (GITLAWB, R/R affiché 149.1 au lieu de ~25.5).
Solution : detect_entry() accepte un execution_price optionnel qui remplace le close comme référence R/R (jamais invalidation/target, qui restent des niveaux Fibonacci fixes) ; momentum_entry.py transmet le prix DexScreener réel. /vc (analyse rétrospective, pas d'exécution imminente) non affecté — entry_signals.py/momentum_entry.py (commit 9062f0a7).

------------------------------------------------------------

[DEPLOYE] Sujet    : Mislabeling quote-token — résolution de paire sans filtrer sur l'adresse de base (incident PLAZM/ESHARE)
Date : 2026.07.19  /  Probleme : la sélection de la paire la plus liquide parmi celles renvoyées par fetch_token_pairs(contrat) ne filtrait pas sur base_address — un contrat pouvait être sélectionné comme simple QUOTE du pool d'un AUTRE token de base plus liquide, faisant lire/exécuter sur le mauvais actif (une position PLAZM a en réalité tradé sur ESHARE, produisant un gain fictif de ~11,5M$ dans le paper-trading).
Solution : filtre base_address ajouté partout où ce pattern (max(pairs, key=lambda p: p.liquidity_usd) sans filtre) existait — trouvé et corrigé à 3 endroits : momentum_entry.py/paper_trader.py, acp_onchain_scan.scan_base_token (pipeline /vc), vanguard/backend dexscreener.resolve_token_to_best_pair (vitrine) — commit a122b522. Réflexe réutilisable : grep exhaustif du même pattern dès qu'un bug de ce type est trouvé une fois.

------------------------------------------------------------

[DEPLOYE] Sujet    : Coupe-circuit adaptatif par fournisseur dans la cascade OHLCV
Date : 2026.07.19  /  Probleme : _fetch_candles retentait toujours GeckoTerminal en premier même en pleine rafale de 429, gaspillant la latence du throttle partagé (2.1s/appel à l'époque) sur un appel voué à l'échec avant de retomber sur l'étage suivant.
Solution : état process-local (_provider_fail_counts/_provider_cooldown_until, jamais persisté) appliqué aux 4 étages réseau (GeckoTerminal/CoinMarketCap/Mobula/Dune) — 3 échecs consécutifs (uniquement available=False/exception réseau, jamais un available=True, candles=[] légitime) déclenchent une pause de 180s avant repli direct sur l'étage suivant — momentum_entry.py (commit 63bbd7e7, #95).

------------------------------------------------------------

[CODE] Sujet    : Progression de graduation Virtuals résolue on-chain (BONDING_V5)
Date : 2026.07.09→11  /  Probleme : graduation_progress() retournait toujours None — aucun champ du payload API Virtuals (virtualRaised etc.) ne correspondait au pourcentage réel affiché par l'UI ; de plus, un token encore en bonding (tokenAddress null, adresse réelle dans le champ preToken) n'était pas détecté du tout par fetch_by_address (ne cherchait que tokenAddress).
Solution : fetch_by_address gagne un repli preToken (build_token_by_pretoken_url) ; vrai contrat Bonding trouvé par balayage direct des logs on-chain (eth_getLogs sur Graduated, par blocs de 10000) : 0x1A540088125d00dD3990f9dA45CA0859af4d3B01 (BondingV5, proxy EIP-1967 vérifié Blockscout) — seuil de graduation confirmé PAR TOKEN (tokenGradThreshold(address)), pas une constante globale, validé empiriquement sur un token gradué réel. Implémenté en lecture seule (aucune clé), repli propre à None si le token utilise une autre instance du contrat — services/base_onchain.py, gate ARIA_ONCHAIN_GRADUATION_ENABLED (OFF par défaut), 15 tests (cf. historique git 11/07)

------------------------------------------------------------

[CODE] Sujet    : `owner_change_balance` (GoPlus) jamais consulté par `_check_honeypot` — vecteur de perte totale non couvert
Date : 2026.07.22  /  Probleme : trouvé en observant une position momentum RÉELLEMENT ouverte en prod (CNX, R/R 4.5 à l'entrée) — le contrat avait GoPlus `owner_change_balance=True` (l'owner peut modifier directement le solde de n'importe quel wallet, y compris celui d'ARIA) sans que `_check_honeypot` (le SEUL garde-fou dur du pipeline momentum, décision opérateur du 15/07 : rien d'autre du filtre VC-thesis ne s'applique) ne le détecte — seuls `is_honeypot`/`cannot_sell_all` étaient vérifiés. Ce pouvoir est fonctionnellement DISTINCT du honeypot classique (qui bloque la revente) : il permet un vol/effacement direct de solde, un vecteur de perte totale que ni `is_honeypot` ni `cannot_sell_all` ne capturent.
Solution : `security.owner_change_balance` ajouté comme 3e signal dur dans `_check_honeypot` — rejoint le garde-fou existant plutôt que d'élargir la doctrine "seul le honeypot" (mint_authority/dev_wallet restent hors scope momentum, inchangé) : ce signal est de MÊME NATURE que le honeypot (pouvoir technique de vol direct des fonds, pas un signal de conviction/thèse), et coûte ZÉRO appel réseau supplémentaire (même lecture GoPlus déjà faite pour le honeypot). Code machine-readable `honeypot_rejected` réutilisé (cohérent avec le mandat #192). Pendant côté crible VC (`safety_screen.py`/`acp_onchain_scan.py`) dans `docs/HANDOFF_MOTEUR_LEGITIMITE.md`. `momentum_entry.py` — 1 nouveau test dédié, suite complète 6801 passed / 17 skipped, `test_coherence.py` vert (non commité au moment de cette entrée).

------------------------------------------------------------

[CODE] Subject  : Limit-order mechanism — a drifted-price setup no longer silently rejected
Date : 2026.07.23 / Problem : real position observed in prod (CHECK) revealed the exact gap: between signal detection and execution, honeypot/OHLCV/LLM analysis takes long enough for the price to drift upward (0.038 signal -> 0.044 execution), degrading R/R from 3.9 to 1.52 -- `_execution_rr_still_valid` failing here was a PLAIN reject (`funnel["price_stale_at_execution"]`), discarding a setup that only got MORE EXPENSIVE, not a dead one.
Solution : new `limit_orders.py` -- at that same reject point, `should_place_limit_order` distinguishes case (a) structure already broken (fresh price through the invalidation, or the one-time re-analysis at the pending->watching transition fails) -> reject outright as before, from case (b) drifted upward but structure intact -> a limit order is placed at the ORIGINAL signal price, watched by `momentum_websocket._drain_once()` (restructured so the check runs even with an empty `_pending`, never starving the watch on a quiet day) until it triggers, the invalidation breaks, or it expires silently (`LIMIT_ORDER_EXPIRY_HOURS=3`, never a Telegram alert). Sizing extracted into a new public `paper_trader.compute_entry_alloc()` (same formula/thresholds as before extraction), recomputed with FRESH portfolio context at trigger time -- real bug found and fixed during testing: `_execute_trigger` originally re-applied `risk_guard.cap_alloc_to_price_impact`/`simulated_fill_price` on top of what `open_position` already does internally, silently collapsing the allocation to zero (double price-impact degradation on an already-degraded price). `momentum_entry.py`/`paper_trader.py`/`momentum_websocket.py`/`limit_orders.py` (new) -- 3 new dedicated test files (66 tests), full suite green, `test_coherence.py` green (not yet committed at the time of this entry).

------------------------------------------------------------

[CODE] Subject  : Liquidity-rotation signal -- measure capital flow on low-info tokens (observational only)
Date : 2026.07.23 / Problem : operator's own trading blind spot, described directly: "sur ces small-caps sans info, l'edge n'est pas les fondamentaux -- c'est de sentir si l'argent tourne vers ce token maintenant" -- triggered by the real AUTONOMOPOLY position (potential_score 0.0/10, zero team/audit/roadmap, yet the pipeline still bought it as a pure momentum bet). A low-info token has nothing to judge on fundamentals by design, but every buy/sell and every dollar of volume is fully transparent on-chain.
Solution : new `skills/liquidity_rotation.py` (pure, DB-free, zero extra network call) -- ``compute_liquidity_rotation`` blends two independent measurements into a 0-10 score: (1) buy-pressure ACCELERATION (h1 buys/(buys+sells) vs the 24h average -- a token net-selling most of the day but net-BOUGHT this hour is rotation happening right now, invisible in the 24h aggregate alone) and (2) volume ACCELERATION (h1 volume run-rated to 24h, divided by the real 24h volume -- a fresh spike vs stale activity spread evenly). Each half capped at 5 points so neither dominates; missing data on one side halves the achievable score rather than fabricating a neutral value. `services/dexscreener.PairSnapshot` gained the h1/h6 volume+txns fields it was silently discarding (DexScreener already returns them in the SAME response as the h24 fields already parsed). Wired as a DELIBERATELY OBSERVATIONAL field on the momentum signal dict (`liquidity_rotation_score`/`_accelerating`/`_volume_ratio`) -- computed on every BUY, persisted on the position (new tracking columns, same hot-migration pattern as the 07/23 performance-breakdown chantier), segmented by `/performance` (`key_liquidity_rotation`). NEVER yet used to gate or size a position -- same "measure before I act" doctrine as the whole /performance chantier: once enough real trades accumulate, a real correlation to winrate/PnL can be measured before this is ever wired into the decision. `services/dexscreener.py`/`momentum_entry.py`/`paper_trader.py`/`limit_orders.py`/`performance_breakdown.py`/`skills/liquidity_rotation.py` (new) -- 13 new dedicated tests (`test_liquidity_rotation.py`) + 2 momentum_entry tests + 1 performance_breakdown test, full suite to confirm, `test_coherence.py` green (not yet committed at the time of this entry).
