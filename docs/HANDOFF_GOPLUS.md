# HANDOFF — GoPlus (Token Security API, honeypot check)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

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

[DEPLOYE] Sujet    : Vraie structure de facturation GoPlus trouvee (facture par token, pas par appel)
Date : 2026.07.21 / Probleme : calibrage initial du throttle base sur un test empirique en rafale (blocage a la 11e requete) sans comprendre pourquoi - le dashboard reel revele un palier gratuit 150 CU/min, 15 CU par token EVM verifie, soit 10 req/min reelles.
Solution : throttle recalibre a 6.667s (90% de 10/min) ; bug d'auth corrige au passage (mauvais nom d'en-tete access-token au lieu de Authorization: Bearer, l'endpoint restait tolerant donc le bug etait invisible) - services/goplus.py (commit 40a86db6d932)

------------------------------------------------------------

[DEPLOYE] Sujet    : Coupe-circuit reactif + retry cible sur honeypot no_data
Date : 2026.07.21 / Probleme : aucun coupe-circuit sur pannes GoPlus repetees ; un verdict honeypot_unavailable propre mais vide (no_data) n'etait jamais retente alors qu'il correspondait le plus souvent a un simple delai d'indexation (quasi tous les faux negatifs re-testes juste apres etaient en fait des tokens valides).
Solution : coupe-circuit 5 echecs consecutifs -> pause 5min ; retry cible unique apres 8s sur no_data uniquement (jamais en boucle, jamais sur une vraie panne reseau deja couverte ailleurs) - services/goplus.py, momentum_entry.py (commits 284f5946 / fc4291d3)

------------------------------------------------------------

[DEPLOYE] Sujet    : Reordonnancement pipeline + auto-blacklist honeypots confirmes
Date : 2026.07.21 / Probleme : le check honeypot GoPlus (ressource la plus rare/limitee du pipeline momentum) tournait en 2e position, avant tous les filtres gratuits (liste noire, liquidite, volume, age, profil, concentration) - gaspillait des appels sur des candidats de toute facon rejetes gratuitement.
Solution : honeypot deplace en dernier, juste avant l'OHLCV ; tout honeypot CONFIRME (jamais une simple indisponibilite) transfere automatiquement vers momentum_blacklist.py pour ne plus jamais redepenser un appel sur ce contrat - momentum_entry.py (commit 40a86db6d932)
