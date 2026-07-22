# HANDOFF — Wallet-scoring / smart-money (/walletscore, classement)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Le 15/07 a été un marathon de revue croisée (Gemini/ChatGPT/DeepSeek, 4 IA, 6 rounds, 22+
> correctifs) — résumé par grand thème ici, pas un correctif par ligne. Détail exact :
> historique git, commits du 15/07 préfixés #157 à #178.

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

[CODE] Sujet    : Détection copy-trading/bot — flag séparé, jamais mélangé au composite_percentile
Date : 2026.07.22  /  Probleme : `composite_percentile` mesure la PERFORMANCE d'un wallet, mais un wallet qui se contente de copier systématiquement un autre wallet déjà scoré (bot ou suiveur pur) peut afficher une bonne performance sans jamais démontrer de conviction indépendante — rien ne distinguait ce cas d'un vrai smart-money. Design vérifié indépendamment (22/07) après une proposition externe attribuée à "Grok" v2 qui suggérait de mélanger ce signal DANS le score composite — écarté, confirmé avec l'opérateur (Option 1 : le composite reste pur performance, la détection de copie est un flag séparé).
Solution : nouvelle table `wallet_entry_timestamps` (wallet, contract, chain, entry_ts), peuplée GRATUITEMENT dans `smart_money._analyze_wallet_multi_token` — sous-produit de `earliest_buy_ts`, déjà calculé pour le critère "early entry" existant, zéro appel réseau supplémentaire (`services/copy_trading_detection.py` — nouveau module dans `skills/`, la doctrine `gather_*_facts`/`judge_*` déjà utilisée par `dev_wallet.py`/`insider_wallets.py`). Requête de corrélation (jointure de la table sur elle-même, une seule requête) : un wallet qui entre systématiquement 5-15 min après un AUTRE wallet déjà scoré, sur ≥3 tokens DISTINCTS → `copy_trading_suspected` ; sous ce seuil (y compris un chevauchement isolé sur un seul token, qui peut être une simple réaction indépendante à la même annonce publique) → `independent`. Câblé dans `score_wallets` via `_resolve_copy_trading` (nouveau, fusionne le résultat sur toutes les chaînes de `card.chains_scanned`), nouveau champ `WalletScoreCard.copy_trading_flag`/`copy_trading_points` — informationnel, jamais lu par `_apply_comparative_ranking`/le calcul du composite. `services/smart_money.py` / `skills/copy_trading_detection.py` (nouveau) — 31 nouveaux tests (`test_copy_trading_detection.py` nouveau, 3 tests de câblage bout-en-bout dans `test_smart_money_wallet_scoring.py`, nouvelle fixture d'isolation DB `_isolated_copy_trading_db`), suite complète 6884 passed / 17 skipped, `test_coherence.py` vert (non commité au moment de cette entrée).
