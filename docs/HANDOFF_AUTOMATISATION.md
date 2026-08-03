# HANDOFF — Automatisation continue (cron VPS, veille, observabilité)

> **Repo PUBLIC — jamais d'IP/secret/token/clé en clair ici.**

> Format : `[STATUT] Sujet` / `Date : AAAA.MM.JJ / Probleme : ...` / `Solution : ... — fichier (hash)`.
> `[STATUT]` : DEPLOYE / CODE (testé, pas déployé) / CONFIG (pas de commit) / ETAT ACTUEL.

Regroupe l'historique (conception, premiers tests réels, incidents rencontrés et corrigés) des
mécanismes cron VPS indépendants listés dans CLAUDE.md ("Automatismes en place") : veille
continue Research, Avocat du Diable, promotion backlog, watchdog paper-trading 1M$, surveillance
santé logs. CLAUDE.md garde l'état opérationnel court (quel fichier, quel cron, quel gate) — ce
fichier garde le "comment on en est arrivé là" et les leçons apprises en route.

------------------------------------------------------------

[DEPLOYE] Sujet    : Veille continue automatisée "VPS Research" — conception et premier test réel
Date : 2026.07.18  /  Probleme : décision opérateur explicite ("h24 7/7, jamais deux fois la même, 50 idées/jour, tu les ajoutes au plan") de mettre en place une veille de recherche continue, indépendante de toute session/app ouverte.
Solution : cron VPS natif (initialement `0 */3 * * *`, `/opt/aria-data/research-loop/run.sh`) — `claude -p` headless, outils bridés en dur (`--allowedTools "WebSearch WebFetch Read Write"` + `--disallowedTools "Bash Edit Agent Task"`, aucune commande exécutable même sur injection de prompt). Cadence de démarrage volontairement prudente (3h, pas 2h) car ce VPS partage le même quota MAX 5x que le reste de l'ingénierie — le texte prévoyait déjà de la monter "une fois l'impact réel observé sur 1-2 jours". Premier test réel : 7 entrées propres en ~3 min, 0 outil interdit invoqué, dédoublonnage validé en conditions réelles (une piste déjà connue correctement écartée). Distinction actée avec l'opérateur (discussion croisée Gemini) entre Research (scout, pensée divergente, aucun ancrage code) et l'Avocat du Diable (critique convergente, bornée au diff — cf. entrée suivante) : les deux rôles ne fusionnent jamais dans le même agent.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Cadence veille Research relevée 3h→2h, jamais mise à jour dans CLAUDE.md
Date : 2026.08.03  /  Probleme : le crontab réel (`crontab -l`) tournait déjà à `0 */2 * * *` (2h, 12 passages/jour) alors que CLAUDE.md documentait encore "toutes les 3h (8 passages/jour)" avec un texte justificatif qui se contredisait lui-même ("cadence de démarrage volontairement prudente, pas 2h" alors que 2h était déjà la cadence réelle) — dérive de doc jamais rattrapée après un relevage de cadence.
Solution : `crontab -l` revérifié en direct le 03/08 (passe de compaction CLAUDE.md, étape 8/9) — confirmé `0 */2 * * *`. CLAUDE.md corrigé pour refléter la cadence réelle. Réflexe à graver : toute modification de cadence cron doit être accompagnée, dans le même geste, d'une mise à jour du texte CLAUDE.md qui la documente — sinon la doc dérive silencieusement comme ici.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Procédure réutilisable — audit workflow avant de compacter une section sensible de CLAUDE.md
Date : 2026.08.03  /  Probleme : —
Solution : lors de la passe de compaction du 03/08, une première tentative manuelle de compresser la section "Règles absolues" a silencieusement perdu 12 éléments substantiels (états par défaut de gates, verrous `test_coherence`, bornes chiffrées, une clause de sécurité sur une adresse codée en dur, un jalon futur) — une seule relecture humaine ne les a pas tous vus non plus avant qu'un workflow dédié les trouve. Procédure qui a fonctionné, réutilisable telle quelle pour toute future compaction d'une section sensible (garde-fous, capital réel) :
1. Un premier agent (`fetch-original`) lit le fichier réel et retourne le texte ORIGINAL verbatim de la section concernée — jamais de mémoire/résumé.
2. Un deuxième agent (`diff-audit`) reçoit l'ORIGINAL + la version PROPOSÉE dans son prompt, et audite élément par élément (chaque gate, chaque borne chiffrée, chaque nom de fichier/fonction cité) — consigne explicite : lister toute perte réelle avec citation des deux côtés, ou dire "aucune perte" si c'est le cas ; terminer par un verdict tranché SÛR À COMMITTER / CORRECTIONS NÉCESSAIRES.
3. Un troisième agent (`pointer-check`) vérifie que chaque nouveau pointeur `docs/HANDOFF_*.md` introduit par la compaction résout vers du contenu réel (lit les fichiers cibles), et propose une entrée HANDOFF prête à coller pour tout ce qui manque.
Coût : 2 agents pour l'audit + 1 pour le pointer-check (respecte le plafond de 2 agents/workflow en les regroupant en 2 phases, `Diff-audit`/`Pointer-check`, chacune sous ce plafond). Sur les points où l'audit trouve un vrai choix (pas juste une perte accidentelle) — ex. le périmètre exact d'une exception de gouvernance, ou un déclencheur qui contredisait une autre règle du texte source — l'agent doit explicitement dire "à faire valider par l'opérateur", jamais trancher seul en silence.

