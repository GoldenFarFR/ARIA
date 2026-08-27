# HANDOFF — Blockscout (holders, wallet scoring, données de contrat)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[DEPLOYE] Sujet    : Panne infra confondue avec absence légitime de donnée
Date : 2026.07.15  /  Probleme : une panne GeckoTerminal transitoire figeait un token "sans prix" pour toujours dans le scan incrémental
Solution : distinction explicite panne transitoire (retenté au prochain passage) vs absence légitime de donnée — smart_money.py (cf. historique git 15/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Crédits Pro épuisés (402), bloquait le pipeline momentum
Date : 2026.07.20  /  Probleme : holder_concentration indisponible dès que les crédits Blockscout Pro sont à sec
Solution : repli automatique permanent vers l'endpoint gratuit dès détection d'un 402 avec clé Pro encore configurée — blockscout.py (6e540db0)

------------------------------------------------------------

[DEPLOYE] Sujet    : Repli x402 payant ajouté en dernier recours
Date : 2026.07.21  /  Probleme : le chemin gratuit/Pro peut échouer sur les deux à la fois
Solution : blockscout_x402.get_token_holders_x402 (0,002$/appel) — chemin gratuit/Pro toujours tenté en premier, coût seulement si les deux échouent — blockscout_x402.py (216762a8)

------------------------------------------------------------

[DEPLOYE] Sujet    : Champ `token_address` toujours `None` (mauvais nom de champ API)
Date : 2026.07.14  /  Probleme : `_parse_token_transfer` lisait `token.get("address")`, mais l'API Blockscout v2 renvoie le champ sous `address_hash` — l'analyse smart-money tournait à vide silencieusement depuis sa construction initiale, invisible car tous les tests mockaient déjà le mauvais nom de champ.
Solution : Champ corrigé + tests re-mockés sur le vrai schéma. Norme de process actée : tester tout nouveau client d'API externe contre un VRAI appel (curl sur le VPS) avant de le considérer terminé, jamais faire confiance à un mock auto-cohérent — services/blockscout.py (commit `85e4c16`).

------------------------------------------------------------

[DEPLOYE] Sujet    : Blockscout Pro a sec (402) ne repliait jamais vers l'endpoint gratuit
Date : 2026.07.20 / Probleme : le client decide une seule fois a la construction s'il utilise la Pro API (cle presente) ou l'endpoint gratuit (cle absente) - une cle configuree mais a sec (credits epuises) faisait echouer TOUTE requete au lieu de retomber sur le gratuit, pourtant fonctionnel pour Base.
Solution : _get_json detecte un 402 avec cle Pro encore active + chaine Base -> bascule permanente (pour la duree de vie du process) vers l'endpoint gratuit et retente la meme requete - services/blockscout.py (cf. historique git 20/07)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Debit et couts Blockscout reels
Date : 2026.07.21 / Probleme : confusion possible sur le statut du debit ARIA (5 req/s) - suppose payant a tort.
Solution : 5 req/s = palier gratuit AUTHENTIFIE (100K credits/jour, sans CB) - les vrais paliers payants (49$/199$ par mois) ne servent qu'a un debit bien superieur (15/30 req/s). Blockscout expose aussi un point Pro payable a l'appel via x402 (holders enrichis avec labels d'entite, ~0,002$/appel, timeout de reglement 28-45s - pas 12s par defaut) - services/blockscout_x402.py (cf. historique git 21/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Quota Blockscout Pro epuise par le wallet-scoring (rescan 13 chaines/passage)
Date : 2026.07.16  /  Probleme : smart_money.py (wallet-scoring, #157) re-scannait les 13 chaines supportees a chaque passage de rattrapage pour un wallet actif (~5460 credits/wallet), epuisant le quota Blockscout Pro (100k credits) plusieurs fois par jour, alors qu'aucune fonction de trading ne consommait ce signal multi-chaines.
Solution : DEFAULT_SCAN_CHAINS() court-circuite en Base uniquement (_BASE_ONLY_OVERRIDE=True), classement TVL multi-chaines conserve dans le code mais inactif tant que non leve — smart_money.py (commit a75acef65a89)

------------------------------------------------------------

[DEPLOYE] Sujet    : Suivi proactif du budget de crédits Pro (débit ≠ budget cumulé)
Date : 2026.07.22  /  Probleme : le throttle "90% de la capacité" (5 req/s → 4,5 req/s) protège du rate-limit instantané, mais aucun mécanisme ne suivait le budget CUMULÉ (100 000 crédits/jour, 20 crédits/appel standard, sourcé via la doc officielle Blockscout) — seul le repli RÉACTIF sur un vrai 402 déjà reçu existait, jamais de prévention.
Solution : nouveau `blockscout_credit_budget.py` (même patron que `x402_budget.py`, fenêtre calendaire journalière minuit UTC, plafond dur 90 000/jour, append-only) — `_get_json` vérifie `can_spend()` AVANT même de tenter un appel Pro sur Base et bascule proactivement vers l'endpoint gratuit si le budget est sur le point d'être épuisé ; chaque appel Pro réussi enregistre sa consommation. Le repli réactif sur 402 reste en place comme filet de sécurité — services/blockscout.py, services/blockscout_credit_budget.py (cf. historique git 22/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Coût réel par endpoint corrigé (token-transfers = 30 crédits, pas 20) + vraie source de consommation identifiée
Date : 2026.07.22  /  Probleme : le budget ci-dessus supposait 20 crédits/appel pour tout endpoint (doc officielle générique) — un relevé RÉEL du dashboard opérateur montre que `token-transfers` (sur `/transactions/:hash/` ET `/addresses/:address_hash/`) coûte en fait 30 crédits/appel (357810/11927 et 203460/6782). Ces deux endpoints représentent à eux seuls 73,6% de toute la consommation du mois (561 270/762 850 crédits) — et ils ne sont PAS appelés par le pipeline momentum (qui n'utilise que holders/tokens/smart-contracts), mais par le WALLET-SCORING (historique de transferts d'un wallet, `smart_money.py`).
Solution : `cost_for_endpoint(path)` (mapping par suffixe, 30 pour token-transfers, 20 par défaut) remplace le coût uniforme partout (vérification proactive ET enregistrement). Fenêtre de renouvellement réelle observée sur le même relevé : ~12h glissantes depuis épuisement, pas minuit UTC pile — `day_start()` reste une approximation documentée comme telle, jamais présentée comme exacte. **Le vrai levier pour réduire la pression sur ce budget était donc côté wallet-scoring, pas côté momentum** — mécanisme retiré entièrement le 25/08 (décision opérateur, cf. `docs/HANDOFF_X402.md` entrée 25/08), ce levier n'a plus d'objet — services/blockscout.py, services/blockscout_credit_budget.py (cf. historique git 22/07)

------------------------------------------------------------

[DEPLOYE] Subject : duplicate calls on the same contract -- plumbing fix + shared short-TTL cache on the singleton
Date : 2026.08.11 / Problem : backlog #93 targeted a candle_history-style "persisted Blockscout history", but an Explore audit (read-only research, real call graph) found a different, more immediate gain : (1) a real plumbing bug -- `acp_onchain_scan._resolve_dev_behavior` (VC crible) never received the `holders=` parameter added on 28/07 to `gather_dev_wallet_facts` for exactly this purpose, while `_resolve_insider_wallets` right below it already reuses it correctly -- redoing `get_token_holders` for the SAME contract within the SAME VC evaluation ; (2) `get_address_info` called separately by those same two functions for the same token_address ; (3) no shared cache between the independent callers of the `blockscout_client` singleton (momentum_entry, VC crible, `paper_trader_risk`'s 15min post-entry invalidation recheck), whereas `_check_holder_concentration` already has its own local 300s cache and `holder_concentration_cache.py` its own 7-day verdict cache, both disjoint from everything else.
Solution : (1) direct fix -- `_resolve_dev_behavior(ctx, token_address, holders=None)` now forwards `holders` to `gather_dev_wallet_facts`, guaranteeing zero network call saved on this call site. (2) short-TTL cache (60s, same doctrine as `momentum_entry._security_cache`/`_pair_snapshot_cache`) directly in `BlockscoutClient` (`_cache_get`/`_cache_set`, key `(method, address.lower())`) for the 6 methods proven duplicated or duplicable : `get_address_info`, `get_token_metadata`, `get_token_holders`, `check_contract_flags`, `get_verified_source`, `read_owner`. Covers EVERY caller of the singleton without touching them one by one. **An `available=False` result is never cached** (trap found while testing : it broke the existing circuit breaker, which needs to SEE every real consecutive failure to open -- same doctrine as `holder_concentration_cache.py`, just applied here to a short TTL instead of 7 days).
`skills/acp_onchain_scan.py`/`services/blockscout.py`/`tests/test_acp_onchain_dev_behavior.py` (new, 3 tests)/`tests/test_blockscout_client.py` (+5 cache tests). Full suite green (9906 passed, 17 skipped). Deployed (commit `b339bbc1`), confirmed live in the current codebase (27/08 review).
