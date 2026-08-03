# HANDOFF — Opérations VPS (git, déploiement, worktrees, dispatch)

> **Repo PUBLIC — jamais d'IP/secret/token/clé/adresse email personnelle en clair ici.** Noms de variables OK (ex. `GOPLUS_APP_KEY`), leurs valeurs jamais.

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

[ETAT ACTUEL] Sujet    : `git push origin <nom-de-branche>` pousse la branche locale de ce nom, pas HEAD
Date : 2026.07.12  /  Probleme : un commit fait sur `main` local suivi de `git push origin <autre-branche>` est parti vers cette autre branche au lieu de `main` — `origin/main` n'a jamais bougé, sans erreur ni avertissement visible.
Solution : Toujours pousser avec un refspec explicite (`git push origin main:main` ou `HEAD:main`), jamais `git push origin <nom>` seul ; revérifier après coup via `git fetch origin main && git show origin/main:<fichier>` — cf. historique git 12/07.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : `origin` d'une session VPS peut pointer vers le mauvais dépôt sans erreur visible
Date : 2026.07.12 vers 13  /  Probleme : une session VPS a rapporté un push "réussi" (`git ls-remote` positif) vers une branche qui n'existait pourtant pas sur le bon dépôt — son `origin` pointait en réalité vers un autre repo du même écosystème (toutes ses commandes git étaient cohérentes... avec le mauvais dépôt).
Solution : Vérifier `git remote -v` en cas de doute, ou faire confirmer par la session commandement via l'API GitHub (indépendante du proxy git local) ; tout dispatch qui cible un chemin précis (ex. `docs/aria-learning-inbox/`) doit nommer explicitement le dépôt cible, jamais le laisser implicite — cf. historique git 12-13/07.

------------------------------------------------------------

[DEPLOYE] Sujet    : Déploiement blue-green + autoheal (rollback quasi instantané)
Date : 2026.07.13  /  Probleme : un déploiement cassé (health-check en échec) causait un downtime le temps de corriger, aucun mécanisme de retour arrière rapide.
Solution : `deploy.sh` bascule en blue-green (alternance de port, nouveau conteneur health-checké pendant que l'ancien tourne encore, nginx ne bascule qu'après succès) + `willfarrell/autoheal` avec disjoncteur maison (plafond 3 redémarrages/10min) — vanguard/deploy.sh / vanguard/scripts/autoheal-circuit-breaker.sh (cf. historique git 13/07).

------------------------------------------------------------

[DEPLOYE] Sujet    : Vérification post-déploiement trop rapide après reload nginx
Date : 2026.07.13  /  Probleme : `deploy.sh`/`deploy-vitrine.sh` tiraient un curl immédiatement après `systemctl reload nginx` — reload pas instantané (workers mettent un court instant à tourner), donc échec systématique et rollback automatique malgré un déploiement sain.
Solution : Boucle `retry_until` (~10s de plafond) avant de conclure à un échec — deploy.sh / vanguard/deploy_vitrine_lib.sh (cf. historique git 13/07).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Suppression de branche distante bloquée par le proxy de session, pas le classifieur
Date : 2026.07.13  /  Probleme : `git push origin --delete <branche>` sur une branche déjà fusionnée à l'identique échoue en HTTP 403 ("non autorisé par la politique de l'organisation") — action structurellement impossible depuis une session cloud, même sur du contenu sans risque.
Solution : Faire supprimer la branche par l'opérateur directement sur l'interface GitHub (icône corbeille) — cf. historique git 13/07.

------------------------------------------------------------

[CODE] Sujet    : Contention `.git/index.lock` à isolation par worktree concurrente
Date : 2026.07.13  /  Probleme : chaque worktree Claude Code a son propre index, mais certaines opérations git (refs, packing) touchent quand même le `.git/` partagé — à 5+ agents concurrents sur la même machine, contention intermittente sur `.git/index.lock`. Un `git commit` qui échoue sur ce lock, suivi d'un nettoyage automatique de worktree (non-interactif `-p`, cas des sessions VPS), peut détruire un travail non commité de façon permanente.
Solution : Committer tôt et souvent dans chaque worktree — aucun correctif officiel confirmé livré côté Claude Code à cette date (ticket amont `anthropics/claude-code#55724`) — cf. historique git 13/07.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Tâche cron programmée ne se déclenche pas si la session VPS reste active
Date : 2026.07.12  /  Probleme : un job de vérification programmé n'a jamais tourné — la session VPS était restée active sans interruption sur un autre travail, et ce type de tâche ne se déclenche qu'en session inactive.
Solution : Vérifier manuellement en cas de doute plutôt que de compter sur le déclenchement automatique d'une session qui pourrait rester active — cf. historique git 12/07.

------------------------------------------------------------

