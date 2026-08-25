# HANDOFF — Audit livraison (composants qui n'ont jamais livré leur résultat attendu)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.
> Détail complet, méthode de mesure et chemin de reprise : `specs/001-audit-code-sans/`
> (spec.md/plan.md/research.md/audit-scope.md/tasks.md/quickstart.md). Ce fichier est la
> synthèse livrée, pas le journal de travail.

Origine (25/08) : le retrait du mécanisme wallet-scoring a montré qu'un composant peut
tourner des mois, consommer un budget de rate-limit réel, être recâblé ~50 fois, et
n'avoir JAMAIS produit son résultat attendu (un smart wallet qualifié) — sans qu'aucun
test ni revue de code ne le détecte, parce que le code faisait exactement ce pour quoi
il était écrit, juste pas ce pour quoi il avait été construit. Cet audit pose la même
question à 11 autres mécanismes actifs.

[ETAT ACTUEL] Sujet : x402 seller -- jamais de vente tierce réelle
Date : 2026.08.25 / Probleme : `ARIA_X402_SELLER_ENABLED`/`_MAINNET` actifs en prod, mais
`x402_revenue_log` (table complète) ne contient que 2 lignes depuis toujours -- même
adresse payeur, 3h d'écart le 05/08, lecture = un auto-test opérateur, jamais un client
tiers. Zéro vente en 20 jours. Zéro listing sur le x402 Bazaar confirmé en direct.
Solution : décision opérateur nécessaire -- investir dans la découvrabilité (listing
Bazaar, #297) ou mettre le gate mainnet en pause tant qu'aucun canal de découverte
n'existe. Pas d'action prise ici (audit read-only, capital/revenu réel).

------------------------------------------------------------

[ETAT ACTUEL] Sujet : CabalSpy sourcing -- orphelin depuis le retrait wallet-scoring, tourne encore
Date : 2026.08.25 / Probleme : `cabalspy_kol_wallets` contient 1183 wallets, tous d'un
UNIQUE sync du 20/08 (`sourced_at` identique), jamais répété depuis. Pourtant le cycle
heartbeat a encore tourné aujourd'hui (25/08 11:11), consommant son budget API chaque
jour. Zéro appelant réel de `catalogued_wallets()` en dehors du module lui-même --
exactement le schéma wallet-scoring, détecté cette fois en 5 jours au lieu de mois.
Solution : décision opérateur -- mettre `ARIA_CABALSPY_SOURCING_ENABLED` en pause tant
qu'aucun consommateur réel n'existe. Pas d'action prise ici.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : Polymarket paper trading -- livré, cadence désormais vérifiée
Date : 2026.08.25 / Probleme : CLAUDE.md notait la cadence/le volume réels comme "jamais
vérifiés". Table complète (9 positions, ever) : premier pari 30/07, dernier 22/08, 3
marchés distincts, contre 426 lignes dans `polymarket_judgment_log` (~2.1% de conversion
jugement->pari). 3 clôtures avec P&L réel vérifié dans le code (+100% chacune).
Solution : aucune -- cadence de ~2-3 paris/semaine cohérente avec la barre de
sélectivité élevée du design (probabilité>=0.85 + convergence 3 votes), pas un mécanisme
à l'arrêt. Ferme le gap CLAUDE.md.

------------------------------------------------------------

[CODE] Sujet : wallet_copy_shadow -- biais de survivance sur les clôtures sans prix de sortie
Date : 2026.08.25 / Probleme : `summary()` excluait silencieusement toute position closed
dont `exit_price_usd` était NULL (42% des 151 clôtures réelles, 64 lignes) du calcul de
`realized_pnl_usd`/`closed_positions` -- même schéma que le bug de survivance PnL du
17/08. Le signal "wallet à copier" repose aussi presque entièrement sur du PnL LATENT
(jusqu'à +176k$ non réalisé sur un wallet) plutôt que réalisé (~+933$ net sur 151
clôtures, tous wallets confondus). Constat annexe : `summary()` n'a AUCUN consommateur
(ni Telegram, ni heartbeat, ni API) -- le signal existe mais reste invisible.
Solution : `summary()` expose désormais `closed_unknown_exit_count` séparément (jamais
de prix inventé, mais plus jamais invisible). 2 nouveaux tests de régression (18 au
total dans le module, tous verts). Câblage d'un consommateur recommandé, pas fait ici.
— `wallet_copy_shadow.py`, `test_wallet_copy_shadow.py` (2a7efa57)

------------------------------------------------------------

[CODE] Sujet : signal cascade falsifiability -- verdict faussé par un outlier, corrigé (verdict final inchangé)
Date : 2026.08.25 / Probleme : `falsifiability_report()` décidait son verdict
("critère utile" vs "sans valeur") sur la moyenne BRUTE des retours à terme -- un seul
candidat rejeté avec un retour de +1 609 067% (probable artefact) gonflait la moyenne du
côté "rejected" à 38340% (7j), sans jamais appliquer le garde-fou statistique du projet
(retester sans le top 1-2 avant de conclure).
Solution : `avg_return_*_pct_no_top2` calculé et utilisé pour décider le verdict.
Recalcul manuel : rejected reste ~21-29% vs validated ~7-10% même sans outlier -- le
verdict natif du mécanisme ("pas mieux que le hasard, NE PAS transmettre à ARIA") tient
toujours, mais pour la bonne raison statistique. 1 nouveau test (30 au total, tous
verts). — `signal_cascade_convergence.py`, `test_signal_cascade_convergence.py` (074292f0)

------------------------------------------------------------

[ETAT ACTUEL] Sujet : candle_staleness_shadow -- données suffisantes, analyse jamais écrite
Date : 2026.08.25 / Probleme : 37 717 observations sur 15 jours (10/08-25/08), largement
assez pour calibrer un seuil réel (10,1% de taux de flag sur l'ensemble). Mais la passe
de "forward-validation" que le code promettait ("for the future forward-validation
pass") n'a jamais été écrite -- aucune corrélation entre `would_flag=1` et un vrai
problème de prix n'a jamais été mesurée.
Solution : écrire la passe de validation (croiser avec `wick_filter_shadow`/`ath_shadow`
sur le même contrat+horodatage) avant de pouvoir sortir du mode shadow. Pas fait ici
(hors périmètre read-only de cet audit), mais désormais une étape concrète et scopée.

------------------------------------------------------------

[CODE] Sujet : Sepolia autonomous pilot -- échec silencieux à chaque cycle, corrigé
Date : 2026.08.25 / Probleme : `sepolia_autonomous_log` contient 0 ligne, jamais, malgré
un cycle heartbeat actif chaque jour. Cause réelle : `anchor_enabled()`/`ledger_address()`
ne sont pas configurés, donc chaque cycle sort sur `skipped_no_ledger` -- AVANT tout
appel à `_insert_log`, contredisant le docstring de la fonction elle-même ("logs EVERY
round"). Aucun swap testnet n'a donc jamais été tenté malgré `ARIA_SEPOLIA_SWAP_ENABLED=true`.
Solution : `skipped_no_ledger` journalise désormais une vraie ligne (contrairement à
`skipped_paused`/`skipped_disabled`, des états OFF intentionnels et stables, laissés
inchangés). 1 nouveau test (25 au total, tous verts). Câblage réel de l'ancre à décider
par l'opérateur avant que ce pilote puisse revendiquer un swap testnet prouvé.
— `sepolia_autonomous.py`, `test_sepolia_autonomous.py` (7952fed0)

------------------------------------------------------------

[ETAT ACTUEL] Sujet : 3 gates wallet-scoring orphelins (`ARIA_WALLET_SCAN_QUEUE_ENABLED`, `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED`, `ARIA_SMART_MONEY_LEADERBOARD_ENABLED`)
Date : 2026.08.25 / Probleme : zéro référence dans tout code/test/doc vivant -- seulement
dans des sauvegardes `.env.bak*` historiques et un snapshot figé du 22/07. Pourtant les 3
variables restent définies (à `false`) dans le conteneur réel.
Solution : retrait recommandé du `.env` de prod au prochain déploiement (pur nettoyage,
aucun changement de comportement puisque rien ne les lit). Pas fait ici.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : daily_trade_floor -- fausse alerte de cet audit, mécanisme sain
Date : 2026.08.25 / Probleme : suspecté "non documenté" par le scope initial de cet audit
-- suspicion jamais vérifiée, corrigée ici. En réalité très documenté
(`HANDOFF_PAPER_TRADING.md`/`HANDOFF_PIPELINE_MOMENTUM.md`), gate OFF par conception
après sa fenêtre de test diagnostique (Item #100, fin juillet), dernier run 28/07.
Solution : aucune action -- a livré exactement ce pour quoi il a été construit pendant
sa fenêtre de test, correctement inactif depuis.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : ARIA_SCALPING_ONLY_SOURCING_ENABLED -- code mort déjà retiré, variable d'env orpheline
Date : 2026.08.25 / Probleme : le code qui lisait ce gate a été supprimé le 18/08 lors
du retrait scalping v1-v9 (déjà documenté). Seule la variable d'environnement Docker
traîne encore.
Solution : même nettoyage que les 3 gates wallet-scoring (T009), à regrouper dans le
même déploiement de nettoyage `.env`.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : Robinhood testnet rehearsal -- sain, bloqué par son gate parent
Date : 2026.08.25 / Probleme : 4 tentatives (24/08-25/08), toutes `blocked`, raison
identique et explicite : `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` désactivé (fail-closed par
défaut). Contrairement au cas Sepolia (T008), chaque tentative est proprement journalisée
-- ce n'est pas un bug, le module a 1 jour et n'a simplement encore jamais atteint sa
propre logique de rehearsal.
Solution : décision opérateur -- activer `ARIA_HOMEMADE_AGENT_WALLET_ENABLED` si le
burn-in continu reste voulu. Pas d'action prise ici (capital réel adjacent).

------------------------------------------------------------

[ETAT ACTUEL] Sujet : dip_recovery_shadow -- construit le 13/08, jamais activé
Date : 2026.08.25 / Probleme : trouvé lors du test de généralisation de la méthode
(T016, hors périmètre initial de cet audit) -- signal d'entrée proposé par l'opérateur
(-30%/24h + stop -5%), câblé dans le scheduler heartbeat, mais son gate dédié
`ARIA_DIP_RECOVERY_SHADOW_ENABLED` n'a jamais été activé : 0 ligne dans ses 2 tables,
aucune entrée jamais dans `heartbeat_state.json`. Ni un bug ni un orphelin post-retrait
-- simplement jamais mis en route depuis sa construction il y a 12 jours.
Solution : décision opérateur -- activer le gate si le test shadow reste voulu, ou
confirmer que c'est un abandon délibéré. Pas d'action prise ici.

------------------------------------------------------------

## Note incidente sécurité (25/08)

Pendant la vérification live du gate Sepolia (T008), une commande
`docker exec aria-api env | grep -i SEPOLIA` a affiché `ARIA_SEPOLIA_PRIVATE_KEY` en
clair dans le terminal -- violation de la règle absolue "jamais afficher un secret via
Bash", même s'il s'agit d'une clé testnet sans fonds réels. Non recopiée, non stockée,
non réutilisée ; signalée à l'opérateur en direct avec une recommandation de rotation
par précaution.
