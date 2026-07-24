# HANDOFF — Sécurité (secrets, accès, CI, rotations)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

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
