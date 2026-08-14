# Registre des automatisations -- ARIA

Vue d'ensemble UNIQUE de tout ce qui s'exécute automatiquement dans ce projet
(hooks git, hooks Claude Code, crons VPS, CI GitHub, sidecars Docker) --
créé le 11/08 sur demande opérateur ("savoir quels sont tous les hooks
automatisés... quand il y en aura 100 ce sera trop compliqué de se rappeler
de tous"). Ce fichier ne remplace aucun des registres détaillés existants
(`docs/hooks-changelog.md` pour les hooks, `docs/HANDOFF_AUTOMATISATION.md`
pour les crons) -- il en est le résumé consultable en un coup d'oeil, avec
pointeur vers le détail pour qui veut creuser.

**Règle de tenue** : le tableau "État actuel" est édité EN PLACE à chaque
changement d'état (jamais un nouveau paragraphe empilé) -- reflète toujours
la réalité au moment de la lecture. Le "Journal des bascules" en bas est
APPEND-ONLY -- une ligne par changement actif/inactif, jamais réécrit.
Toute nouvelle automatisation créée s'ajoute ici dans le MÊME commit,
comme pour `hooks-changelog.md`.

---

## État actuel

### Hooks Git (repo, stub `.git/hooks/*` non versionné → script versionné)

| Nom | Déclencheur | État | Depuis | Pourquoi |
|---|---|---|---|---|
| `pre-commit-secret-scan.sh` | pre-commit | 🟢 actif, bloquant | 23/07 | scan gitleaks du diff staged, bloque si secret détecté |
| `guardrail-file-alert.sh` | pre-commit | 🟢 actif, alerte seule | 07/08 | tripwire si un fichier garde-fou (wallet_guard/regles-uniques/config.toml) est touché |
| `english-content-check.sh` | pre-commit | 🟢 actif, alerte seule | 11/08 | détecteur heuristique de contenu repo resté en français (règle 23/07) |
| `commit-msg-coauthor-check.sh` | commit-msg | 🟢 actif | 07/08 | auto-ajoute le co-auteur GoldenFarFR si Claude est déjà présent |
| `devils-advocate-review.sh` | pre-push | 🟢 actif | 18/07 | critique architecturale post-push (Claude Fable 5), batché à 8000 lignes cumulées |

### Hooks Claude Code (session, `.claude/hooks/*.sh` wirés dans `.claude/settings.json`)

| Nom | Déclencheur | État | Depuis | Pourquoi |
|---|---|---|---|---|
| `session-start.sh` | SessionStart | 🟢 actif | 07/07 | prépare le venv Python (async sur web) |
| `session-compact-reminder.sh` | SessionStart (matcher compact) | 🟢 actif | 03/08 | réinjecte les rappels critiques juste après une compaction auto |
| `gate-status-injector.sh` | SessionStart | 🟢 actif | 04/08 | injecte l'état réel des gates ARIA (`docker inspect`) en début de session |
| `signal-cascade-queue-reminder.sh` | SessionStart | 🟢 actif | 09/08 | injecte la file de triage de la cascade de signaux |
| `system-issues-reminder.sh` | SessionStart | 🟢 actif | 11/08 | surface les issues ouvertes du registre `system_issues` |
| `session-checkpoint.sh` | UserPromptSubmit | 🟢 actif | 07/08 | rappel MAJ résumés (150 msg) + rappel déploiement VPS (4000 lignes) |
| `french-reasoning-reminder.sh` | UserPromptSubmit | 🟢 actif | 22/07 | rappel de rester en français, raisonnement visible inclus |
| `block-secret-display.sh` | -- (retiré de settings.json) | 🔴 désactivé | créé 24/07, retiré (date exacte non tracée) | bloquait des déploiements légitimes -- **ne jamais restaurer** (décision opérateur actée) |

### Crons VPS (`crontab -l`, hors repo -- vérifié en direct le 11/08)

| Nom | Cadence | État | Pourquoi |
|---|---|---|---|
| `research-loop/run.sh` | `0 */2 * * *` | 🟢 actif | veille externe continue (Base/écosystème), tools restreints |
| `research-log-promotion.sh` | `0 9 * * *` | 🟢 actif | promeut le research-log vers CLAUDE.md/backlog |
| `paper-watchdog/run.sh` | `30 */3 * * *` | 🟢 actif | surveille le test papier 1M$ |
| `log-health-watch/run.sh` | `15 * * * *` | 🟢 actif | scanne les logs prod pour Traceback/CRITICAL |
| `circuit-breaker-watch/run.sh` | `45 * * * *` | 🟢 actif | alerte sur `sustained_outage` (5 modules à cooldown) |
| `memory-watch/run.sh` | `5,20,35,50 * * * *` | 🟢 actif | RAM+swap combinés, seuil 75%, hystérésis 65% ; boucle rapprochée (90s) pendant une alerte ; kill auto de sessions ccd-cli orphelines (>48h) au-delà de 90% |
| `db-backup/run.sh` | `10 4 * * *` | 🟢 actif | backup SQLite quotidien (WAL-safe), rotation 14j |
| `v8-watch/run.sh` | `*/30 * * * *` | 🟢 actif (cadence accélérée) | monitoring scalping_v8 -- **nominal 6h prévu, pas encore restauré (backlog #1)** |
| `signal-cascade-watch/run.sh` | `*/15 * * * *` | 🟢 actif | cycles + convergence de la cascade de signaux |
| `vc-watch/run.sh` | `0 * * * *` | 🟢 actif | silence anormal sur les cycles VC (crawl/radar/thesis/resolve/forecast/self-report) |
| `outgoing-pause-watch/run.sh` | `0 * * * *` | 🟢 actif | rappel Telegram RÉPÉTÉ (pas d'hystérésis one-shot) tant que le kill-switch `/stop` reste armé |

### CI / GitHub Actions (`.github/workflows/*.yml`, tourne sur les serveurs GitHub)

| Nom | Déclencheur | État | Pourquoi |
|---|---|---|---|
| `ci.yml` | push/PR (chemins filtrés) | 🟢 actif, bloquant | surface produit VC + garde-fou cohérence (`test_coherence.py`) |
| `codeql.yml` | push main + hebdo | 🟢 actif, alerte seule | analyse statique des failles de code (injection, désérialisation...) |
| `frontend-build.yml` | push/PR | 🟢 actif, bloquant | build strict des fronts (vitrine + produit), `npm ci` |
| `sca-scan.yml` | push/PR (tout le repo) | 🟢 actif, bloquant sur PR | CVE connues dans les dépendances Python/JS |
| `secrets-scan.yml` | push/PR (tout le repo) | 🟢 actif, bloquant | secrets codés en dur, `.secrets.baseline` audité |
| `security-sim.yml` | quotidien 03:17 UTC | 🟢 actif, bloquant | red-team automatisé contre le backend (milliers de requêtes hostiles) |
| `uptime-watch.yml` | ~15 min | 🟢 actif | surveille l'uptime depuis L'EXTÉRIEUR du VPS (angle mort d'une panne totale) |
| `dependabot.yml` | continu | 🟢 actif, alerte seule | alertes CVE uniquement, PRs de version routine désactivées |

### Sidecars Docker (VPS, tournent en continu, pas un cron/hook événementiel)

| Nom | État | Pourquoi |
|---|---|---|
| `willfarrell/autoheal` | 🟢 actif | redémarre un conteneur `unhealthy` |
| `autoheal-circuit-breaker.sh` | 🟢 actif | plafonne à 3 redémarrages/10min, pause autoheal sinon |

### Hors scope de ce registre (mécanismes auto-correctifs, pas des déclencheurs événementiels)

Les auto-régulations câblées DANS le heartbeat (pas des hooks/crons séparés) --
`holder_concentration_outage_bypass.py`, `goplus_quota_suspension.py`,
throttle adaptatif GeckoTerminal, `wallet_scan_concurrency.py`,
`burn_in_cadence.py` -- restent documentées dans leurs HANDOFF respectifs
(`HANDOFF_PIPELINE_MOMENTUM.md`, `HANDOFF_GOPLUS.md`, `HANDOFF_WALLET_SCORING.md`,
`HANDOFF_AUTOMATISATION.md`) plutôt que listées ici, pour ne pas diluer ce
fichier avec des mécanismes internes au code plutôt que des déclencheurs
externes.

---

## Journal des bascules

Une ligne par changement d'état actif/inactif (pas les créations/modifs de
contenu -- celles-là restent dans `hooks-changelog.md`/`HANDOFF_AUTOMATISATION.md`).

- **2026-07-24→27** `block-secret-display.sh` créé puis étendu (`show-env-safe.sh`).
- **2026-07-2x (date exacte non tracée)** `block-secret-display.sh` retiré de
  `settings.json` -- bloquait des déploiements légitimes, ne jamais restaurer.
- **2026-08-11** `v8-watch/run.sh` toujours à cadence accélérée 30min (créé
  temporaire lors de l'activation initiale) -- restauration 6h nominal
  toujours en attente, backlog #1.
