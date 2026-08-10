# HANDOFF — GoPlus (Token Security API, honeypot check)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Item #212 -- watchlist de 600 slots remplace l'appel synchrone sur Base/Ethereum
Date : 2026.07.29 / Probleme : suite au diagnostic quota (entree ETAT ACTUEL ci-dessous), calcul du vrai debit soutenable (marge 90%, contrainte la plus stricte des 3 confirmees au dashboard -- 150 CU/min, 30k CU/jour, 150k CU/mois) : 135 000 CU/mois utilisables / 15 CU par jeton EVM / 43 200 min/mois = ~288s entre verifications honeypot, ~43x plus lent que le calibrage du 21/07 (6,667s) qui n'avait considere que la contrainte CU/min (la moins stricte des 3). Un appel synchrone par candidat (comportement historique) ne peut pas tenir ce debit sans bloquer chaque evaluation ~5 minutes.
Solution : nouveau `services/goplus_watchlist.py` -- 600 candidats deja qualifies par tous les gates gratuits (liste noire/liquidite/volume/wash-trading/parabolique/profil/concentration) tournent en boucle a debit soutenable (600 x 288s = 48h pile pour un cycle complet, calcul verifie exact par l'operateur), rafraichis par un nouveau cycle de fond heartbeat (`goplus_watchlist_cycle`, 5min, double gate `ARIA_PAPER_TRADING_ENABLED`+`ARIA_GOPLUS_WATCHLIST_ENABLED`, tous deux OFF par defaut). `momentum_entry._check_honeypot` ne fait plus AUCUN appel reseau synchrone sur Base/Ethereum -- lit la watchlist (statut frais <48h) ou met en file d'attente (nouveau code `honeypot_pending`, jamais un rejet definitif, jamais blackliste, exclu du contrefactuel comme `honeypot_unavailable`). Score de priorite pour les 600 slots base sur liquidite+activite (signaux gratuits, calculables AVANT le honeypot check que la watchlist differe justement) -- jamais le score DEX composite complet (qui depend deja du honeypot check). Solana garde l'ANCIEN chemin synchrone inchange (volume marginal, deja "quasi bloque par couverture GoPlus" -- cf. entree ETAT ACTUEL 17/07 plus bas, RugCheck fallback ne mappe pas proprement sur le stockage TokenSecurity de la watchlist). Commande diagnostic `/goplusqueue` (Telegram, admin-only) -- demande operateur explicite. `services/goplus_watchlist.py` (nouveau), `momentum_entry.py` (`_check_honeypot`/`_evaluate_security_verdict`/`run_goplus_watchlist_cycle`), `heartbeat.py` (cycle + double gate), `counterfactual_tracker.py` (exclusion `honeypot_pending`), `gateway/telegram_bot.py` (`/goplusqueue`), `token_candidate_screening.py` (liquidity_usd/volume_24h_usd transmis). Tests : `test_goplus_watchlist.py` (19), extensions `test_momentum_entry.py`/`test_token_candidate_screening.py` -- suite complete verte (8434 passed), `test_coherence.py` vert. Deploye (commit `5e9a09c5`), confirme en prod : watchlist alimentee par le pipeline reel + cycle de fond confirme actif apres activation du gate dedie (necessite une RECREATION du conteneur -- `docker restart` seul ne relit jamais un `.env` modifie, les variables sont figees a `docker run --env-file`, piege reel rencontre le jour meme).

------------------------------------------------------------

[DEPLOYE] Sujet    : Item #212 suite -- Honeypot.is promu source PRIMAIRE permanente, GoPlus dernier recours
Date : 2026.07.29 / Probleme : l'entree precedente (juste en dessous) faisait de Honeypot.is un simple repli TEMPORAIRE (consulte seulement quand GoPlus repond indisponible, un seul candidat verifie par passage de 5min -- toujours limite par le debit GoPlus meme quand celui-ci echoue systematiquement). Decision operateur explicite le jour meme (apres verification qualite reelle, voir plus bas) : rendre ce nouvel ordre PERMANENT, pas juste pour la duree de la panne -- Honeypot.is devient la source n°1 pour toujours, GoPlus seulement en dernier recours quand Honeypot.is lui-meme echoue.
Solution : `_check_watchlist_candidate` essaie TOUJOURS Honeypot.is en premier ; GoPlus n'est appele QUE si Honeypot.is echoue, et plafonne a UN SEUL appel GoPlus par passage (defense en profondeur -- si Honeypot.is tombait en panne generalisee, ca n'inonderait jamais GoPlus). Le cycle traite desormais un LOT (`_GOPLUS_WATCHLIST_BATCH_SIZE=100`) par passage au lieu d'1 seul candidat -- debit Honeypot.is reel confirme par un vrai burst test (150 requetes a ~9,5 req/s soutenu = 0 echec ; a ~22,7 req/s = 59 echecs 429), calibre a 5 req/s (marge 90%, throttle `_MIN_INTERVAL_S` de 1.0s a 0.2s dans `services/honeypot_is.py`) -- 100 candidats/passage = ~20s, watchlist de 600 videe en ~6 passages (~30min) au lieu de 48h. Verification QUALITE reelle avant de valider la decision (jamais suppose) : teste sur FLOCK (candidat reel du pipeline, ordre limite pose le jour meme) -- verdict clean correct, holderAnalysis riche (950 holders testes, 950 reussis) ; teste sur BRIAN (le vrai honeypot ayant cause la perte reelle de -8962$ le 17/07, deja dans `momentum_blacklist`) -- detecte CORRECTEMENT (`isHoneypot:true`, `sellTax:100%`, raison technique precise "execution reverted: STF", `holderAnalysis` cohérente 12/13 echecs de vente) -- signal de qualite tres rassurant, donnee parfois plus riche que GoPlus. Limite structurelle assumee en connaissance de cause (decision operateur explicite) : `owner_change_balance` de GoPlus ne sera quasiment plus jamais verifie en routine, meme apres le retour du quota GoPlus -- accepte car capital 100% fictif (test 1M$) ; rappel banque pour une passe de rattrapage GoPlus dans ~15 jours sur les candidats deja acceptes pendant la fenetre de transition. `momentum_entry.py` (`_check_watchlist_candidate` nouveau, `run_goplus_watchlist_cycle` reecrit -- retourne desormais `blacklisted` comme LISTE, plus une string unique), `services/honeypot_is.py` (throttle 0.2s). Tests : suite `run_goplus_watchlist_cycle` entierement reecrite (Honeypot.is primaire, GoPlus dernier recours plafonne, traitement par lot) -- suite complete verte (8446 passed), `test_coherence.py` vert.

