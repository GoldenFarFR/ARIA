# HANDOFF — Sécurité (secrets, accès, CI, rotations)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[CODE] Subject : ACP trade path silently fell back to acp-cli's DEFAULT slippage -- founding 09/07 rule violated, now fail-closed
Date : 2026.08.07 / Problem : found by the #259 proactive audit (never triggered by an incident) on the 3 real-capital-critical files never re-audited since they were written. Full chain verified end to end before fixing: `acp_client_actions.py` builds the `trade_tokens` payload with token_in/token_out/amount_in only -- no `slippage` key ever; `wallet_guard._exec_trade_tokens` read `payload.get("slippage", "")` -> always empty; `acp_cli.trade_tokens` guards with `if slippage.strip():` -> `--slippage` NEVER appended; acp-cli therefore applied ITS OWN DEFAULT. That is exactly the founding 09/07 incident (ETH->USDC swap defaulting to 30%, entry further down this file) whose rule is carved in CLAUDE.md's absolute rules: "slippage never above 10%, always explicit, never a trade tool's default value". Latent, not active: verified live that the prod container cannot reach acp-cli at all (`which acp` fails, `APPDATA` empty -> `run_acp` returns exit 127 before any network call), so no real spend could have gone out from the VPS -- the exposure is the local Windows machine where acp-cli is installed and the private key actually signs.
Solution : `_exec_trade_tokens` now FAIL-CLOSED -- returns a blocking reason instead of executing. Deliberately blocks rather than forcing a value: acp-cli's real `--slippage` unit (percent vs fraction vs bps) is unverified (the flag has never been passed, no test/doc in the repo records it, and the package is absent from the VPS so it cannot be checked here) -- guessing "10" against a tool expecting a fraction would mean 1000% slippage, strictly worse than the bug being fixed. Project doctrine on an unverifiable value is fail-closed + state why, never invent precision. Does NOT import `agent_wallet_pilot.MAX_SLIPPAGE_BPS`: that pilot is structurally separate from this shared guardrail (locked by `test_coherence`), both modules answer to the same CLAUDE.md rule, never to each other. Invariant locked by a new `test_acp_trade_never_falls_back_to_the_tools_default_slippage` using AST inspection (a plain substring check false-positived on the fix's own docstring, which quotes the faulty pattern to explain it) -- verified the guard genuinely catches the pre-fix code (flagged line 40) rather than passing vacuously. `wallet_guard.py` / `test_coherence.py` (`a9c02a29`). 138 tests green on coherence+wallet_guard; full aria-core suite 9351 passed / 8 failed, the 8 confirmed pre-existing by re-running them with the change stashed (Telegram menu-command alphabetical ordering + scalping_v9, unrelated files). Operator gave explicit approval before touching this guardrail file (absolute rule: never modify a guardrail without an explicit "ok"), and the session was switched to Opus for the red-zone pass per the model policy. To re-open the path: verify acp-cli's real unit, then pass an explicit validated value -- never via a `.get(..., "")` that degrades to the default again.

------------------------------------------------------------

