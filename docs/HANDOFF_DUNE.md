# HANDOFF — Dune Analytics (sourcing SQL, pièges de requête)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[CODE] Sujet    : get_price_history produisait ts=0 sur TOUTES les bougies -- format bucket réel jamais testé contre un vrai appel
Date : 2026.08.04 / Probleme : audit opérateur de la qualité des 7 providers OHLCV de la cascade (demande explicite : "teste chaque api elle repond toutes avec les meme information necessaire ou il y a une difference de qualite") -- test live sur WETH/base a révélé que Dune (dernier recours de la cascade) renvoyait 49/49 bougies avec ts=0. Cause : le format réel du champ `bucket` retourné par Dune est `"2026-08-02 19:00:00.000 UTC"` (suffixe texte " UTC", pas "Z" ni un offset "+00:00") -- le seul `.replace("Z", "+00:00")` du code ne matchait jamais ce format, `datetime.fromisoformat` levait une exception à chaque ligne, et l'exception attrapée retombait sur `ts=0` -- un timestamp fabriqué traité comme valide par tout appelant en aval (RSI/détection de trous/expiry dépendent tous des deltas de ts). Le test unitaire existant (`test_malformed_rows_skipped_not_a_crash`) utilisait un format bucket sans suffixe qui ne reproduisait jamais le vrai format -- jamais confronté à un vrai appel Dune depuis son écriture (norme de process déjà établie après l'incident Blockscout, pas appliquée ici).
Solution : strip du suffixe " UTC" avant le parsing ; en cas d'échec de parsing malgré tout, la ligne est désormais SKIPPÉE (jamais un ts=0 fabriqué) -- même doctrine fail-closed que le skip déjà existant sur open/high/low/close invalides. Test existant corrigé pour refléter le nouveau comportement (0 bougie au lieu de 1 avec ts=0) + nouveau test verrouillant le vrai format live.
`packages/aria-core/src/aria_core/services/dune.py`, `packages/aria-core/tests/test_dune_client.py` -- suite ciblée verte (197 tests). Impact pratique limité (Dune = dernier recours, rarement atteint) mais réel quand il l'est.

------------------------------------------------------------

[CODE] Sujet    : CTE token_launch classait à tort un token établi comme "vient de naître"
Date : 2026.07.15 / Probleme : build_early_buyer_multiple_query filtrait la fenêtre lookback_days dans le WHERE avant l'agrégat MIN(block_time) - un token établi depuis longtemps, dont le premier trade DANS la fenêtre tombait par hasard il y a lookback_days jours, était classé à tort comme un nouveau lancement, polluant le signal d'acheteurs précoces avec des acheteurs d'un token ancien en pleine remontée.
Solution : filtre de date déplacé du WHERE (pré-agrégat) au HAVING (post-agrégat) - la CTE scanne l'historique complet de dex.trades et ne garde que les tokens dont la PREMIÈRE transaction jamais vue tombe réellement dans la fenêtre récente - services/dune.py (cf. historique git 15/07)

------------------------------------------------------------

[CODE] Sujet    : Littéral varbinary vs varchar casse une requête Dune sur addresses.stats
Date : 2026.07.15 / Probleme : le champ address est de type varbinary dans addresses.stats (contrairement à dex.trades.taker qui est varchar) - un littéral entre guillemets simples échoue en exécution réelle ("Cannot find common type between varbinary and varchar"), une requête syntaxiquement valide en apparence mais qui plante à l'exécution.
Solution : émettre des littéraux hexadécimaux nus (0x...) au lieu de chaînes quotées pour toute colonne varbinary - vérifié en direct deux fois via le MCP Dune - services/dune.py (3ca1cdd)

------------------------------------------------------------

[CODE] Sujet    : peak_multiple aberrant (~10^22x) sur une requête de sourcing early-buyer
Date : 2026.07.15 / Probleme : division par launch_price_usd quasi-nul (dust trade probable sur le tout premier trade jamais vu d'un token, MIN() sur un seul point de mesure sans plancher de montant) - une requête syntaxiquement correcte (EXECUTE_SQL_LIMIT_1 seul ne l'aurait pas détecté) mais dont les valeurs de sortie sont inutilisables sans inspection réelle.
Solution : plancher amount_usd >= min_trade_usd (défaut 1.0) ajouté dans token_peak ET token_launch_price - ramène le pire cas de ~10^22x à ~1.65x10^7x (résultat encore élevé, en partie un phénomène réel de bonding curve, distinction non poussée plus loin) - services/dune.py (cf. historique git 15/07, #185)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Clustering Sybil (au-delà de la convergence pairwise) - pistes gratuites vérifiées, rien implémenté
Date : 2026.07.15 / Probleme : la plus grosse limite documentée de smart_money.py reste l'absence de clustering d'entité au-delà de la convergence pairwise entre 2-3 wallets soumis ensemble.
Solution : GraphSense vérifié négatif par lecture directe du code source (pas d'heuristique de clustering compte-EVM, seulement côté UTXO/Bitcoin) ; labels.owner_addresses.algorithm_name vérifié vide sur 52.4M lignes ; addresses.stats.first_funded_by + cex.addresses (Dune) restent les deux signaux gratuits exploitables ; Louvain+K-Core puis K-means fait-maison recommandé comme point de départ (Arkham/Webacy payants en secours) - cf. docs/aria-learning-inbox/2026-07-15-radar-sybil-clustering-entite-gratuit.md