------------------------------------------------------------

[DEPLOYE] Sujet    : Item #212 suite -- second avis TEMPORAIRE Honeypot.is pendant l'epuisement du quota GoPlus (~15 jours)
Date : 2026.07.29 / Probleme : la watchlist ci-dessus geree correctement le DEBIT, mais l'operateur signale que le quota GoPlus reel ne se renouvelle que dans ~15 jours -- bien plus long que ce que la watchlist seule peut ponter (les candidats resteraient "en attente" indefiniment, le pipeline momentum n'achete plus rien sur Base/Ethereum pendant tout ce temps).
Solution : diligence reelle avant integration (doctrine "jamais coder contre une doc sans un vrai appel") -- Honeypot.is teste en direct (curl) sur Base ET Ethereum (WETH sur les deux, reponses coherentes `isHoneypot`/`buyTax`/`sellTax`/`chain.name`), aucune cle API requise a ce jour ("API Key system is not yet implemented", docs.honeypot.is), debit reel observe via les en-tetes `x-ratelimit-*` : 50 requetes par fenetre glissante COURTE (quelques secondes) -- bien plus genereux que GoPlus, legitimite confirmee (reference QuickNode/RugDoc/plusieurs integrations GitHub dont une listant Base explicitement comme chaine supportee). Nouveau `services/honeypot_is.py` (throttle prudent 1 req/s, tres en dessous du plafond reel observe) consulte UNIQUEMENT dans `run_goplus_watchlist_cycle` quand GoPlus lui-meme repond indisponible -- jamais un remplacement permanent, GoPlus reprend automatiquement la main des qu'il repond `available=True` a nouveau (rien a revenir manuellement). Limite honnetement documentee : Honeypot.is n'a pas d'equivalent au veto `owner_change_balance` de GoPlus (ajoute le 22/07 apres l'incident CNX) -- ce signal precis reste non couvert pendant la fenetre de repli. `services/honeypot_is.py` (nouveau), `momentum_entry.run_goplus_watchlist_cycle` (bascule si GoPlus indisponible). Tests : `test_honeypot_is.py` (9) + 4 nouveaux tests `test_momentum_entry.py` (fallback clean/confirme/double-panne/jamais-consulte-si-goplus-ok) -- suite complete verte (8447 passed), `test_coherence.py` vert.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Item #212 -- code 4029 en boucle soutenue, quota mensuel/jour epuise (pas un debit)
Date : 2026.07.29 / Probleme : coupe-circuit (5 echecs consecutifs, pause 300s) se rouvre en boucle depuis ~15h00 UTC, toutes les ~6-7 minutes, compteur d'echecs consecutifs qui ne redescend jamais a 0 (monte 5->6->7->8->9...). Throttle deja calibre a 9 req/min (6.667s, 90% du vrai plafond 150 CU/min = 10 req/min confirme au dashboard le 21/07) -- hypothese initiale (debit instantane depasse par la charge cumulee de plusieurs appelants) invalidee par un test empirique : un SEUL appel, totalement isole (coupe-circuit ferme, aucune charge concurrente, contrat WETH -- jamais lui-meme suspect), echoue immediatement avec "rate limit GoPlus" (code 4029) des le premier essai post-cooldown.
Solution : le vrai probleme n'est pas le debit par minute (deja respecte) mais un quota CU mensuel/jour du palier Free (150k CU/mois documente comme motif du cache le 22/07, jamais confirme au dashboard depuis) qui semble epuise -- bloque tout appel jusqu'a son renouvellement, quelle que soit la cadence. Cause probable du volume : Ethereum ajoute comme 2e chaine au pipeline momentum (26/07) + VC pocket qui recommence a consommer (Item #200, retry_stale_pending elargi le 29/07) + wallet-scoring en fond -- plus de contrats verifies/mois qu'avant, sur un quota inchange. Aucun code deploye : le dome (fallback propre `available=False`, jamais de donnee inventee) protege deja le pipeline correctement en attendant. Chiffre exact du quota et date de reset a confirmer par l'operateur directement au dashboard gopluslabs.io (identifiants jamais manipules par une session Claude Code) avant d'envisager un palier payant -- soumis a la regle "outils payants, seuil 1000$/mois de revenus ARIA". `services/goplus.py` (aucun changement, diagnostic seul).

------------------------------------------------------------

[CODE] Sujet    : Item #127 -- support batch multi-adresses verifie et falsifie (rien a exploiter)
Date : 2026.07.28 / Probleme : `get_token_security()` envoie toujours UNE seule adresse (`contract_addresses`) par appel malgre le nom de parametre au pluriel, qui laissait supposer un vrai support batch jamais exploite -- hypothese a verifier avant toute implementation (norme du projet : jamais coder contre une doc/API sans un vrai appel reel).
Solution : 4 appels reels en direct (curl) confirment que GoPlus IGNORE SILENCIEUSEMENT tout sauf la PREMIERE adresse d'une liste separee par virgules -- teste sur Base (chain_id=8453, 2 ordres d'adresses differents), avec la virgule encodee en %2C, et sur Ethereum mainnet (chain_id=1, pour ecarter un bug specifique a Base) : dans les 4 cas, `code:1 "OK"` mais `result` ne contient QUE la premiere adresse, aucune erreur ni avertissement de troncature. La doc officielle (docs.gopluslabs.io) ne revendique d'ailleurs aucun support batch (parametre au singulier dans le texte, aucune note sur un maximum d'adresses). Rien a construire -- le nom de parametre au pluriel etait un faux indice. Commentaire ajoute dans le code pour qu'une session future ne retente pas la meme hypothese sans revérifier. `services/goplus.py` (commentaire seul, aucun changement fonctionnel).