[DEPLOYE] Subject : Generic mechanical guard for every ARIA_*_ENABLED gate (Item #176)
Date : 2026.07.31 / Probleme : 64 distinct ARIA_*_ENABLED gates exist across aria-core, each protected ad hoc (if at all) in test_coherence.py -- this file's own history recorded ARIA_BONDING_DISCOVERY_ENABLED and ARIA_CABALSPY_SOURCING_ENABLED as OFF in CLAUDE.md while both were actually ON in prod, caught only by a manual 5-agent audit (24/07), never mechanically.
Solution : same pattern as `_EXTERNAL_WRITE_ALLOWLIST` (Item, 10/07) -- new `_KNOWN_ENABLED_GATES` registry (all 64 current gates) + `test_all_enabled_gates_registered_in_known_gates` scans every `.py` file for `ARIA_[A-Z_]*_ENABLED` and fails if a new one appears undeclared. Honest scope: proves a gate is KNOWN and DECLARED, cannot prove its live prod value (CI has no access to the deployed `.env`, still requires a real "verifier avant d'affirmer" check) -- test_coherence.py.

------------------------------------------------------------

[DEPLOYE] Subject : TTL/expiry on stale pending approvals (Item #175)
Date : 2026.07.31 / Probleme : a pending approval (`aria_core.approvals`, shared by wallet_guard's ACP spend escalation, agent_wallet_monitor alerts, and marketing_video review) could sit undecided forever if the admin never clicked the Telegram prompt -- safe on its own (`wallet_guard.escalate_spend` never spends without a real click), but a decision made days/weeks later would act on stale context (an old swap quote, a since-changed config). `ApprovalStatus.EXPIRED` already existed in the schema but was never used.
Solution : new `approvals.expire_stale_pending()` (24h TTL, never auto-approves/executes -- just closes the entry, a fresh escalation is needed if still wanted) + `run_expiry_cycle()` (notifies the admin per expired entry, best-effort) wired to a new always-on heartbeat task `approval_ttl_cycle` (60min, no ARIA_*_ENABLED gate needed -- pure safety cleanup, same reasoning as `xai_balance_monitor_cycle`) -- approvals.py, heartbeat.py, test_approvals.py (10 new tests).

------------------------------------------------------------

[DEPLOYE] Sujet    : Item #232 -- CI secret-scan casse en boucle (2 bugs distincts, pre-existants)
Date : 2026.07.30 / Probleme : signalement operateur en direct (captures d'ecran de checks GitHub Actions en echec repete) -- le job `detect-secrets (baseline diff)` echouait systematiquement sur TOUT push/PR, sans rapport avec le contenu reellement pousse. Deux causes distinctes trouvees via l'API GitHub Actions (jamais devine) : (1) 7 findings jamais audites dans `.secrets.baseline` depuis l'ajout des tests Phase 1 App mobile (mots de passe/TOTP factices dans test_operator_account.py/test_operator_mobile_routes.py, adresses Ethereum publiques dans test_basenames.py) + 1 finding de cette session (market_slug Polymarket, test_polymarket_paper_trader.py) -- tous verifies manuellement comme faux positifs avant regeneration. (2) le step separe "Scanner les emails hors liste blanche" (incident #139) detectait `git@github.com` (syntaxe SSH generique, PAS une adresse personnelle -- ironiquement present dans le texte de CE MEME fichier qui RACONTE l'incident du 13/07) et `mobile/assets/icon.png` (un email fantome genere par du bruit d'octets binaires, `read_text(errors="ignore")` decodant silencieusement un PNG au lieu d'echouer).
Solution : `.secrets.baseline` regenere (procedure documentee dans secrets-scan.yml). `check_no_personal_email.py` : `git@github.com` ajoute a `ALLOWLISTED_EMAILS` avec la raison en commentaire ; nouvelle exemption par EXTENSION binaire (`_BINARY_EXTENSIONS`, png/jpg/ico/woff/pdf/zip/etc.) plutot qu'une exception au cas par cas -- couvre tout futur asset binaire, pas seulement ce PNG precis. — .secrets.baseline/check_no_personal_email.py, 2 nouveaux tests dedies (13 total), suite complete verte.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Repos annexes volontairement archivés sur GitHub, jamais désarchivés
Date : 2026.07.18  /  Probleme : —
Solution : `template-grok-cursor` et `aria-acp-showcase` restent archivés (décision opérateur) en plus du `GITHUB_TOKEN` PAT fine-grained scopé au seul repo `ARIA` et de la protection de branche `main` (entrées ci-dessous) — ne jamais les désarchiver/supprimer sans consigne opérateur explicite.

------------------------------------------------------------

[DEPLOYE] Sujet    : Token Telegram + cle Blockscout Pro fuyaient en clair en continu dans les logs prod
Date : 2026.07.24 / Probleme : root logger INFO (main.py, fix legitime du 16/07 pour rendre visibles les logger.info() applicatifs) faisait aussi logger httpx (utilise par python-telegram-bot + tous les clients internes) -- chaque requete Telegram (token dans le chemin d'URL) et Blockscout Pro (apikey en query-string) fuyait en clair dans docker logs. Meme classe que l'incident du 16/07 (traite alors comme rotation de secret, jamais la cause racine) -- confirme reactif sur les NOUVEAUX secrets (8 lignes token Telegram + 127 lignes cle Blockscout sur 12h reelles). Trouve par l'audit 5-agents du 24/07.
Solution : logging.getLogger("httpx").setLevel(logging.WARNING) juste apres le basicConfig -- ferme la fuite pour tous les clients HTTP (Telegram, Blockscout, futur Alchemy) sans toucher la visibilite des logger.info() applicatifs. Garde-fou mecanique ajoute (test_httpx_logger_silenced_below_info) - vanguard/backend/app/main.py (commit a suivre)

------------------------------------------------------------

[DEPLOYE] Sujet    : CI scan de secrets rouge en continu
Date : 2026.07.17  /  Probleme : main et toutes les branches VPS échouaient le job secrets-scan — 5 valeurs factices de test absentes du baseline
Solution : vérifiées une à une (aucun vrai secret), baseline régénéré, diff audité entrée par entrée (841 lignes, 100% expliquées) — .secrets.baseline

------------------------------------------------------------

[CONFIG] Sujet    : Secret affiché en clair pendant un diagnostic
Date : 2026.07.17  /  Probleme : LLM_FALLBACK_API_KEY (clé Groq de secours) affichée en clair dans une sortie d'outil, jamais dans le chat
Solution : rotation recommandée par précaution — pas confirmée faite (à revérifier)

------------------------------------------------------------

[CONFIG] Sujet    : Clé GoPlus exposée deux fois (env | grep)
Date : 2026.07.21  /  Probleme : GOPLUS_APP_KEY/SECRET affichées en clair via `docker exec env | grep`, deux fois le même jour — cause probable de la rotation de secret côté GoPlus qui a cassé l'authentification (cf. docs/HANDOFF_GOPLUS.md)
Solution : réflexe à généraliser — vérification de présence (grep -q) jamais affichage de la valeur

------------------------------------------------------------

[CONFIG] Sujet    : Clé GoPlus exposée une 3e fois, même session (22/07)
Date : 2026.07.22  /  Probleme : docker exec printenv GOPLUS_APP_KEY affiché en clair pendant le diagnostic du bug d'authentification — même erreur répétée malgré la leçon déjà actée le 21/07
Solution : rotation recommandée — clé déjà connue comme non-critique (lecture seule, pas de mouvement de fonds), mais hygiène à refaire

------------------------------------------------------------

[DEPLOYE] Sujet    : Clé privée wallet Virtuals exposée en dur
Date : 2026.07.09  /  Probleme : `skills/development/connect.ts` contenait la vraie clé privée du wallet agent Virtuals "Aria Vanguard ZHC" (mainnet) codée en dur, malgré une référence trompeuse à baseSepolia.
Solution : Code corrigé pour lire `process.env`, rotation Virtuals confirmée (nouvelle clé active avant suppression de l'ancienne) — connect.ts (cf. historique git 09/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Délégation autonome "Cursor" trouvée vivante, retirée
Date : 2026.07.10  /  Probleme : `aria_worker_queue.py`/`community_worker_skill.py` permettaient une délégation externe autonome hors du périmètre validé, jamais désactivée.
Solution : Code et narratif (`directives.md`) retirés/réécrits, garde-fou mécanique ajouté (`test_coherence.py::test_external_write_actions_registered_in_allowlist` — toute fonction qui écrit à l'extérieur doit être déclarée dans une allowlist explicite) — cf. historique git 10/07.

------------------------------------------------------------

[DEPLOYE] Sujet    : Faux positifs scanner de secrets CI
Date : 2026.07.13  /  Probleme : CI rouge systématique sur `main` depuis le merge de #60 — valeur factice de test non enregistrée au baseline `.secrets.baseline`, et un commentaire `git@github.com` (syntaxe SSH) matchait la regex du scanner PII.
Solution : Baseline regénérée (une seule addition exacte, zéro suppression, validation opérateur avant modification d'un fichier garde-fou), commentaire reformulé — cf. historique git 13/07.

------------------------------------------------------------

[CONFIG] Sujet    : `GITHUB_WRITE_REPOS` confirmé désactivé en prod
Date : 2026.07.11  /  Probleme : point en attente depuis plusieurs sessions sur l'état réel de ce flag après l'incident Cursor.
Solution : Vérifié directement dans le `.env` du conteneur `aria-api` sur le VPS — valeur `off` confirmée, valeur par défaut du template durcie de `*` à `off` — production.env.example (aria-ops).

------------------------------------------------------------

[DEPLOYE] Sujet    : Endpoints de diagnostic distants, token dédié
Date : 2026.07.15  /  Probleme : besoin de lire l'état du pool de sourcing et le journal agent-wallet depuis une session sans accès filesystem direct au VPS, sans réutiliser le secret admin.
Solution : `GET /api/aria/diagnostics/pool-status` et `/agent-wallet-ledger` gatés par un token dédié `ARIA_DIAGNOSTIC_TOKEN` (header `X-Diagnostic-Access`, distinct du secret admin et du token relay) — pire cas de fuite = lecture seule d'un journal, jamais une validation de dépense — vanguard/backend/app/api/routes/aria.py (cf. historique git 15/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : /whoami exposait la liste réelle des admin_ids à tout visiteur
Date : 2026.07.15 / Probleme : handler Telegram orphelin (jamais enregistré via add_handler, reliquat probable créé hors flux git normal) câblé par nécessité (seule voie pour qu'un visiteur non reconnu retrouve son ID Telegram) - la branche VISITEUR renvoyait settings.admin_ids (la vraie liste des IDs admin) à n'importe qui tapant /whoami.
Solution : un visiteur ne voit plus que son propre ID Telegram - la branche admin reste inchangée (déjà exposée par construction à un admin confirmé) - telegram_bot.py (cf. historique git 15/07, #181)

------------------------------------------------------------

[DEPLOYE] Sujet    : Aucun garde anti-injection sur l'écriture en mémoire vectorielle (memory poisoning)
Date : 2026.07.18 / Probleme : audit du chemin d'écriture LanceDB a trouvé deux trous - cybercentry_insight.py écrivait directement sans aucun triage (0 appelant en prod mais aucune garantie pour demain) ; le triage Groq existant (x_insight_relevance.py) vérifiait pertinence et véracité mais jamais l'injection de prompt spécifiquement.
Solution : lancedb_store.contains_injection_marker() - garde regex FR+EN posée à la couche de PERSISTANCE elle-même (store()), protège tout appelant présent ET futur sans les modifier individuellement ; x_insight_relevance.py gagne un 5e critère INJECTION dans le même prompt Groq (aucun nouvel appel LLM), prime sur PERTINENT/FAIT si détecté - lancedb_store.py / x_insight_relevance.py (cf. historique git 18/07, #206)

------------------------------------------------------------

[DEPLOYE] Sujet    : Vulnerabilite axios transitive (GHSA-xj6q-8x83-jv6g) via @coinbase/cdp-sdk
Date : 2026.07.20 / Probleme : axios 1.16.0 epingle transitivement par @coinbase/cdp-sdk (via @wagmi/connectors -> @base-org/account) dans vanguard/ et template-grok-cursor/, vulnerable (config.auth lu sans garde hasOwnProperty).
Solution : override npm vers axios 1.18.1+ (meme mecanisme deja utilise pour ws/uuid) plutot que d'attendre la mise a jour du pin amont - vanguard/package-lock.json (commit 347cebe743dd)

------------------------------------------------------------

[CONFIG] Sujet    : Export de cle privee wallet CDP reel - procedure sure
Date : 2026.07.21 / Probleme : besoin ponctuel d'exporter la cle privee du wallet agent CDP (capital reel) sans jamais l'exposer a une session Claude Code ni elargir les pouvoirs de la cle API de prod.
Solution : cle API CDP TEMPORAIRE creee avec le seul scope Export (jamais ajoute a la cle de prod "ARIA", qui n'a jamais eu ce scope), utilisee une fois via un script dedie sur le VPS lisant tout depuis l'environnement d'appel, supprimee immediatement apres usage. La cle privee du wallet elle-meme n'a jamais transite par une session Claude Code - cf. historique git 21/07

------------------------------------------------------------

[CONFIG] Sujet    : Secrets affiches en clair via grep/docker logs non filtres (Blockscout Pro, Telegram, Etherscan)
Date : 2026.07.16  /  Probleme : BLOCKSCOUT_PRO_API_KEY exposee 3 fois (grep brut sur .env, docker logs non filtre x2), TELEGRAM_BOT_TOKEN expose via une URL de log en clair, cle Etherscan V2 montree en capture d'ecran — diagnostics de panne menes sans precaution.
Solution : rotation Blockscout+Telegram confirmee par l'operateur (18/07) ; Etherscan V2 restee inerte (aucun code ne la lit) donc risque non actif. Reflexe grave : ne jamais grep/cat/docker logs un fichier contenant un secret sans filtre — toujours une verification de presence silencieuse (grep -q) — cf. historique git 16/07

------------------------------------------------------------

[CONFIG] Sujet    : Rotation GITHUB_TOKEN neutralisée par une ligne dupliquée dans .env
Date : 2026.07.18  /  Probleme : GITHUB_TOKEN était défini deux fois dans le .env (nouvelle valeur en tête, ancien token OAuth large plus bas) — la dernière occurrence l'emporte dans un fichier .env, donc l'ancien token serait resté actif malgré l'ajout du nouveau PAT scopé si la ligne dupliquée n'avait pas été supprimée.
Solution : ligne dupliquée supprimée (sed ciblé sur le préfixe de l'ancien token), nouveau PAT fine-grained (scope repo ARIA seul, Issues/PR lecture-écriture, Contents/Metadata lecture seule, expiration 90j) vérifié par un vrai appel API avant ET après révocation de l'ancien token OAuth — cf. historique git 18/07.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Protection de branche main activée sur GitHub
Date : 2026.07.18  /  Probleme : force-push et suppression de branche possibles par n'importe qui (opérateur, sessions VPS) sur main, sans garde-fou.
Solution : protection de branche activée via l'UI GitHub (le classifieur de sécurité de session refuse l'action via API) — force-push et suppression bloqués pour tout le monde ; "PR obligatoire avant merge" volontairement PAS activé (casserait le push direct des sessions). Vérifié via l'API (branches/main -> protected: true).

------------------------------------------------------------

[DEPLOYE] Sujet    : URL non sanitisée d'un projet externe atteignait le prompt système Telegram
Date : 2026.07.19  /  Probleme : une URL "Site officiel" déclarée par un projet scanné (donc potentiellement attaquant-contrôlée) était ajoutée BRUTE à process_trail, propagée jusqu'à build_trade_status_context() puis splicée sans balise <donnees_non_fiables> dans le prompt système Telegram — violation du mandat anti-injection au dernier maillon de la chaîne, alors que le reste du pipeline était déjà sanitisé.
Solution : sanitisation systématique à la SOURCE (nouvelle fonction _trail_note(), plus aucun trail.append() brut) + défense en profondeur au POINT D'INJECTION (build_trade_status_context() enveloppe tout le bloc dans <donnees_non_fiables> + sanitize_untrusted_text) — conviction_research.py/brain.py (commit 100b2087).

------------------------------------------------------------

[DEPLOYE] Sujet    : Lecture de fichier arbitraire + comparaison de secret non constante
Date : 2026.07.07  /  Probleme : un endpoint backend permettait une lecture de fichier arbitraire (path traversal) et le secret opérateur était comparé par une comparaison de chaînes classique (vulnérable à une attaque temporelle).
Solution : chemin d'accès validé/restreint au dossier autorisé + comparaison à temps constant (secrets.compare_digest) — vanguard/backend (commit fc863c9)

------------------------------------------------------------

[DEPLOYE] Sujet    : Webhook Telegram + secret admin + rate-limit durcis
Date : 2026.07.08  /  Probleme : webhook Telegram fail-open si TELEGRAM_WEBHOOK_SECRET vide (kill-switch forgeable par n'importe qui), secret opérateur accepté en query-string (fuite via logs/Referer), champ `handle` du corps permettant l'usurpation d'un rôle opérateur, rate-limit contournable via l'en-tête client X-Visitor-Id.
Solution : webhook fail-closed (secret exigé, comparaison à temps constant), secret opérateur header-only (plus de ?secret=), anti-usurpation du champ handle, rate-limit plafonné par IP réelle (--proxy-headers) — vanguard/backend (commit edff6b1)

------------------------------------------------------------

[DEPLOYE] Sujet    : Simulation d'attaque quotidienne (security_sim)
Date : 2026.07.08  /  Probleme : aucune vérification automatisée récurrente de la surface HTTP réelle contre des entrées hostiles — un corps de requête non-UTF8 faisait planter n'importe quel endpoint POST en 500.
Solution : vanguard/backend/security_sim/ (introspection des routes + fuzzing, baseline différentiel) + workflow GitHub Actions quotidien 03:17 UTC ; handler RequestValidationError corrigé dans main.py pour absorber un corps non-UTF8 — commit 99e5fe7, toujours actif (.github/workflows/security-sim.yml)

------------------------------------------------------------

[DEPLOYE] Sujet    : ID Telegram opérateur committé en clair dans des fichiers exemple
Date : 2026.07.08  /  Probleme : TELEGRAM_ADMIN_IDS (identifiant Telegram réel de l'opérateur) committé en clair dans vanguard/operator/local.env.example, production.env.example et un test — contraire à la doctrine « zéro PII dans le repo public », pas exploitable seul mais PII réelle exposée.
Solution : remplacé par un champ vide / placeholder générique (123456789 pour le test) — vanguard/operator/local.env.example, production.env.example (cf. historique git 08/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Garde-fou anti-fuite IP/email limité à un seul fichier HANDOFF en dur
Date : 2026.07.08  /  Probleme : test_coherence.py ne vérifiait la fuite IP/email que dans docs/HANDOFF-2026-07-07-nuit.md — 4 nouveaux fichiers HANDOFF existaient déjà sans être couverts par ce garde-fou.
Solution : scan généralisé par glob (docs/HANDOFF-*.md) au lieu d'un nom de fichier en dur — packages/aria-core/tests/test_coherence.py (cf. historique git 08/07)

------------------------------------------------------------

[CONFIG] Sujet    : Clé Tavily exposée en clair, collée par erreur dans le chat opérateur
Date : 2026.07.09  /  Probleme : la clé TAVILY_API_KEY de l'opérateur a transité en clair dans la conversation (collée par erreur) pendant le câblage du provider de recherche web.
Solution : traitée comme compromise immédiatement, jamais écrite en fichier/commit/log ; opérateur a régénéré une nouvelle clé sur tavily.com avant de la poser dans le .env VPS (cf. historique git 09/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Garde anti-IP étendu au code Python, pas seulement aux docs
Date : 2026.07.09  /  Probleme : test_no_public_ip_in_human_docs ne couvrait que les docs Markdown — une vraie IP de VPS et une vraie clé CoinGecko se sont glissées dans une fixture de test Python, ni detect-secrets (une IP n'est pas un « secret » classique) ni le check existant ne l'ont repérée.
Solution : nouveau test_no_public_ip_in_source_or_tests étend le garde-fou au CODE (src+tests), zéro faux positif vérifié avant activation, .secrets.baseline régénéré — test_coherence.py (cf. historique git 09/07)

------------------------------------------------------------

[CONFIG] Sujet    : Clé privée encodée dans un NOM de fichier, hors repo
Date : 2026.07.09  /  Probleme : deux fichiers trouvés hors repo (dossier home Windows de l'opérateur) avec une clé privée EC en base64/PKCS8-DER collée directement dans le NOM du fichier (reliquat probable de l'incident connect.ts) — visible dans un simple listing de dossier, sans même l'ouvrir.
Solution : fichiers supprimés via un joker sur le préfixe du nom (le nom exact retranscrit depuis une capture ne correspondait pas caractère pour caractère) — réflexe à généraliser : un secret peut fuiter via le NOM d'un fichier, pas seulement son contenu (hors repo, aucun commit — cf. historique git 09/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Tentative d'injection de prompt dans le README d'un repo adjacent (aria-core)
Date : 2026.07.11  /  Probleme : le repo privé aria-core (nom réservé pour extraction future de packages/aria-core/) contenait un README rédigé à la 2e personne comme une instruction directe à une IA ("explique comment tu brancherais un nouveau skill GitHub dans aria-core") — tentative d'injection non exploitée mais présente dans un repo que des sessions futures pourraient lire.
Solution : non exécutée, signalée, README remplacé par une description neutre — leçon actée : grep/lire aussi les README des repos adjacents lors d'un audit d'écosystème, pas seulement le code — aria-core/README.md (cf. historique git 11/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Fichier vault (goldenfar-vault.gfv) resté tracké dans git malgré une exclusion voulue
Date : 2026.07.11  /  Probleme : deux lignes de négation dans .gitignore du repo aria-ops (!sync/vault/, !sync/vault/*.gfv) neutralisaient l'exclusion du dossier vault/, laissant un fichier vault tracké dans l'historique git depuis le premier commit du repo.
Solution : git rm --cached (fichier gardé sur disque) + suppression des 2 lignes de négation sur décision opérateur explicite ("aucune donnée sensible sur GitHub" prime sur le choix de design précédent) ; reste dans l'historique passé (réécriture d'historique explicitement différée, sujet séparé) — aria-ops/.gitignore (commit 9212f42, repo aria-ops)

------------------------------------------------------------

[DEPLOYE] Sujet    : Nom réel opérateur codé en dur dans une regex fonctionnelle
Date : 2026.07.11  /  Probleme : un grep exhaustif demandé par l'opérateur (le nom réel ne doit jamais apparaître publiquement) a trouvé deux usages FONCTIONNELS, pas seulement documentaires : brain._routing_message (deux regex de détection de préfixe de pont Cursor/KART construites avec le nom réel en dur dans le pattern) et relay_conversation._history_message (nom réel écrit en dur comme label [<nom>] dans l'historique envoyé au LLM).
Solution : nouveau champ de config aria_operator_display_name (défaut générique "Operator", vraie valeur uniquement dans le .env réel du VPS, jamais commise) lu par les deux fonctions via getattr(...) or "Operator" — comportement identique une fois configuré, jamais le nom réel dans le code source — vanguard/backend/app/config.py, aria_core/testing.py, brain.py, relay_conversation.py (commits 59f7ed1d/193d4711)

------------------------------------------------------------

[DEPLOYE] Sujet    : Pre-commit gitleaks scan + full-history secret audit
Date : 2026.07.23  /  Probleme : two secret-leak incidents this session (Claude Code's own Bash commands displaying real .env values in visible output) prompted an operator request to audit the whole repo and add a mechanical guard, not rely on discipline alone.
Solution : full-history gitleaks scan (2443 commits) found 329 matches, all verified false positives (`.secrets.baseline` SHA1 hashes, public wallet addresses in test fixtures, one fabricated JWT in a security-sim attack corpus) -- no real secret ever committed to this repo. New `.gitleaks.toml` (allowlists the two verified-safe sources) + `scripts/pre-commit-secret-scan.sh` (versioned logic, scans only the staged diff via `gitleaks protect --staged`, fails closed if gitleaks itself is missing) + `.git/hooks/pre-commit` stub (unversioned, same pattern as the existing pre-push devils-advocate hook) -- tested against a real fake token (blocked) and a normal change (passed) before relying on it. gitleaks binary installed at `/usr/local/bin/gitleaks` on this VPS only -- the hook is local to this clone, not synced to other VPS/machines. Separately: CDP_WALLET_SECRET/GITHUB_TOKEN/TELEGRAM_BOT_TOKEN/STRIPE_SECRET_KEY/ANTHROPIC_API_KEY (exposed via a full .env diff notification) and Blockscout Pro/Etherscan/a Sepolia testnet private key (16/07 incident) still need operator-side rotation -- unrelated to this repo audit, tracked separately.

------------------------------------------------------------

[DEPLOYE] Sujet    : Automatic, immediate kill-switch on unexpected_outflow (Item #198)
Date : 2026.07.29  /  Probleme : the prior design (Item #187) only armed the kill-switch AFTER the operator answered a Telegram confirmation -- "PAS autorise" armed it, "Autorise par moi" was a no-op -- meaning nothing was actually blocked for however long the operator took to reply to a possible key compromise.
Solution : `agent_wallet_monitor.run_agent_wallet_monitor_cycle` now arms `outgoing_pause.pause()` immediately on detecting an unexpected_outflow, before sending the Telegram alert; never re-arms (or overwrites the reason) if already paused; notification now fires regardless of pause state; lifting requires the OWNER specifically AND a matching tx_hash in the current pause reason. New append-only `kill_incident_log.py` (every arm/lift, manual or auto) closes the gap where `pause_state.json` only ever held one overwritten snapshot -- agent_wallet_monitor.py, gateway/telegram_bot.py, kill_incident_log.py (commit fcf4698e)

------------------------------------------------------------

[DEPLOYE] Subject  : Founding slippage incident -- ETH->USDC swap defaulting to 30%
Date : 2026.07.09 / Problem : a swap ETH->USDC (a liquid pair, external trade tool) had a default slippage of 30% -- would have accepted a result ~$10 worse than necessary for no reason, no explicit parameter had been set before the call.
Solution : rule carved in stone by explicit operator decision (07/09) -- slippage never above 10%, always explicit, never a trade tool's default value; applies to every external trade tool used by ARIA (Arena Virtuals, future pilots) -- cf. CLAUDE.md "Absolute Rules".

------------------------------------------------------------

[CODE] Sujet    : Custody kill-switch split from the shared outgoing_pause flag (Item #62)
Date : 2026.08.03  /  Probleme : a real incident (08/02, a legitimate OpenRouter recharge misclassified as `unexpected_outflow`) auto-armed `outgoing_pause` -- the SAME flag `momentum_websocket.py`'s real-time drain checks before both processing pending limit orders and sourcing new candidates -- silently freezing ALL paper trading (100% fictional capital, CLAUDE.md's own absolute rule: "test pur, sans validation humaine") for ~5h15 (confirmed live: 422 momentum_scan_log rows/hour -> 0 for the whole window -> 80/hour after). Two independent adversarial workflows (verifying the current tradeoffs, and projecting onto the planned 3-Smart-Wallet architecture) converged on the same fix before any code was written.
Solution : new `custody_pause.py` (near-exact structural twin of `outgoing_pause.py`, own `custody_pause_state.json`, never the same file -- `outgoing_pause.py` stays untouched, flagged in CLAUDE.md as "kill-switch, teste -- ne pas recoder"). `agent_wallet_monitor.py`'s auto-arm now targets ONLY `custody_pause` (never `outgoing_pause`) -- the outflow_confirm Telegram callback (`gateway/telegram_bot.py`) resolves/lifts the SAME dedicated flag. The manual `/stop`/`/start`/`/resume` handlers keep writing to `outgoing_pause` unchanged AND now also lift `custody_pause` (the operator's single "halt/resume everything" lever stays simple, still covers paper trading and any stuck custody incident in one command). Real-money paths (`wallet_guard.py` x2, `agent_wallet_pilot.py` x2 swap+transfer, `agent_wallet_smart_swing.py`) now check BOTH flags -- either one blocks a real spend/swap/transfer; paper trading (`momentum_websocket.py`, `paper_trader.py`, `risk_guard.py`) checks only the manual flag, never `custody_pause`, by construction (no call site touches it). `/status` now reports the two states separately. Forward-compatible with the planned 3-Smart-Wallet split (scalping/swing/vc): today a single shared custody flag (n=1 real wallet); per-wallet scoping later is additive, not a re-architecture. `agent_wallet_monitor.py` / `gateway/telegram_bot.py` / `wallet_guard.py` / `agent_wallet_pilot.py` / `agent_wallet_smart_swing.py` / `custody_pause.py` (new) -- new `test_custody_pause.py` (12 tests incl. one reproducing the exact incident end-to-end: custody_pause armed, paper drain proven to keep running), 3 existing tests in `test_agent_wallet_monitor.py`/`test_telegram_kill_switch.py` updated to target the new flag, full suite green (8920 passed, 17 skipped).

------------------------------------------------------------

[CONFIG] Sujet    : Hook PreToolUse block-secret-display.sh retiré (décision opérateur, non documentée jusqu'ici)
Date : 2026.08.05 / Probleme : l'Avocat du Diable (revue post-push) a signalé la disparition silencieuse du hook `PreToolUse` -> `block-secret-display.sh` dans `.claude/settings.json`, remplacé par le gate-status-injector en SessionStart, jamais mentionnée dans un HANDOFF -- coïncidant avec l'auto-allow de `git commit`/`./vanguard/deploy.sh`, lu comme un signal de vulnérabilité potentiel par un lecteur externe.
Solution : retrait CONFIRMÉ délibéré par l'opérateur en session (raison donnée : "c'était de la merde, tu pouvais plus rien déployer avec" -- bloquait des déploiements légitimes). Aucun code changé ici, entrée purement pour que la prochaine session/revue externe trouve la trace de cette décision au lieu de la re-signaler comme une faille. Rappel opérateur (05/08, réaffiché) après une 3e fuite de secrets en session (docker inspect non filtré cette fois, pas seulement .env) : aucun garde-fou automatique ne remplace ce hook -- toute commande pouvant afficher des variables d'environnement de production doit être filtrée manuellement à chaque fois (cf. mémoire de session, jamais un `grep`/`env` brut sur un conteneur ou fichier de secrets).

------------------------------------------------------------

[CONFIG] Sujet    : Audit exposition attaque supply-chain npm keyv/cacheable (04/08) -- non touche, durcissement applique
Date : 2026.08.05 / Probleme : campagne "keyv-shai-hulud" du 04/08/2026 (compte du mainteneur jaredwray pirate, 2234 versions empoisonnees / 444 packages npm, hook preinstall setup.mjs telechargeant Bun, vol de tokens GitHub/npm/AWS, implants caches dans des fichiers de config d'agents IA type hooks Claude Code/VS Code) -- operateur a demande un audit complet du VPS.
Solution : NON TOUCHE, verifie point par point : keyv 4.5.4 installe le 30/07 (avant la fenetre d'attaque 04/08 09h35-13h18 UTC), aucun npm install depuis le 03/08 23h33, aucun IOC (pas de setup.mjs dans les node_modules, pas de Bun, pas de .vscode, hooks .claude/hooks/ tous commites et legitimes), aucun token GitHub volable (remote SSH par alias, pas de .git-credentials, pas de gh auth, pas de GITHUB_TOKEN en env), deploy-vitrine.sh utilise npm ci (lockfile sain du 30/07). Durcissement applique : `npm config set ignore-scripts true` global (bloque le vecteur exact -- preinstall hooks -- de cette campagne ; si un futur build vitrine casse sur un postinstall manquant, c'est ce reglage, le blue-green garde l'ancien build). Consigne quelques jours : pas de npm install/update tant que la campagne est active, les lockfiles actuels sont sains.

------------------------------------------------------------

[CODE] Sujet    : Verrouillage des dependances Python au build + HSTS nginx (suite audit npm keyv)
Date : 2026.08.05 / Probleme : symetrique exact de l'attaque npm du 04/08 cote PyPI -- les deux `pip install` du Dockerfile tiraient a chaque build LES DERNIERES versions publiees (planchers `>=` seuls, aucun lock) : une dependance compromise sur PyPI aurait ete installee silencieusement au prochain deploy.sh. Aussi : header HSTS absent des reponses nginx (vitrine + api), les navigateurs pouvaient etre forces en HTTP par un intercepteur.
Solution : nouveau `vanguard/backend/requirements-lock.txt` (132 versions figees par `pip freeze` du conteneur prod verifie sain) applique en CONTRAINTES `-c` aux deux pip install du Dockerfile -- les planchers `>=` restent le contrat SCA (deliberement), le lock borne le resolveur ; mise a jour du lock uniquement consciente apres upgrade teste. Build de validation complet OK (imports critiques verifies dans l'image de test). HSTS `max-age=31536000; includeSubDomains` ajoute aux deux server blocks 443 (`sites-enabled/vitrine` + `aria-api`, backups `/root/nginx-*.bak-20260805`), verifie live sur les deux domaines. Jail fail2ban nginx envisagee puis ecartee (gain marginal vs bruit de scan deja absorbe). `vanguard/Dockerfile` + `vanguard/backend/requirements-lock.txt` -- commit ci-dessous.