[DEPLOYE] Sujet    : Nouveaux modules absents de la liste curatée de tests en CI
Date : 2026.07.08  /  Probleme : 9 modules livrés la même nuit (relay_chat, relay_conversation, knowledge_inbox, sepolia_wallet, sepolia_autonomous, exam, btc_cycles, code_proposal, skill_projects) avaient chacun leur fichier de test mais n'étaient pas listés dans .github/workflows/ci.yml — une régression sur l'un d'eux serait passée inaperçue.
Solution : les 9 fichiers de test ajoutés à la liste curatée de la CI — .github/workflows/ci.yml (cf. historique git 08/07)

------------------------------------------------------------

[DEPLOYE] Sujet    : Disque VPS saturé par le cache Docker jamais purgé
Date : 2026.07.09→11  /  Probleme : deploy.sh ne nettoyait jamais les images/cache de build après un déploiement, disque monté à 79,8% (images 35GB + build cache 31,7GB, 90% récupérable) — nettoyage manuel one-shot 80%→11% le 10/07, cause racine non corrigée à ce moment-là.
Solution : docker image prune -f + docker builder prune -f ajoutés à la fin de deploy.sh, exécutés UNIQUEMENT après confirmation du health check réussi (jamais en cas d'échec/rollback) — vanguard/deploy.sh (cf. historique git 11/07)

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : VPS dispose d'un accès SSH écriture aux 7 repos GoldenFarFR
Date : 2026.07.11  /  Probleme : une session Claude Code sur le VPS ne pouvait travailler que sur le repo courant, dépendait d'un poste Windows local pour les autres repos de l'écosystème (ARIA, aria-ops, aria-core, template-grok-cursor, aria-acp-showcase, acp-cli-demos, GoldenFarFR).
Solution : 7 deploy keys SSH dédiées (une par repo, aucune partagée, toutes en écriture) configurées sur le VPS — détail complet et alias ~/.ssh/config dans aria-ops/runbooks/vps-github-access.md (repo privé)

------------------------------------------------------------

[CONFIG] Sujet    : Claude Code a fait ajouter des clés API dans le mauvais fichier .env (jamais lu par le déploiement)
Date : 2026.07.23  /  Probleme : `find /opt/aria -maxdepth 2 -iname ".env*"` (profondeur limitée par réflexe, sans vérifier contre `deploy.sh`) a renvoyé `/opt/aria/.env` — pris pour LE fichier .env sans jamais confirmer que c'est celui réellement chargé au déploiement. 4 clés (TWITTERAPI_IO_KEY/MONI_API_KEY/ZERION_API_KEY/CABALSPY_API_KEY) + 1 gate y ont été ajoutées sur plusieurs échanges — absentes du conteneur après déploiement, donc invisibles pour tout le code. `/opt/aria/.env` contenait en plus, à l'insu de tous au moment de l'ajout, d'anciennes variables déjà présentes ailleurs (ARIA_BRAIN_ENABLED, ARIA_VISION_ENABLED, ARIA_WALLET_SCORING_ENABLED, COINGECKO_DEMO_API_KEY, etc.) — un `cat /opt/aria/.env >> vanguard/backend/.env` correctif a donc aussi dupliqué CES variables préexistantes dans le vrai fichier de prod, risque réel d'écraser silencieusement une bonne valeur par une ancienne (heureusement toutes les valeurs dupliquées se sont révélées identiques après vérification manuelle, aucune régression réelle).
Solution : `deploy.sh` charge `ENV_FILE="${ARIA_ENV_FILE:-$REPO_DIR/vanguard/backend/.env}"` — **le seul fichier .env qui compte est `vanguard/backend/.env`, jamais `/opt/aria/.env` à la racine**. `/opt/aria/.env` renommé en `.env.a_supprimer_si_tout_va_bien` (jamais supprimé directement, réversible) pour ne pas induire une future session en erreur. Réflexe à graver : avant de donner un chemin `.env` à l'opérateur, vérifier `deploy.sh`/`ENV_FILE`, jamais un simple `find` à profondeur limitée pris pour argent comptant.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : `docs/HANDOFF-2026-07-17.md` supprimé, ne jamais le recréer
Date : 2026.07.17  /  Probleme : —
Solution : ancien fichier par-date (référencé par une session antérieure au format HANDOFF-par-composant) supprimé, son contenu réparti dans les `docs/HANDOFF_<COMPOSANT>.md` par sujet — ne jamais recréer ce fichier ni le chercher, il n'existe plus par design (cf. règle « HANDOFF par composant, pas par date »).

------------------------------------------------------------

[ETAT ACTUEL] Subject  : `docker restart aria-api` NEVER reloads a changed `.env` -- only a real `deploy.sh` run does
Date : 2026.07.27 / Problem : real incident -- `GROK_API_KEY` broke (old key deleted by the operator on the x.ai dashboard), a new key was generated and written into `/opt/aria/vanguard/backend/.env` (twice, after a first mismatch), then `docker restart aria-api` was run to apply it -- the container came back healthy, but a live API test kept returning 403 "disabled" on the OLD key, identical across two separate restarts. Root cause: `deploy.sh` starts the real container via `docker run --env-file "$ENV_FILE" ...` (blue-green, new container each deploy) -- environment variables are baked into a container at `docker run`/`create` time, never re-read from disk on a plain `docker restart` (that only restarts the existing process inside the SAME already-created container, with its already-loaded env). Editing `.env` on disk has zero effect until the container is actually recreated.
Solution : no code change (this is Docker's own documented behavior, not an ARIA bug) -- purely an operational lesson, gravé here to never be relearned the hard way again: after ANY `.env` change (a key rotation, a new flag), always run the real `vanguard/deploy.sh` (creates a fresh `aria-api-next` container with the current `.env`, health-checks it, then swaps via nginx) -- `docker restart <container>` is only appropriate for transient in-process issues, never for applying a changed environment variable. Verified live: after running `deploy.sh` (new container, commit `71ceaa376c22` confirmed serving), the exact same `GROK_API_KEY` that returned 403 across two restarts returned 200 immediately.

------------------------------------------------------------

[ETAT ACTUEL] Sujet : Protocole "Dispatch VPS" (multi-VPS Principal/Secondaire/Research)
Date : 2026.08.03 / Probleme : —
Solution : Protocole complet conservé ici tel quel pour réactivation future (extrait de
CLAUDE.md, passe de compaction du 03/08) — plus de dispatch multi-VPS depuis le 03/08
(décision opérateur explicite, une seule machine/une seule session, cf. section "Faits
établis" de CLAUDE.md). Si un jour réactivé, reprendre intégralement ce qui suit :

**Dispatch VPS (session cloud « commandement », 11/07, complété 12/07) — règle permanente, ne jamais oublier.** Toute consigne destinée à un VPS (Principal/Secondaire/Research) doit TOUJOURS être formatée : en-tête coloré hors bloc (🟠 **Pour VPS Principal :** / 🔵 **Pour VPS Secondaire :** / 🟣 **Pour VPS Research :**) suivi d'un bloc de code (```) contenant le texte exact à coller — jamais en texte normal, même pour une simple confirmation ou un "vas-y". Le bloc de code déclenche le bouton copier natif du chat ; sans lui l'opérateur doit sélectionner le texte à la main. Se relire avant d'envoyer tout message qui mentionne une prochaine étape pour un VPS. Incident vécu (11/07) : plusieurs consignes envoyées en texte simple, l'opérateur a dû relancer manuellement, VPS Research est resté à l'arrêt en attendant un dispatch jamais réellement formaté/envoyé. **Trois rappels obligatoires dans CHAQUE bloc dispatché (décision opérateur explicite, 12/07 ; 3e ajouté 13/07 après un deuxième incident du même type)** : (1) auto-identification — le VPS doit commencer son prochain rapport par `[VPS Principal]`/`[VPS Secondaire]`/`[VPS Research]` (oublié une fois par Research le 12/07) ; (2) autorité de commit — seule la session cloud commit/pousse sur `main`, le VPS prépare et pousse uniquement sur une branche temporaire dédiée ; (3) **push exclusivement via `scripts/safe-push.sh <ARIA|aria-ops> <nom-de-branche>`, jamais `git push origin ...` à la main** — le script (livré 13/07) vérifie lui-même que le remote local correspond bien au dépôt visé avant de pousser (refus bloquant et visible sinon) et pousse toujours vers une URL explicite, jamais l'alias `origin`. Exemple à coller dans le dispatch : `bash scripts/safe-push.sh ARIA claude/mon-sujet-temp`. Un alias `origin` mal configuré rendait un push "réussi" totalement silencieux sur le mauvais dépôt (vécu le 12/07 : VPS Research sur `aria-ops` au lieu d'`ARIA`) — le script rend cette classe d'erreur impossible plutôt que de compter sur la mémoire d'un agent pressé. Ces trois rappels vont dans le bloc de code lui-même (pas seulement en préambule hors bloc), pour survivre au copier-coller tel quel.

**Précision importante (13/07, deuxième incident distinct du premier, pas la même cause)** : un rapport Research annonçait une note `docs/aria-learning-inbox/` poussée avec succès -- introuvable côté commandement dans un premier temps, mais PAS un mensonge ni un remote cassé : le remote `origin` de cette session Research pointait correctement vers `aria-ops` (son dépôt de travail habituel, validé), et le commit y était réellement présent (confirmé via `git ls-remote`). Le vrai problème : `docs/aria-learning-inbox/` est un chemin qui vit dans **ARIA**, pas dans `aria-ops` -- une consigne qui demande d'écrire dans ce dossier doit donc TOUJOURS préciser explicitement `ARIA` comme dépôt cible dans le dispatch, jamais supposer que le remote par défaut d'une session VPS correspond au bon dépôt pour CE livrable précis.

**Ligne d'objectif DANS CHAQUE bloc dispatché (décision opérateur explicite, 12/07 ; corrigée 16/07 -- l'objectif doit être DANS le bloc de code, pas seulement après)** : chaque bloc de code collé à un VPS doit contenir sa propre ligne "Objectif : ..." (brève, explicite), pas seulement une ligne récapitulative après le(s) bloc(s) dans le message hors-bloc.

**Mode Plan avant exécution sur chaque VPS (décision opérateur explicite, 12/07)** : avant d'envoyer une nouvelle tâche à un VPS, l'opérateur bascule la session cible en mode **"Plan"** (`Shift+Tab`). Le dispatch doit demander explicitement d'élaborer un plan sans exécuter. Le VPS renvoie son plan à l'opérateur, qui le relaie au commandement pour relecture avant tout « go ».

**La relecture d'un plan VPS doit être une vraie relecture critique, pas un tampon (décision opérateur explicite, 14/07).** Chercher activement de vrais trous techniques (effets de bord d'une généralisation, ex. 14/07 — généraliser un contrôle anti-wash-trading d'UN token à TOUS les tokens d'un wallet cassait silencieusement l'exclusion du pool/routeur DEX).

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Exception de gouvernance "GitHub propre" — portée et élargissements successifs
Date : 2026.07.10→11  /  Probleme : —
Solution : décision opérateur explicite du 10/07 accorde à Claude Code le dernier mot sur le seul périmètre "GitHub propre, automatisé et cohérent" (code mort, docs qui dérivent, garde-fous mécaniques), sans redemander avant chaque suppression/correction dans ce périmètre. Élargie le 11/07 à tous les repos GoldenFarFR + suppression de branches/fermeture de PR orphelines (contenu déjà fusionné ailleurs, "ahead 0" vérifié) — toujours gatée par le classifieur de sécurité de session (nom explicite de la cible exigé). Ne s'étend jamais aux garde-fous (permission_mode/wallet_guard/regles-uniques/config.toml), au capital réel, ni aux opérations git destructives — cf. CLAUDE.md "Règles absolues".

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Incident fondateur "session cloud n'a pas d'accès VPS direct" — affirmation jamais revérifiée
Date : 2026.07.17  /  Probleme : CLAUDE.md affirmait à plusieurs reprises (11/07 au 16/07) qu'une session cloud n'a pas d'accès réseau direct au VPS — jamais recontrôlé après le constat du 08/07 documentant pourtant déjà l'inverse pour une session tournant depuis /opt/aria. Plusieurs jours de dispatch VPS et de contournements (endpoints diagnostic dédiés) ont potentiellement été bâtis sur une prémisse jamais revérifiée.
Solution : un simple docker ps/pwd/curl 127.0.0.1 le 17/07 a confirmé que la limite ne s'appliquait pas à cette session — a mené à la norme "Vérifier avant d'affirmer, systématiquement" gravée dans CLAUDE.md (Règles absolues) et à la levée du dispatch multi-VPS le 03/08.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Dérive de la pratique HANDOFF (par date puis par composant) — 12/07 à 22/07
Date : 2026.07.12→22  /  Probleme : la pratique HANDOFF (alors par date) était active et respectée du 07/07 au 11/07, puis s'est arrêtée net après le 11/07 — les 11 jours suivants (12/07 au 22/07) sont partis directement et intégralement dans CLAUDE.md, portant le fichier à 5358 lignes (~600 Ko) avant qu'une compaction complète ne soit nécessaire pour rattraper le retard.
Solution : format HANDOFF-par-composant gravé le 22/07 (décision opérateur explicite) — une entrée = 3 lignes, écrite AU MOMENT MÊME du correctif, jamais différée ; `docs/HANDOFF-2026-07-17.md` (dernier fichier par-date) supprimé le 17/07, contenu réparti par sujet.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Règle "repo full français" (22/07) inversée par "repo content en anglais" (23/07)
Date : 2026.07.22→23  /  Probleme : le 22/07, une fausse alerte (libellés français vus sur une capture Telegram, introuvables dans le code réellement déployé — en fait une traduction d'affichage côté client Telegram, code source resté anglais) a mené à trancher "Claude Code reste full français dans ce repo", écartant explicitement une suggestion inverse le même jour.
Solution : position inversée le 23/07 après clarification du scope réel (repo public lu par une audience externe non-francophone vs conversation opérateur, qui reste en français) — nouveau code/commentaires/docstrings/commits/entrées CLAUDE.md/HANDOFF en anglais depuis le 23/07 ; historique antérieur non traduit rétroactivement — cf. CLAUDE.md "Règles absolues".