------------------------------------------------------------

[DEPLOYE] Sujet    : "Avocat du Diable" — critique architecturale post-push, conception et premier test
Date : 2026.07.18  /  Probleme : feu vert opérateur direct (après conception croisée avec Gemini) pour un critique automatique de chaque push sur `main`, distinct de la veille Research (pensée convergente, bornée au diff, pas de pivot).
Solution : hook `.git/hooks/pre-push` (stub non versionné) appelle `scripts/devils-advocate-review.sh` (versionné), envoie le diff en arrière-plan détaché à DeepSeek R1 via OpenRouter (modèle/lab différent de celui qui écrit le code), reçoit aussi une carte légère de `docs/aria-learning-inbox/` (noms de fichiers seulement). Rapport écrasé à chaque push (`/opt/aria-data/architect-report.md`, hors repo public), log technique séparé append-only. Échec de génération → marqueur `[ÉCHEC DE GÉNÉRATION DU RAPPORT]` explicite, jamais un contenu halluciné silencieux. Agent "Architecte" (relecture de plan avant codage) volontairement pas construit — tester d'abord la valeur du seul critique post-push.

------------------------------------------------------------

[ETAT ACTUEL] Sujet    : Mécanisme de "Routine" de l'app desktop ne peut pas atteindre le filesystem du VPS
Date : 2026.07.18  /  Probleme : deux tentatives (promotion backlog, watchdog paper-trading) via le mécanisme de tâche planifiée de l'app desktop échouaient systématiquement dès que le dossier ciblé était `/opt/aria` — confirmé par test opérateur, fonctionne seulement sur un dossier local du PC.
Solution : les deux routines ont été supprimées, remplacées par du cron VPS natif (indépendant de toute app/PC ouvert). Ce mécanisme desktop reste utilisable uniquement pour des tâches sans besoin d'accès filesystem VPS (pur appel HTTPS vers une API déjà déployée) — ne jamais lui donner `/opt/aria` comme dossier cible.

------------------------------------------------------------

[DEPLOYE] Sujet    : Promotion backlog automatisée — conception et premier test réel
Date : 2026.07.18  /  Probleme : `research-log.md` grossit en continu (cron veille ci-dessus) sans mécanisme pour promouvoir les trouvailles actionnables vers le backlog CLAUDE.md/aria-learning-inbox.
Solution : `scripts/research-log-promotion.sh`, cron VPS quotidien (9h UTC) — relit le journal depuis un marqueur de progression, juge chaque entrée avec un vrai esprit critique (vérifie les faits douteux par WebSearch), outils `Read Write Edit WebSearch WebFetch` + `Bash(git *)` uniquement (jamais de shell arbitraire). Premier test réel (15h47-15h54 UTC) : 4 items promus + 1 fiche, bien sourcés. **Incident réel observé et corrigé** : la session a décrit un de ses propres commits antérieurs comme une "exécution concurrente" — aucune preuve d'un second processus, explication la plus probable : perte du fil de ses propres actions après une compression de contexte interne (le prompt exige de lire l'intégralité de CLAUDE.md, potentiellement deux fois). A mené à une contradiction mineure (un item écarté puis re-promu dans la même session), résultat final correct. Prompt corrigé pour vérifier `git log` soi-même avant de conclure à une interférence externe.

------------------------------------------------------------

[DEPLOYE] Sujet    : Watchdog paper-trading 1M$ — conception et premier test réel
Date : 2026.07.18  /  Probleme : le test paper-trading 1M$ tourne en continu sans qu'aucune session ne surveille son état entre deux reprises de fil.
Solution : `/opt/aria-data/paper-watchdog/run.sh`, cron VPS toutes les 3h (décalé de 30min de la veille Research pour ne pas cogner sur le même quota). Destiné aux sessions Claude Code, pas à l'opérateur — aucune notification Telegram. Calcule équité/P&L approximatifs, signale toute position ouverte >24h sans thèse ou clôturée <24h sans notes de clôture, refuse de conclure sur la qualité du trading sous 20 trades clôturés. Écrit en append-only dans `watchdog-log.md`. Premier test réel : portefeuille encore vide, cohérent avec un cycle relancé ~3h30 plus tôt.

------------------------------------------------------------

[DEPLOYE] Sujet    : Surveillance santé logs production — conception et calibration
Date : 2026.08.03  /  Probleme : demande opérateur explicite après une vidéo sur les tests automatisés — la CI vérifie le CODE, `/api/health`/autoheal est une pure sonde de vivacité HTTP sans conscience des tâches heartbeat (angle mort confirmé par un workflow de validation).
Solution : `/opt/aria-data/log-health-watch/run.sh`, cron VPS horaire — scanne les vrais logs du conteneur `aria-api` depuis le dernier checkpoint, cherche `Traceback`/`CRITICAL` uniquement (délibérément pas un grep générique "error" : mesuré en direct sur 6h de logs réels, 19 occurrences génériques, toutes du bruit). Vérifie aussi `architect-review.log` (les 3 dernières tentatives du hook Avocat du Diable en échec = anomalie) — c'est ce 2e check qui aurait détecté une vraie panne de 30h de ce hook (compte OpenRouter à sec, corrigée le 02/08), invisible à un simple scan de `docker logs`. Notification Telegram réservée à l'anomalie. Validé par un workflow de conception : pas de LLM, pas de dashboard à niveaux — un projet à un seul opérateur avec un seul canal d'alerte n'a pas de lecteur pour un tableau de bord dédié.