------------------------------------------------------------

[DEPLOYE] Sujet    : Rate-limit signalé en HTTP 200 (code 4029), jamais retenté
Date : 2026.07.17  /  Probleme : GoPlus signale son rate-limit via un HTTP 200 avec code:4029 dans le corps, pas un vrai 429 — la retry existante ne se déclenchait jamais
Solution : détection explicite de code==4029 sur une réponse 200, même politique de backoff que le vrai 429 — services/goplus.py (a5a3b2ed)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Solana quasi bloqué par couverture GoPlus
Date : 2026.07.17  /  Probleme : —
Solution : pas un bug — 4/5 candidats Solana testés rejetés faute de donnée GoPlus (token pump.fun trop frais). Décision opérateur explicite : Solana reste au même standard de sécurité que Base, jamais assoupli, fail-closed voulu — pas de commit (doctrine, pas de code)

------------------------------------------------------------

[DEPLOYE] Sujet    : Calibrage débit erroné (1.212s)
Date : 2026.07.21  /  Probleme : premier calibrage basé sur un test empirique mal interprété — GoPlus facture PAR TOKEN VÉRIFIÉ (15 CU/token EVM), pas par appel HTTP
Solution : vraie limite confirmée au dashboard (150 CU/min palier Free) = 10 req/min réelles, 90% = 9/min → 6.667s — services/goplus.py (ce886a24)

