# HANDOFF — Wallet-scoring / smart-money (/walletscore, classement)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Le 15/07 a été un marathon de revue croisée (Gemini/ChatGPT/DeepSeek, 4 IA, 6 rounds, 22+
> correctifs) — résumé par grand thème ici, pas un correctif par ligne. Détail exact :
> historique git, commits du 15/07 préfixés #157 à #178.

[CODE] Sujet    : File /walletqueue -- rattrapage affamé par un lot massif de wallets jamais touchés (item #61)
Date : 2026.07.24 / Probleme : audit réel de la file (252 wallets, dont 246 injectés d'un coup le 23/07 via le sourcing CabalSpy) : le meilleur wallet en cours (91,6% de couverture) sortait APRÈS des centaines de wallets jamais scannés, purement parce que `list_pending` triait le groupe rattrapage en FIFO strict sur `next_check_at` -- ce champ repasse à "maintenant" à chaque tentative, quelle que soit la progression réelle, donc un lot ajouté en masse peut structurellement noyer les wallets déjà bien avancés.
Solution : `list_pending` trie désormais le groupe rattrapage par `last_notified_milestone` DESCENDANT en premier (proxy de couverture déjà stocké dans la même table, zéro coût réseau/jointure supplémentaire), `next_check_at` restant le départage FIFO à égalité de milestone -- le groupe surveillance (déjà 100% couverts) garde son FIFO strict inchangé (vérifié par test dédié). `MAX_WALLETS_PER_CYCLE` volontairement PAS augmenté (confirmé par le diagnostic : le vrai goulot du pire cas vient du throttle GeckoTerminal, pas de ce tri) -- gate `ARIA_CABALSPY_SOURCING_ENABLED` confirmé actif en prod (`docker exec`, valeur non affichée). `services/wallet_scan_queue.py` -- 2 nouveaux tests dédiés (`test_wallet_scan_queue.py`), suite complète en cours de vérification au moment de cette entrée, `test_coherence.py` vert (non commité au moment de cette entrée).

------------------------------------------------------------

[DEPLOYE] Sujet    : Évaluateur "smart wallet" maison livré
Date : 2026.07.14  /  Probleme : —
Solution : score_wallets + commande /walletscore — smart_money.py (#157)

------------------------------------------------------------

[DEPLOYE] Sujet    : Plafond de tokens/passage rendait la couverture impraticable
Date : 2026.07.15  /  Probleme : un wallet très actif ne pouvait jamais être couvert entièrement en un seul appel
Solution : scan incrémental persistant — chaque appel traite le prochain lot jamais vu, jusqu'à couverture complète — wallet_scan_state.py

------------------------------------------------------------

[DEPLOYE] Sujet    : Marathon revue croisée — 22+ correctifs anti-manipulation
Date : 2026.07.15  /  Probleme : trop dense pour une ligne par correctif
Solution : trim anti-chance en pourcentage (pas un compte fixe qui se dilue) ; exclusion wrap/unwrap ETH↔WETH et swaps stable↔stable du comptage de fiabilité ; plancher de liquidité confirmée avant de faire confiance à un prix OHLCV (asymétrique, gate l'achat jamais la vente) ; ratio de confiance affiché jamais caché ; percentile qui exclut couverture partielle/confiance basse — smart_money.py, commits #157→#178

------------------------------------------------------------

[CODE] Sujet    : Limites structurelles documentées, pas corrigées
Date : 2026.07.15  /  Probleme : coordination Sybil au-delà de la convergence pairwise (LE plus important), absence de benchmark alpha vs bêta, mark-to-market des positions ouvertes
Solution : documentées honnêtement dans le code, chantiers séparés si repris un jour — smart_money.py

------------------------------------------------------------

[DEPLOYE] Sujet    : Équation réduite → 2 vrais bugs trouvés
Date : 2026.07.15  /  Probleme : demande opérateur de réduire la formule à une équation a fait resurgir : percentile qui ne créditait pas les ex-æquo, contradiction de signe Sortino/PnL réel
Solution : les deux corrigés + nouveau drapeau sortino_pnl_contradiction — smart_money.py (0b049ad)

------------------------------------------------------------

[DEPLOYE] Sujet    : File d'attente en arrière-plan (/walletqueue)
Date : 2026.07.15  /  Probleme : besoin d'un suivi permanent, pas juste ponctuel
Solution : suivi PERMANENT (jamais retiré une fois à 100%, bascule en surveillance hebdomadaire) sauf inactivité 90j — wallet_scan_queue.py

------------------------------------------------------------

[DEPLOYE] Sujet    : Extraction récurrente holders → classement top wallets
Date : 2026.07.21  /  Probleme : besoin d'une source de candidats pour le classement
Solution : extraction Blockscout x402 → /topwallets, capacité 600. Wallets confirmés mauvais retirés définitivement de la file (pas seulement du classement) — terminologie "rejeté"/"archivé", jamais "banni" (pas le même mécanisme que la sécurité token) — smart_money_leaderboard.py, token_holder_intel.py

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Seuil pour passage au trading réel sur ce signal
Date : 2026.07.21  /  Probleme : —
Solution : ~500 wallets scorés ET distribution des scores saine (pas dégénérée) — pas encore atteint

------------------------------------------------------------

[CODE] Sujet    : Scan incrémental persistant + formule composite de classement (#157 suite)
Date : 2026.07.15  /  Probleme : le plafond `max_tokens_analyzed` ne pouvait jamais couvrir un wallet très actif (680+ tokens) en un seul appel.
Solution : `wallet_scan_state.py` persiste par wallet les tokens déjà analysés/leurs trades archivés, chaque appel `score_wallets()` traite le prochain lot jusqu'à couverture complète ; composite = percentile de rang contre la population déjà notée (win rate/Sortino/PnL/diversification), échantillon minimum (≥90j/≥100 swaps) et robustesse anti-chance (trim des 10 meilleurs/pires trades) ajoutés — smart_money.py / wallet_scan_state.py (commits `128556d`/`0125c74`).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Cost-basis d'un token reçu par simple virement non mis à zéro
Date : 2026.07.15  /  Probleme : un "achat" = n'importe quel transfert entrant (swap, virement, airdrop) — sans jambe stablecoin dans la même transaction, le prix d'entrée retombe sur le prix de marché OHLCV au moment du transfert, jamais 0$ — un airdrop revendu sous-estime donc le vrai gain du wallet.
Solution : Limite documentée, PAS corrigée — piste identifiée (si la transaction ne contient QUE le transfert entrant seul, fixer le prix d'entrée à 0$) mais nécessite une décision opérateur avant d'être construite — smart_money.py (`_hash_based_price`).

------------------------------------------------------------

[CODE] Sujet    : Marathon de revue croisée multi-IA (Gemini/ChatGPT/Grok/DeepSeek) — plus de 20 correctifs
Date : 2026.07.15  /  Probleme : 18+ angles morts relevés sur `/walletscore` (anti-chance qui se dilue avec le volume, exploits gratuits via wrap/unwrap ou swaps stable-stable, confiance de prix jamais affichée, rug pull mal comptabilisé par le plancher de liquidité, percentile pollué par une couverture partielle ou une confiance de prix basse).
Solution : Chaque point vérifié contre le code avant d'agir (2 affirmations Gemini réfutées après vérification) — trim anti-chance en %, exclusion wrap/unwrap et stable-stable du compteur de swaps, plancher de liquidité confirmée ASYMÉTRIQUE (gate l'achat, jamais la vente — sinon un rug pull disparaît des stats), `price_confirmation_ratio`/`price_confidence_low` affichés et utilisés pour exclure de la population de comparaison percentile. Limites documentées honnêtement en tête de fichier plutôt que masquées (Sybil au-delà de la convergence pairwise = la plus importante, jamais résolue à cette date ; pas de benchmark alpha/bêta ; pas de mark-to-market des positions ouvertes). Patron de défense réutilisable : plancher de qualité confirmée, fail-open sur inconnu/fail-closed sur confirmé-mauvais, ratio de confiance affiché jamais caché, seuil anti-chance qui scale avec l'échantillon — smart_money.py (commits `8565d62` → `4ba693e`).

------------------------------------------------------------

[CODE] Sujet    : Panne réseau confondue avec "pas de pool" fige un prix pour toujours
Date : 2026.07.15 / Probleme : resolve_primary_pool peut échouer transitoirement (timeout/429/erreur serveur) au lieu de confirmer "aucun pool pour ce token" - le scan incrémental persistant ne retentait un token déjà "vu" que si son activité on-chain changeait, jamais sur la simple disparition d'une panne réseau. Une coupure d'une seconde condamnait une jambe à rester "sans prix" pour toujours, faussant durablement le PnL et price_confirmation_ratio du wallet.
Solution : classification transient_pricing_error_tokens exclue du checkpoint "scanné" - redevient éligible au prochain appel sans qu'aucune nouvelle activité ne soit nécessaire - smart_money.py (7ab29a6)

------------------------------------------------------------

[CODE] Sujet    : Percentile ignorait les ex-aequo + Sortino pouvait masquer un PnL réel négatif
Date : 2026.07.15 / Probleme : _percentile plaçait à tort un wallet dont la valeur était exactement égale à celle de la majorité de la population au 0e percentile, indiscernable d'un wallet réellement pire que tout le monde. Séparément, Sortino se calcule sur le rendement en % par trade, jamais pondéré par le capital engagé - un wallet pouvait afficher un Sortino positif "honorable" alors que son PnL réel en dollars était négatif (démontré : 4 micro-trades +100% sur 1$ chacun + 1 trade -50% sur 1000$ -> PnL -496$ mais Sortino +1.4).
Solution : percentile calculé sur le rang moyen (0.5x pour un ex-aequo, convention scipy percentileofscore kind='mean') + nouveau drapeau sortino_pnl_contradiction affiché en ATTENTION à côté du score (le biais sous-jacent n'est pas corrigé, seulement rendu visible) - smart_money.py (0b049ad)

------------------------------------------------------------

[CODE] Sujet    : Pagination Blockscout tronquée silencieusement sur un wallet très actif
Date : 2026.07.15 / Probleme : client.get_token_transfers(limit=2000, max_pages=10) pouvait arrêter la pagination alors que Blockscout avait encore de la donnée au-delà - un wallet à plus de 2000 transferts ERC-20 vie entière voyait ses transferts les plus anciens disparaître sans aucun signal, biaisant potentiellement tous les axes de score, pas seulement unmatched_sell_events.
Solution : nouveau champ TokenTransfersResult.truncated qui distingue "historique réellement épuisé" de "coupé par le plafond ou une erreur réseau en route", affiché en ATTENTION sur la fiche wallet (card.transfer_history_truncated) - smart_money.py (cf. historique git 15/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : /walletscore en prod + file d'attente /walletqueue pour les wallets à forte activité
Date : 2026.07.15 / Probleme : le plafond de 10 tokens analysés par passage rendait la couverture complète d'un wallet extrême (1067 swaps, 680 tokens tradés) impraticable en usage manuel Telegram (~68 rappels /walletscore nécessaires).
Solution : plafond WEIGHTS.max_tokens_analyzed remonté 10->50 + nouveau services/wallet_scan_queue.py (file FIFO SQLite dédiée, dédoublonnage, réutilise le moteur incrémental existant) - cycle heartbeat wallet_scan_queue_cycle (20min, double gate ARIA_WALLET_SCAN_QUEUE_ENABLED + ARIA_WALLET_SCORING_ENABLED) - services/wallet_scan_queue.py (de51a6d)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Sourcing des wallets candidats 100% gratuit, sans Nansen/Zerion
Date : 2026.07.15 / Probleme : qui alimente la file de scan smart-money sans dépendance externe payante (Nansen ~0.005$/appel, Zerion API PnL non confirmée exposée publiquement) ?
Solution : skills/wallet_candidate_sourcing.py liste les détenteurs actuels des tokens déjà jugés gagnants par ARIA (vc_predictions clôturées + paper_trader.get_closed_positions), enfile ces adresses dans wallet_scan_queue - aucun débit minimum garanti, dépend du nombre réel de trades gagnants sur la période - skills/wallet_candidate_sourcing.py (cf. historique git 15/07)

------------------------------------------------------------

[CODE] Sujet    : wallet_scan_queue_cycle bloquait tout le heartbeat jusqu'à 50 minutes
Date : 2026.07.15 / Probleme : le heartbeat d'ARIA traite ses tâches en séquence stricte (une boucle for qui await chaque tâche l'une après l'autre) - un wallet_scan_queue_cycle à 2 wallets x 50 tokens pouvait donc bloquer toutes les autres automatisations activées jusqu'à ~50 minutes, le throttle GeckoTerminal partagé (2.1s/appel) étant la cause du temps par token.
Solution : MAX_WALLETS_PER_CYCLE ramené de 2 à 1 (décision opérateur "pas pressé") - pire cas de blocage ramené à ~25 minutes, sans toucher au throttle GeckoTerminal partagé par tout ARIA - services/wallet_scan_queue.py (cf. historique git 15/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Intelligence wallet/entite - extraction Blockscout x402 + classement /topwallets
Date : 2026.07.21 / Probleme : dependance a des services tiers payants (Nansen/Arkham) jamais achetes pour identifier les meilleurs wallets investisseurs ; aucune source interne de labels d'entite enrichis.
Solution : services/blockscout_x402.py (holders enrichis payes a l'appel) -> token_holder_intel.py (stockage local aria.db, jamais git) -> detection de wallets recurrents sur 3+ tokens -> smart_money_leaderboard.py classe via la formule composite deja existante de smart_money.py, capacite 600, eviction sous percentile 30 ou inactivite 90j. Commande /topwallets, cycle d'extraction 3h. 4 gates actives en prod (cf. historique git 21/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Deux trous de suivi du classement smart-money corriges
Date : 2026.07.21 / Probleme : un wallet devenu inactif (90j+) gardait sa note figee dans le classement sans etre signale ; un wallet confirme mauvais continuait a etre rescanne indefiniment et pouvait reapparaitre.
Solution : remove_and_archive explicite sur inactivite confirmee ; rejet definitif via smart_money_rejected_wallets (meme doctrine que momentum_blacklist.py, terminologie classement/archive jamais banni pour ne pas confondre performance et securite) - smart_money_leaderboard.py (cf. historique git 21/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : analyze_smart_money() -- signal qualite-prioritaire remplace le seuil binaire fixe
Date : 2026.07.22 / Probleme : le score_delta applique a une paire scannee (acp_onchain_scan.py, include_smart_money) etait un forfait fixe (+8) des que >=2 wallets convergents etaient detectes parmi les top holders -- identique pour 2 ou 8 wallets, aucune prise en compte de la QUALITE du signal (composite_percentile deja calcule par le chantier /walletscore, ignore ici).
Solution : nouvelle fonction latest_score_for_wallet() (lecture seule dans wallet_score_log, aucun nouveau calcul) + formule qualite-prioritaire (decision operateur explicite, exemple chiffre verifie : 2 wallets a score 95 -> delta 15, 10 wallets a score 45 -> delta 8 -- la qualite domine toujours la pure quantite). Porte d'entree binaire (>=2 wallets convergents) inchangee -- un seul wallet ne suffit toujours jamais. Fallback modeste (55) pour un wallet jamais score ailleurs - services/smart_money.py (commit 955dd615).

------------------------------------------------------------

[CODE] Sujet    : Blockscout à sec de crédits bloquait la file de scan wallet (73,6% du budget Pro) — Alchemy+Moralis en repli rapide
Date : 2026.07.22 / Probleme : la population wallet-scoring restait bloquée à 5 wallets distincts malgré les 3 gates actifs — cause trouvée dans les logs réels : crédits Blockscout Pro épuisés (402 "Out of credits"), repli automatique vers l'endpoint gratuit Blockscout trop lent/instable sur les wallets actifs (34s puis erreur 500 constatés en conditions réelles sur un wallet réel testé), faisant timeout systématiquement `wallet_scan_queue_cycle` (300s) sans jamais progresser. `token-transfers` (l'endpoint consommé ici) représente à lui seul 73,6% de toute la consommation de crédits Pro du mois.
Solution : nouveau module `services/wallet_transfers_fast.py` — cascade Alchemy (`alchemy_getAssetTransfers`, 120 CU/appel, 30M CU/mois gratuit confirmé) -> Moralis (`erc20/transfers`, 50 CU/appel, 40 000 CU/jour gratuit confirmé par capture du dashboard opérateur) -> indisponible. Branché dans `smart_money.py` (le seul point d'appel `get_token_transfers`), scopé chaîne "base" uniquement (seule chaîne vérifiée), gate `ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED` (OFF par défaut) — si le gate est OFF, la chaîne n'est pas "base", ou les deux fournisseurs échouent, retombe sur Blockscout exactement comme avant ce chantier (comportement historique strictement inchangé). Les deux fournisseurs vérifiés par de VRAIS appels authentifiés (pas la doc) sur le wallet qui avait fait planter Covalent/GoldRush (candidat écarté séparément, cf. entrée dédiée ci-dessous) — tous deux répondent en <4s. Capacité combinée (~274 000 appels/mois) vs volume actuel Blockscout sur ce poste (~18 700 appels/mois) : marge ~14x. `services/wallet_transfers_fast.py` / `services/smart_money.py` — 17 nouveaux tests (`test_wallet_transfers_fast.py`), suite complète 6818 passed / 17 skipped, `test_coherence.py` vert (non commité au moment de cette entrée). Note : ni Alchemy ni Moralis ne fournissent de prix historique USD natif — ARIA garde sa propre reconstruction de prix (OHLCV/GeckoTerminal), aucun changement sur ce point. Débit de scan (`MAX_WALLETS_PER_CYCLE=1`) volontairement PAS augmenté dans ce même correctif — la vraie contrainte du pire cas (~25 min/wallet) vient du throttle GeckoTerminal (2,1s/appel, jusqu'à 50 tokens), pas de Blockscout ; changer ce réglage exige une mesure empirique séparée avant d'y toucher.

------------------------------------------------------------

[CONFIG] Sujet    : Covalent/GoldRush écarté comme fournisseur de scan détaillé
Date : 2026.07.22 / Probleme : diligence sur Covalent (rebrandé GoldRush) comme alternative à Blockscout pour le scan de transferts wallet.
Solution : écarté après test réel — timeout 34s puis erreur 500 sur un wallet actif + WETH (exactement le profil de wallet à mieux scorer) ; prix historique natif (`quote_rate`/`delta_quote`) revenu `null` sur un petit token testé (pas garanti universellement) ; structure de réponse réelle différente de la doc simplifiée fournie (transaction imbriquée avec sous-tableau `transfers[]`, pas un objet plat). Alchemy/Moralis retenus à la place (cf. entrée ci-dessus) — action opérateur uniquement (compte créé, pas de commit).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : /walletqueue bloqué depuis 7j+ sur un seul wallet — cause probable identifiée, PAS corrigé
Date : 2026.07.23  /  Probleme : mesure empirique demandée par le backlog (#21) — `docker logs`/lecture directe de `wallet_scan_checkpoint`/`wallet_scan_queue` sur le conteneur prod confirment : le wallet `0xfceed0502ab2995f5a9a243758ec43e64e4ef0b9` apparaît 262 fois dans les logs sur 7 jours (timeout systématique ~300s, "plafond de 50 tokens atteint" à chaque tentative), son checkpoint persistant (`wallet_scan_checkpoint.last_scan_at`) est figé depuis le 16/07 (aucun progrès réel). Les 4 AUTRES wallets de la file (ajoutés le même jour, 15/07) n'apparaissent ZÉRO fois dans les logs sur 7 jours — jamais scannés du tout. `MAX_WALLETS_PER_CYCLE=1` combiné à ce blocage gèle toute la population wallet-scoring, pas juste un débit lent.
Solution : PAS CORRIGÉ — cause probable identifiée (`score_wallets` ne persiste le checkpoint qu'APRÈS avoir traité tout le lot de `WEIGHTS.max_tokens_analyzed=50` tokens ; un timeout à mi-lot perd tout le travail déjà fait) mais le mécanisme EXACT qui fait que ce wallet précis est toujours re-choisi (malgré un `next_check_at` plus récent que les 4 autres, ce qui devrait le désavantager dans le tri `ORDER BY next_check_at ASC` de `list_pending()`) reste à confirmer — pas de correctif écrit à l'aveugle sur ce code de prod critique sans certitude complète. Backlog #29 créé pour reprendre l'investigation (logging de diagnostic recommandé avant tout correctif) — `services/smart_money.py` / `services/wallet_scan_queue.py` / `services/wallet_scan_state.py`.

------------------------------------------------------------

[CODE] Sujet    : Détection copy-trading/bot — flag séparé, jamais mélangé au composite_percentile
Date : 2026.07.22  /  Probleme : `composite_percentile` mesure la PERFORMANCE d'un wallet, mais un wallet qui se contente de copier systématiquement un autre wallet déjà scoré (bot ou suiveur pur) peut afficher une bonne performance sans jamais démontrer de conviction indépendante — rien ne distinguait ce cas d'un vrai smart-money. Design vérifié indépendamment (22/07) après une proposition externe attribuée à "Grok" v2 qui suggérait de mélanger ce signal DANS le score composite — écarté, confirmé avec l'opérateur (Option 1 : le composite reste pur performance, la détection de copie est un flag séparé).
Solution : nouvelle table `wallet_entry_timestamps` (wallet, contract, chain, entry_ts), peuplée GRATUITEMENT dans `smart_money._analyze_wallet_multi_token` — sous-produit de `earliest_buy_ts`, déjà calculé pour le critère "early entry" existant, zéro appel réseau supplémentaire (`services/copy_trading_detection.py` — nouveau module dans `skills/`, la doctrine `gather_*_facts`/`judge_*` déjà utilisée par `dev_wallet.py`/`insider_wallets.py`). Requête de corrélation (jointure de la table sur elle-même, une seule requête) : un wallet qui entre systématiquement 5-15 min après un AUTRE wallet déjà scoré, sur ≥3 tokens DISTINCTS → `copy_trading_suspected` ; sous ce seuil (y compris un chevauchement isolé sur un seul token, qui peut être une simple réaction indépendante à la même annonce publique) → `independent`. Câblé dans `score_wallets` via `_resolve_copy_trading` (nouveau, fusionne le résultat sur toutes les chaînes de `card.chains_scanned`), nouveau champ `WalletScoreCard.copy_trading_flag`/`copy_trading_points` — informationnel, jamais lu par `_apply_comparative_ranking`/le calcul du composite. `services/smart_money.py` / `skills/copy_trading_detection.py` (nouveau) — 31 nouveaux tests (`test_copy_trading_detection.py` nouveau, 3 tests de câblage bout-en-bout dans `test_smart_money_wallet_scoring.py`, nouvelle fixture d'isolation DB `_isolated_copy_trading_db`), suite complète 6884 passed / 17 skipped, `test_coherence.py` vert (non commité au moment de cette entrée).

------------------------------------------------------------

[CODE] Sujet    : Sourcing CabalSpy — wallets KOL labellisés (identité complète), catégorisation multi-chain
Date : 2026.07.23  /  Probleme : opérateur a demandé une nouvelle source de candidats pour `/walletscore`, avec l'idée explicite de "noter les wallets connus et éjecter les mauvais" — `wallet_candidate_sourcing.py` a une doctrine "zéro dépendance externe" (Nansen/Zerion déjà écartés) ; CabalSpy diligencé et vérifié en conditions réelles (curl direct opérateur, capture) avant tout code.
Solution : **changement de politique assumé** (décision opérateur explicite, pas un dérapage) — palier Free confirmé (0$/mois, 10 000 crédits, sans CB). Vérifié réel : `GET /v1/wallets?blockchain=base&type=kol` → 200 wallets Base avec identité COMPLÈTE (name/twitter/telegram/copytrade_link) — la vraie valeur ajoutée. `type=smart` → 38 wallets ANONYMES (recoupe probablement `smart_money.py`, peu de valeur). `GET /v1/wallets/lookup?address=...` cherche une adresse sur toutes les chaînes en un appel (`found:false` sur Vitalik et un wallet Base random d'ARIA — base restreinte à quelques centaines de KOL connus, pas exhaustive). Nouveau `services/cabalspy.py` (pagination cursor, throttle 1 req/s non sourcé officiellement — doc silencieuse sur le rate limit) + `skills/cabalspy_candidate_sourcing.py` : **deux volets séparés** — (1) catégorisation (`cabalspy_kol_wallets`, TOUTES chaînes — Base/BNB/Solana — simple répertoire) et (2) sourcing réel vers `wallet_scan_queue.enqueue_wallets()` UNIQUEMENT pour Base (seul pipeline downstream vérifié capable de les traiter — `smart_money.py`/Blockscout câblés Base-only en dur, confirmé dans le code). BNB (EVM, effort d'extension pas encore vérifié) et Solana (format d'adresse différent, aucun Blockscout, chantier séparé) catalogués mais jamais scorés à tort. Resynchronisation complète plafonnée à 1x/semaine (économie de crédits). Câblé au heartbeat (`cabalspy_candidate_sourcing_cycle`, 180 min), triple gate (`ARIA_CABALSPY_SOURCING_ENABLED` + `ARIA_WALLET_SCAN_QUEUE_ENABLED` + `ARIA_WALLET_SCORING_ENABLED`), tous OFF par défaut. `services/cabalspy.py` (nouveau) / `skills/cabalspy_candidate_sourcing.py` (nouveau) / `heartbeat.py` — 19 nouveaux tests, suite complète à confirmer.

------------------------------------------------------------

[CODE] Subject  : `/walletqueue` permanent stall — root cause confirmed and fixed (follow-up to the 07/23 [ETAT ACTUEL] entry above)
Date : 2026.07.23 / Problem : follow-up diagnostic (real Docker logs + code + DB read, not a guess) confirmed the exact mechanism left open above: `heartbeat.py`'s 300s per-task timeout (`_TASK_TIMEOUT_SECONDS`) silently cancels `run_wallet_scan_queue_cycle` mid-batch whenever GeckoTerminal rate-limits hard enough -- `score_wallets()` only persists its checkpoint (`wallet_scan_state.save_checkpoint`) AFTER the full 50-token batch is scored, so the cancellation throws away all progress already made. Candidate selection being deterministic, the next cycle retries the exact same sub-batch -- forever. Live logs (23/07) showed the default 50-token batch taking ~18-20 minutes under sustained 429s, always blowing past the 300s budget. Secondary, distinct contributor also confirmed: a total multi-chain fetch failure (`card.available=False`) is silently treated the same as "no new milestone this pass" in `run_wallet_scan_queue_cycle` -- noted but NOT addressed in this fix (out of scope of what was validated).
Solution : new `BACKGROUND_QUEUE_MAX_TOKENS_PER_WALLET = 10` passed as `score_wallets(..., max_tokens=...)` for this queue path ONLY -- the interactive `/walletscore` command keeps the full 50 for a comprehensive one-shot score, unchanged. Calibration: ~24s/token observed under sustained rate-limiting -> 10 tokens ≈ 240s, leaving a real margin under the 300s timeout for the rest of the cycle's own work. A smaller batch that reliably checkpoints each cycle beats a larger one that never does. `services/wallet_scan_queue.py` -- 1 new dedicated test (`test_cycle_caps_max_tokens_for_the_background_queue_path`), full suite green, `test_coherence.py` green (not yet committed at the time of this entry).

------------------------------------------------------------

[CODE] Subject  : Wallet identity enrichment -- Farcaster reverse-lookup + Basenames forward resolution, in-house instead of paying Neynar
Date : 2026.07.24 / Problem : operator asked whether a wallet address could surface a name/ENS/linked X account, and separately whether paying Neynar via x402 for this was worthwhile. Real diligence done before writing any code: a live authenticated Dune query confirmed `dune.neynar.dataset_farcaster_verifications`'s real schema (the verified address lives inside a JSON-encoded `claim` VARCHAR column, not a direct column -- `json_extract_scalar(claim, '$.address')` needed). A live, unauthenticated call to `api.warpcast.com/v2/user?fid=<fid>` then confirmed Warpcast's own `connectedAccounts` field already exposes a linked X account for FREE (platform=="x") -- Neynar was NOT needed for this signal at all, closing that question with a working free alternative rather than a subscription decision.
Solution : two new orchestrated pieces, zero new paid surface. (1) `dune.get_farcaster_fid_by_address()` (new query builder + function in `services/dune.py`, same anti-injection EVM-format validation as `build_addresses_stats_query`) reverse-resolves an address to a Farcaster fid via the already-paid/calibrated Dune client. (2) `farcaster.get_profile_by_fid()` (new function in `services/farcaster.py`, same free no-key Warpcast client as `verify_profile()`) resolves that fid to a full profile including the linked X account. (3) New `services/farcaster_reverse.py` orchestrates both into a single `reverse_lookup_address()` -- dome doctrine throughout, no verified Farcaster account for an address is a normal outcome, never an error. Separately, Basenames FORWARD resolution (name -> address) was built as `services/basenames.py` -- the REVERSE direction (address -> name) already existed for free via `Blockscout.get_address_info().ens_domain_name`, already wired into `smart_money.py`'s `display_name` for /walletscore and /topwallets, so only the missing forward direction was built. Real architecture bug found and fixed while validating end-to-end (never assumed from memory or a single web fetch): a name's resolver is NOT always the well-known default L2Resolver proxy -- a real round-trip test against "jesse.base.eth" (independently cross-checked via Blockscout's own `ens_domain_name` field) returned an all-zero address when calling the default resolver directly, and only resolved correctly once `Registry.resolver(node)` was queried FIRST to find this name's actual (custom) resolver. Both the Registry (`0xb94704422c2a1e396835a571837aa5ae53285a95`) and the default resolver proxy addresses were independently verified via Blockscout's own contract-verification API before use, never trusted from a single web-fetched doc. `services/dune.py` / `services/farcaster.py` / `services/farcaster_reverse.py` (new) / `services/basenames.py` (new) -- 8 new Dune tests, 6 new Farcaster tests, 6 new farcaster_reverse tests, 8 new basenames tests (namehash validated against the published EIP-137 test vector), full suite to confirm, `test_coherence.py` green.

------------------------------------------------------------

[CODE] Subject  : GeckoTerminal's two independent throttle locks unified (throughput audit finding)
Date : 2026.07.24 / Problem : a full throughput-smoothing audit (workflow, 3 agents) confirmed `services/geckoterminal.py`'s `GeckoTerminalClient` owns its own throttle (`_lock`/`_throttle`, ~2.222s/call, used by `resolve_primary_pool`/`get_pool_created_at`), while `GeckoTerminalClient.get_ohlcv` lazily delegates to `services/ohlcv.py`'s module-level `ohlcv_client` -- which had its OWN, completely independent `asyncio.Lock` (`min_interval=2.2`), never coordinated with the first one via `wait_for_shared_rate_limit()` (the 21/07 fix that already unified vanguard/backend's client with this one). Confirmed live in the exact hot path the operator cares about: `smart_money.py`'s per-token loop calls BOTH `gecko.resolve_primary_pool(...)` and `gecko.get_ohlcv(...)` on the SAME external GeckoTerminal account, paced by two genuinely separate timers.
Solution : `OHLCVClient.__init__` gained an opt-in `use_shared_throttle: bool = False` parameter (default False -- the 7 existing test-constructed instances with `min_interval=0.0` keep working completely unmodified, never touching the shared limiter). When `True`, `_throttle()` calls `geckoterminal.wait_for_shared_rate_limit()` (lazy import inside the method, never at module load -- no circular-import risk even though `geckoterminal.py` itself lazily imports `ohlcv.py` back). Only the ONE real-production singleton, `services/ohlcv.py`'s module-level `ohlcv_client`, opts in (`OHLCVClient(use_shared_throttle=True)`) -- a simpler, safer design than injecting a throttle callback, since the import failure surface only exists at first real use, never at import time. `services/ohlcv.py` -- 3 new tests (`test_ohlcv_client.py`: default instance keeps its own lock, opt-in calls the shared limiter, the module singleton itself has the flag set), full suite green, `test_coherence.py` green.

------------------------------------------------------------

[CODE] Subject  : Adaptive circuit breaker on wallet_transfers_fast.py's Alchemy/Moralis cascade (Item #125)
Date : 2026.07.27 / Problem : `get_fast_token_transfers` (Alchemy primary -> Moralis second resort -> Blockscout, caller-handled) had zero memory of a confirmed failure across calls -- a sustained Alchemy outage made every wallet-scoring candidate retry Alchemy's full backoff loop before falling through to Moralis, every single time, same anti-pattern already fixed for DexScreener (Item #123) and Blockscout (Item #124) this same week.
Solution : same pattern/thresholds as those two breakers (`_CIRCUIT_FAIL_THRESHOLD=3`, `_CIRCUIT_COOLDOWN_SECONDS=180.0`) -- but keyed PER PROVIDER (`"alchemy"`/`"moralis"`, plain dicts) since the two can fail completely independently. `get_fast_token_transfers` checks `_in_cooldown(provider)` before attempting each one, skipping straight to the next tier while a provider is paused. Real bug caught by the tests before it ever reached prod: the first version recorded an outcome even when a provider's API key was simply absent (a static config condition, not a network failure) -- 3 candidates with no `MORALIS_API_KEY` configured would have opened Moralis's breaker for no reason, masking the real "not configured" state behind a misleading "circuit breaker open" one. Fixed by only calling `_record_outcome` when the provider's own API key is actually present (a real network attempt was made). `services/wallet_transfers_fast.py` -- 3 new dedicated tests (`test_wallet_transfers_fast.py`: Alchemy's breaker opens after 3 distinct failures and the 4th candidate skips straight to Moralis with zero Alchemy HTTP calls, a success in between resets the counter so 2+1+2 never opens it, the cooldown expires and a fresh call succeeds normally). Full suite to confirm, `test_coherence.py` to confirm (not yet committed at the time of this entry).

------------------------------------------------------------

[DEPLOYE] Subject  : Wallet-scan-queue catch-up loop was regenerating a full LLM thesis on every intermediate sub-batch (real x.ai cost bug)
Date : 2026.07.27 / Problem : operator flagged a real Grok/x.ai spend spike (~$0.40-0.68/day vs a $0.30/day total-LLM budget target) and asked to audit every LLM consumer. Traced live via console.x.ai's request logs (browser, session already authenticated) to `wallet_scan_queue.py`'s catch-up loop (Item #84, 07/24): it re-calls `smart_money.score_wallets()` in a tight loop for up to 4 minutes straight (`CATCHUP_CYCLE_SOFT_DEADLINE_SECONDS=240`) while progressively covering ONE wallet's token history -- every single sub-batch call regenerated a full narrative thesis (`_generate_thesis`, ~950 prompt tokens) on a still-PARTIAL, soon-discarded score, even though only the numeric progress counter is ever shown before `full_coverage` (the thesis text is only used once, in the FINAL report via `format_wallet_scoring_report`'s `report.synthesis`).
Solution : new `skip_thesis: bool = False` param on `score_wallets()` (default False -- zero behavior change for every other caller, including the manual `/walletscore` command, which still gets its thesis immediately even on partial coverage). The catch-up loop in `wallet_scan_queue.py` now passes `skip_thesis=True` on every intermediate sub-batch call, then makes exactly ONE additional call WITHOUT `skip_thesis` right after the loop exits -- but ONLY when it exited on real full coverage (a stall or deadline timeout means the next cycle is still catching up, so a thesis now would just be discarded again). Net effect: same number of LLM calls as a single-shot scan (exactly 1 per wallet reaching full coverage), instead of one per sub-batch. `services/smart_money.py` / `services/wallet_scan_queue.py` -- 1 new dedicated test (`test_smart_money_wallet_scoring.py::test_skip_thesis_never_calls_the_llm`, asserts the LLM callable is never invoked) + 1 existing test updated to expect the new call pattern (`test_wallet_scan_queue.py::test_catchup_wallet_loops_multiple_subbatches_until_full_coverage`), targeted suite green (300 passed), `test_coherence.py` green.
