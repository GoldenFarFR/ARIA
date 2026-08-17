# Hooks changelog

Journal daté de tous les hooks du repo (git hooks + hooks Claude Code) — créé
le 07/08 sur demande opérateur ("tiens moi un fichier a jour avec les log des
modif de chaque hook pour plus tard par date"). Un hook = un mécanisme qui
s'exécute automatiquement à un événement (commit/push/session Claude Code) —
distinct des crons VPS (Research watch, Devil's Advocate promotion, watchdogs),
qui restent trackés dans `docs/HANDOFF_AUTOMATISATION.md`.

Règle de tenue : chaque création/modification d'un hook ajoute une ligne ici,
dans le même commit que le changement — jamais reconstruit de mémoire après
coup. Un git hook lui-même (`.git/hooks/*`) n'est PAS versionné (git n'exécute
jamais un fichier suivi sous `.git/hooks/`) — chaque stub pointe vers un script
`scripts/*.sh` versionné, c'est ce script qui apparaît dans l'historique.

---

## Git hooks (stub `.git/hooks/*` → script versionné `scripts/*.sh`)

### pre-commit → `scripts/pre-commit-secret-scan.sh` + `scripts/guardrail-file-alert.sh`
Bloque un commit si un vrai secret est détecté (gitleaks, staged diff
uniquement) ; alerte (sans jamais bloquer) si un fichier garde-fou est
touché.
- **2026-07-23** création `pre-commit-secret-scan.sh` — scan gitleaks sur le
  diff staged, échec fermé (bloque si secret trouvé ou si gitleaks absent).
- **2026-08-07** ajout `guardrail-file-alert.sh` — tripwire mécanique sur
  `wallet_guard.py` / `regles-uniques.mdc` / `.claude/settings.json` /
  tout `config.toml` réel. Détection + log durable
  (`/opt/aria-data/guardrail-file-alerts.log`) uniquement, **jamais de
  blocage** (consigne opérateur explicite : "choisi il faut pas que sa te
  bloque"). Stub réécrit pour chaîner les deux scripts (le premier ne peut
  plus bloquer, seul le code de sortie du secret-scan gate le commit).
- **2026-08-11** added `english-content-check.sh` -- heuristic French-text
  tripwire on staged CLAUDE.md/`docs/HANDOFF_*.md` prose and `.py`/`.sh`
  comments (accented characters or 3+ French stopwords per line, quoted
  operator citations excluded first). Real incident behind it: 3
  HANDOFF entries got written straight in French mid-session (23/07's
  "repo content stays English" rule), caught only by the operator noticing
  by eye. Alert-only, same doctrine as `guardrail-file-alert.sh` (a
  heuristic detector has real false-positive risk, must never block a
  legitimate commit). Log: `/opt/aria-data/english-content-alerts.log`.
  Stub chained after `guardrail-file-alert.sh`, still before the
  secret-scan gate.

### pre-push → `scripts/pre-push-regression-check.sh` (chaîné AVANT devils-advocate-review.sh)
Garde-fou mécanique manquant (17/08, opérateur : "un truc qui vérifie ce que tu
fais ici en direct par rapport à toutes les lignes cumulées avant déploiement
vers github ?") — ni l'instruction textuelle de `session-checkpoint.sh` ("vérifie
que les tests passent avant de pousser") ni la revue Fable 5 de
`devils-advocate-review.sh` ne lançaient réellement `pytest` : la première est
une consigne à l'agent (oubliable), la seconde une revue architecturale, jamais
un test fonctionnel. **Bloquant** (contrairement à `devils-advocate-review.sh`,
toujours async) : `exit 1` empêche le push si un test cible échoue.
Périmètre : cumul depuis `.claude/last-deployed-ref` (même marqueur que le
compteur 500 lignes de `session-checkpoint.sh`), pas seulement le diff de ce
push — une régression introduite plusieurs pushs plus tôt mais jamais encore
testée ensemble est quand même attrapée. Chaque module source modifié est
mappé à ses tests par `grep` (jamais un simple `test_<module>.py`, la
convention réelle du repo n'est pas uniforme, ex. `dexpaprika.py` →
`test_dexpaprika_client.py`) + `test_coherence.py` toujours inclus. Chaîné
AVANT `devils-advocate-review.sh` dans le stub `.git/hooks/pre-push` — un test
cassé bloque le push avant qu'un appel Fable 5 payant ne soit envisagé.
- **2026-08-17** création. Vérifié en direct : 968 tests ciblés en 65s
  (contre 577s pour la suite complète).

### pre-push → `scripts/devils-advocate-review.sh` (+ `scripts/devils-advocate-lib.sh`)
Critique architecturale post-push par Claude Fable 5 (async, jamais bloquant),
rapport écrit dans `/opt/aria-data/architect-reports/pending/<sha>.md`.
- **2026-07-18** création — veille continue VPS Research + première version
  Avocat du Diable.
- **2026-08-02** migration modèle vers Gemini (recharge OpenRouter épuisée
  côté modèle précédent).
- **2026-08-03** (x2) identification des requêtes OpenRouter (headers
  X-Title/X-OpenRouter-Title).
- **2026-08-04** refonte : file pending/archived (un fichier par push, plus
  jamais écrasé), modèle officialisé Claude Fable 5, ajout du check
  pré-commit synchrone (`devils-advocate-precommit.sh`, lib partagée
  `devils-advocate-lib.sh`).
- **2026-08-05** exception `.github/**`-only : review payante sautée si le
  push ne touche que de la config CI (validé opérateur).
- **2026-08-07** seuil de batching (2000 lignes cumulées) mécanisé via
  `LAST_REVIEWED_MARKER` — plus de suivi manuel `git diff --stat`, corrige
  un incident réel le jour même (push isolé ~20 lignes sous le seuil ayant
  quand même déclenché un appel payant).
- **2026-08-12** le cumul de lignes calculé à chaque push est maintenant
  affiché directement dans la sortie du `git push` (stderr, synchrone,
  avant le bloc détaché) — gap réel trouvé en direct : le calcul existait
  déjà mais n'atterrissait que dans un fichier log jamais lu au moment du
  push, une session poussant sans vérifier elle-même le seuil n'avait rien
  qui le lui mettait sous les yeux.

### commit-msg → `scripts/commit-msg-coauthor-check.sh`
Auto-ajoute la ligne `Co-Authored-By: GoldenFarFR` si `Co-Authored-By: Claude`
est déjà présente mais que la ligne opérateur manque. Ne bloque jamais, ne
touche pas un commit sans aucun co-auteur Claude (commit opérateur direct).
- **2026-08-07** création — garde-fou mécanique délégué ("choi toi"),
  remplace la vigilance manuelle (memory `feedback_dual_coauthor_commits`,
  convention posée le 29/07 sans mécanisation jusqu'ici).

---

## Hooks Claude Code (`.claude/hooks/*.sh`, wirés dans `.claude/settings.json`)

### session-start.sh — `SessionStart`
Prépare l'environnement (venv Python 3.12 + `aria-core[dev]`) à l'ouverture
de session, asynchrone sur web (barre de statut "🔧 env NN%").
- **2026-07-08** création (x2 commits le même jour : garde-fou de cohérence
  CI + passage en mode asynchrone avec progression %).

### system-issues-live-alert.sh — `UserPromptSubmit`
Remonte les anomalies `system_issues` **pendant** une session, sans attendre
un redémarrage. Créé après une remarque opérateur qui a invalidé la
conception de la veille : `system-issues-reminder.sh` est un hook
`SessionStart`, or l'opérateur redémarre très rarement ("je redémarre une
nouvelle session très rarement tu risques d'en rater beaucoup") et les
sessions durent des heures. Câbler les watchdogs vers `system_issues`
(17/08) ne suffisait donc pas : une anomalie détectée en milieu de session
n'aurait été vue qu'à la session suivante — on avait seulement déplacé le
problème qu'on croyait résoudre (dépendre de l'opérateur pour relayer une
alerte Telegram).
Anti-bruit indispensable, en deux couches : (1) chaque id n'est signalé
qu'une fois par session (`.claude/.system-issues-alerted`, même patron que
`.architect-pending-reminded-state`) ; (2) filtré à `warning`/`critical`/
`error` — le niveau `info` (file-staleness-watch, docs à relire) produisait
8 lignes de bruit au premier test réel, exactement ce qui rend un mécanisme
d'alerte inutilisable ; les `info` restent listées au démarrage par
`system-issues-reminder.sh`. Lecture seule (`-readonly`), ne peut jamais
écrire dans la base de prod, et utilise `.timeout` et non `PRAGMA
busy_timeout` (le PRAGMA écho sa valeur sur stdout en mode `-cmd` et
polluerait le contexte injecté — vrai bug rencontré le même jour sur
`log-health-watch/run.sh`).
- **2026-08-17** création. Vérifié en direct : alerte au 1er passage sur une
  issue `critical` de test, silencieux au 2e (anti-doublon), silencieux sur
  les 8 `info` réellement ouvertes.

### session-checkpoint.sh — `UserPromptSubmit`
Deux mécanismes dans le même fichier : (1) rappel périodique de mise à jour
des résumés (HANDOFF/CLAUDE.md/etat-systeme-cable), (2) rappel de déploiement
VPS au-delà d'un seuil de lignes non déployées.
- **2026-07-08** création — checkpoint 20 messages + rappel déploiement VPS
  (seuil 2500 lignes, une ligne non bloquante).
- **2026-07-10** cadence checkpoint relevée 20→1000 messages.
- **2026-07-15** (x2) seuil déploiement VPS 2500→6000 puis 6000→4000 lignes
  (ajustement opérateur le même jour).
- **2026-08-03** (x2) mécanisation de l'auto-compact à 60% contexte ; cadence
  checkpoint abaissée 1000→150 messages (avis Fable 5 : à 1000 le rappel ne
  se déclenchait presque jamais avant la fin d'une session).
- **2026-08-04** ajout du déclencheur Avocat du Diable dans le même hook.

### session-compact-reminder.sh — `SessionStart` (matcher: "compact")
Réinjecte les rappels critiques (français, garde-fous capital réel, vérifier
avant d'affirmer) juste après chaque compaction automatique.
- **2026-08-03** création, en même temps que la mécanisation de l'auto-compact
  ci-dessus (filet de sécurité contre la dérive post-compaction déjà observée).

### french-reasoning-reminder.sh — `UserPromptSubmit`
Rappel systématique de rester en français (raisonnement visible inclus) à
chaque message.
- **2026-07-22** création.

### gate-status-injector.sh — `SessionStart`
Injecte l'état réel des gates ARIA (`docker inspect` sur `aria-api`) en début
de session — évite de citer un gate de mémoire sans vérifier.
- **2026-08-04** création, avec l'autorisation `deploy.sh` en settings projet.

### signal-cascade-queue-reminder.sh — `SessionStart`
Injecte le contenu de la file de triage persistante (étage 4 de la cascade
de signaux multi-source, `signal_cascade_convergence.py`) en début de
session — lecture seule, réponse vide si aucune entrée en attente ou DB
absente (environnement web/cloud). Objectif opérateur (08/08) : "lue par
Claude Code au démarrage de session" — sans ce hook la file existerait
mais personne n'irait jamais la consulter.
- **2026-08-09** création, avec `docs/HANDOFF_SIGNAL_CASCADE.md`.

### block-secret-display.sh — présent sur disque, **NON câblé** dans settings.json
Bloquait toute commande Bash affichant un secret en clair.
- **2026-07-24** création (x2 commits le même jour : version initiale +
  correctifs après revue).
- **2026-07-27** ajout `show-env-safe.sh` (lecture whitelistée de `.env`
  avec masquage).
- **Retiré volontairement depuis** (date exacte non tracée dans les commits
  du hook lui-même) — bloquait des déploiements légitimes. **Ne jamais
  proposer de le restaurer** (memory `feedback_block_secret_display_hook_removed`).
  Fichier laissé sur disque, hors de `settings.json`.

---

## Hors scope de ce fichier (trackés ailleurs)
- Crons VPS (Research watch, backlog promotion, watchdogs paper-trading/log/
  mémoire) → `docs/HANDOFF_AUTOMATISATION.md`.
- `test_coherence.py` (garde-fou CI, pas un hook au sens événementiel) →
  documenté dans CLAUDE.md § Automations in place.