------------------------------------------------------------

[DEPLOYE] Sujet    : Header d'authentification jamais reconnu par l'API
Date : 2026.07.21  /  Probleme : header envoyé "access-token" au lieu de "Authorization: Bearer" — jamais reconnu depuis le début, appels tournaient sur le palier anonyme sans jamais utiliser le compte authentifié
Solution : corrigé vers "Authorization: Bearer <token>" — a révélé un 2e bug le même jour (code 4012, cf. entrée suivante) — services/goplus.py (363a9e89)

------------------------------------------------------------

[DEPLOYE] Sujet    : Authentification rejetée (code 4012) malgré jeton valide
Date : 2026.07.21  /  Probleme : une fois le header corrigé ci-dessus, l'API rejette le jeton avec "Wrong Signature" alors que /token l'a émis avec succès
Solution : repli temporaire sur l'API publique + cooldown 30min — traitait le symptôme, pas la cause (cf. entrée du 22/07 pour la vraie cause) — services/goplus.py (8bc51bda)

------------------------------------------------------------

[DEPLOYE] Sujet    : Cause racine du code 4012 — double préfixe Bearer
Date : 2026.07.22  /  Probleme : GoPlus renvoie parfois access_token DÉJÀ préfixé "Bearer " dans la chaîne elle-même — notre code rajoutait un second préfixe ("Bearer Bearer ...")
Solution : normalisation du token à l'extraction (retire un préfixe "bearer " insensible à la casse). Vérifié en direct (WETH, USDC) + confirmé sur le dashboard GoPlus lui-même (0 requête/30j → requêtes réelles trackées) — services/goplus.py (3239d0d6)

------------------------------------------------------------

[DEPLOYE] Sujet    : Cache de sécurité par contrat (dédup de ressource rare)
Date : 2026.07.22  /  Probleme : chaque check honeypot consomme du CU réel désormais que l'auth fonctionne — risque réel de dépasser les plafonds jour/mois (30k/150k CU) sans dédup
Solution : token vérifiablement renoncé (owner_address vide + aucune porte dérobée confirmée) → cache 30 jours (rien ne peut plus changer). Sinon → cache 120s (dédup des réévaluations rapprochées) — services/goplus.py (7e4f78d9)

------------------------------------------------------------

[DEPLOYE] Sujet    : Coupe-circuit reactif + retry cible sur honeypot no_data
Date : 2026.07.21 / Probleme : aucun coupe-circuit sur pannes GoPlus repetees ; un verdict honeypot_unavailable propre mais vide (no_data) n'etait jamais retente alors qu'il correspondait le plus souvent a un simple delai d'indexation (quasi tous les faux negatifs re-testes juste apres etaient en fait des tokens valides).
Solution : coupe-circuit 5 echecs consecutifs -> pause 5min ; retry cible unique apres 8s sur no_data uniquement (jamais en boucle, jamais sur une vraie panne reseau deja couverte ailleurs) - services/goplus.py, momentum_entry.py (commits 284f5946 / fc4291d3)

------------------------------------------------------------

[DEPLOYE] Sujet    : Reordonnancement pipeline + auto-blacklist honeypots confirmes
Date : 2026.07.21 / Probleme : le check honeypot GoPlus (ressource la plus rare/limitee du pipeline momentum) tournait en 2e position, avant tous les filtres gratuits (liste noire, liquidite, volume, age, profil, concentration) - gaspillait des appels sur des candidats de toute facon rejetes gratuitement.
Solution : honeypot deplace en dernier, juste avant l'OHLCV ; tout honeypot CONFIRME (jamais une simple indisponibilite) transfere automatiquement vers momentum_blacklist.py pour ne plus jamais redepenser un appel sur ce contrat - momentum_entry.py (commit 40a86db6d932)

------------------------------------------------------------

[DEPLOYE] Sujet : suspension de quota mensuel corrigee (18/08) + mecanisme AUTOMATIQUE pour toute future exhaustion
Date : 2026.08.10 / Probleme : GOPLUS_QUOTA_SUSPENDED_UNTIL (constante codee en dur, commit 0392725a du 05/08) exigeait un edit de code + commit + deploiement a chaque fois que la vraie date de renouvellement devait etre corrigee -- meme friction operationnelle que l'ancien bypass manuel holder-concentration (.env), trouvee le meme jour en etendant cette automatisation a un deuxieme mecanisme manuel du code. Date corrigee de 08-16 a 08-18 (confirmation operateur directe).
Solution : nouveau module `goplus_quota_suspension.py` -- detecte un VRAI signal de rate-limit (HTTP 429 ou le code GoPlus 4029 deguise en HTTP 200, jamais une panne reseau generique qui reste geree par le disjoncteur existant de GoPlusClient) sur 3 echecs consecutifs, s'auto-arme, se desarme au premier succes reel. Different du cas Blockscout (panne infra de quelques heures) : un quota CU mensuel peut rester mort plusieurs JOURS -- fenetre de suspension a backoff exponentiel (12h initial, double a chaque sonde post-expiration encore en echec, plafonne a 48h) plutot que de sonder chaque appel. La constante manuelle reste en place jusqu'au 18/08 (dernier correctif manuel necessaire) ; au-dela, le mecanisme automatique prend seul le relais pour toute future exhaustion. — nouveau fichier `goplus_quota_suspension.py`, `services/goplus.py` (`_get_json` verifie le mecanisme auto en plus de la constante manuelle, nouvelle methode `_record_rate_limit_for_auto_suspension`), nouveau `test_goplus_quota_suspension.py` (6 tests : pas-suspendu-initialement, sous-le-seuil-jamais-arme, atteint-le-seuil-arme-une-fois, succes-reel-desarme-et-reset, sonde-post-expiration-double-le-backoff, backoff-jamais-au-dela-du-plafond) + 3 tests d'integration dans `test_goplus.py` (armement automatique via de vrais code 4029 repetes, court-circuit sans appel reseau une fois arme, succes reel desarme) + 3 tests existants du disjoncteur generique neutralises pour ce nouveau mecanisme (testaient deja des 429 repetes, tombaient desormais dans le champ du nouveau seuil avant d'atteindre celui du disjoncteur classique).
