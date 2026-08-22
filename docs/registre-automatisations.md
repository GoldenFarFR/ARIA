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
| `pre-push-regression-check.sh` | pre-push (chaîné AVANT devils-advocate-review.sh) | 🟢 actif, bloquant | 17/08 | pytest ciblé sur le cumul depuis le dernier déploiement VPS, bloque le push si un test échoue |
| `devils-advocate-review.sh` | pre-push | 🟢 actif | 18/07 | critique architecturale post-push (Claude Fable 5), batché à 8000 lignes cumulées |

### Hooks Claude Code (session, `.claude/hooks/*.sh` wirés dans `.claude/settings.json`)

| Nom | Déclencheur | État | Depuis | Pourquoi |
|---|---|---|---|---|
| `session-start.sh` | SessionStart | 🟢 actif | 07/07 | prépare le venv Python (async sur web) |
| `session-compact-reminder.sh` | SessionStart (matcher compact) | 🟢 actif | 03/08 | réinjecte les rappels critiques juste après une compaction auto |
| `gate-status-injector.sh` | SessionStart | 🟢 actif | 04/08 | injecte l'état réel des gates ARIA (`docker inspect`) en début de session |
| `signal-cascade-queue-reminder.sh` | SessionStart | 🟢 actif | 09/08 | injecte la file de triage de la cascade de signaux |
| `system-issues-reminder.sh` | SessionStart | 🟢 actif | 11/08 | surface les issues ouvertes du registre `system_issues` |
| `system-issues-live-alert.sh` | UserPromptSubmit | 🟢 actif | 17/08 | remonte les anomalies system_issues EN COURS de session (warning/critical), l'opérateur redémarrant très rarement |
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
| `file-staleness-watch/run.sh` | `20 8 * * *` | 🟢 actif | signale (`system_issues`) les docs vivants sans revue depuis >15j, plafonné à 8 nouveaux/passage ; `mark-reviewed.sh` remet le compteur à zéro |

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
| `clean-install-check.yml` | hebdo dimanche 03:00 UTC | 🟢 actif, alerte seule | simule un clone frais (`pip install -e ".[dev]"` seul, sans extras) -- collecte toute la suite + exécute les tests connus pour dépendre d'un extra optionnel, détecte un import non gardé avant qu'un tiers ne tombe dessus (15/08, Task #188) |

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

- **late-bonding-sample-watch** (21/08) -- `/opt/aria-data/late-bonding-sample-watch/run.sh`, cron VPS toutes les 10 min. Notifie l'operateur par Telegram quand l'echantillon de clotures de la poche LATE-BONDING atteint 100 / 250 / 500 / 1000 sous la configuration COURANTE. Demande explicite de l'operateur ("previens moi quand tu aura assez de donnees") -- mecanise plutot que confie a la memoire d'une session, qui se termine. Chaque palier n'est annonce qu'une fois, et l'etat se remet a zero automatiquement des que `CONFIG_EPOCH` bouge (sinon un changement de configuration ferait croire a un echantillon deja constitue alors qu'il repart de zero). Le message porte TOUJOURS le PnL brut ET le PnL prive de ses deux meilleurs trades cote a cote : annoncer la seule moyenne inviterait exactement la lecture que le garde-fou statistique existe pour empecher. Lecture seule, aucun jugement -- il annonce qu'il y a de quoi decider, jamais quoi decider.


---

## Detail migre depuis CLAUDE.md (21/08)

Ces entrees vivaient dans la section « Automations in place » de CLAUDE.md,
qui pesait 19.8 Ko a elle seule (21% du fichier). Decision operateur du
21/08 : liberer l'espace de CLAUDE.md sans rien perdre. Le contenu est
reproduit INTEGRALEMENT ci-dessous et CLAUDE.md n'en garde qu'un index d'une
ligne par mecanisme, conformement a son propre routeur (« Active state ->
sa sous-section dediee ; le detail va dans le fichier du composant »).

- **Environment ready on its own**: `.claude/hooks/session-start.sh` (SessionStart, web) creates a Python 3.12 venv and installs `aria-core[dev]`. On web this is **asynchronous** (status bar "🔧 env NN%" → the indicator disappears once ready). Run tests via this venv: `packages/aria-core/.venv/bin/python -m pytest` (or `pytest` once PATH is exported). Don't recreate the env by hand.
- **Coherence guardrail**: `packages/aria-core/tests/test_coherence.py` runs in **CI** and MUST stay green. It enforces: no IP/email in public docs; honeypot active (VC analysis **and** pool entry filter); `paper_trade_cycle` wired to the heartbeat; ACP gated; referenced docs exist; "established facts" + "automations" blocks present here; **external-write actions registry** (`test_external_write_actions_registered_in_allowlist`, 10/07) — every production function that actually writes externally (GitHub/X/email) must be declared in `_EXTERNAL_WRITE_ALLOWLIST`, otherwise CI breaks immediately (mechanical anti-recurrence guardrail after the Cursor/worker-queue incident). **If you DELIBERATELY change an invariant, update this test in the SAME commit** — that's the contract that prevents drift between sessions.
- **CI**: `.github/workflows/ci.yml` runs the VC surface + key capabilities + the coherence guardrail on every push touching `packages/aria-core/**`.
- **Git workflow**: develop on a `claude/…` branch, THEN **merge into `main`** so new sessions AND prod inherit it (a new session reads `CLAUDE.md` from `main`). Nothing is deployed without `./vanguard/deploy.sh` on the VPS.
- **1M$ paper-trading**: heartbeat task `paper_trade_cycle` **gated by `ARIA_PAPER_TRADING_ENABLED`** (OFF by default); enabling it starts the 20-day proof run.
- **2FA**: member site = Privy native MFA (enrollment button + Google, to enable in the Privy dashboard). Operator = TOTP (`aria_core/admin_totp.py`) **opt-in via `ADMIN_TOTP_SECRET`** (OFF by default, no lock-out; `X-Admin-Totp` header required in addition to the admin secret when enabled; per-IP anti-brute-force lock). Enrollment: `python vanguard/operator/gen-admin-totp.py`.
- **Auto session checkpoint (every 150 messages, lowered from 1000 on 03/08 — Fable 5's opinion: at 1000 the reminder almost never fired before a session ended, undermining the HANDOFF-per-component discipline; 1000 had itself replaced 20 on 10/07)**: hook `.claude/hooks/session-checkpoint.sh` (UserPromptSubmit) counts messages in `.claude/.msg-counter` (gitignored) and, every 150, injects a reminder → the assistant **offers to update the summary files** (HANDOFF, CLAUDE.md, `etat-systeme-cable.md`) to keep `CLAUDE.md` fed and a new session ready. The status bar (`statusline.sh`, value manually duplicated, no shared source) shows "📌 chk NN/150" to see it coming. Saved on operator approval (never forced). Don't undo this hook.
- **Backlog (numbered `#` list, TaskCreate/TaskUpdate) always kept fed (09/07, explicit operator instruction)**: permanently keep **10 to 15 pending/in_progress tasks** in the list. Think about it often, not just when the operator asks "what next?" — as soon as a session finishes several tasks and the count drops under ~10, propose new concrete ideas (never vague filler) to replenish the reserve. Ideas come from what's observed while building (gaps found along the way, spotted technical debt, logical follow-ups of a shipped feature) — never invented to fill space. **`TaskCreate`/`TaskList` alone does NOT persist across sessions (discovered 16/08, operator noticed ~70 tasks created a few days prior had vanished)** — always replicate the list into `docs/task-backlog.md` (cf. "Backlog" section further below) so it survives; a new session reads that file and re-creates its open tasks via `TaskCreate`.
- **VPS deployment reminder (undeployed-lines threshold)**: the same hook measures lines changed on `main` since the last deployment (**tracked** marker `.claude/last-deployed-ref`) and, beyond **4000 lines** (adjustable at the top of the hook, 2500→6000→4000 on 15/07 at operator request), injects a reminder → the assistant displays **ONE SINGLE LINE** ("🚀 VPS deployment recommended — 4000-line quota reached") then **CONTINUES normally** (exceeding the threshold blocks nothing). Deployment commands are only given **on request** ("go"). Throttle: one reminder per new state of `main`. Status bar: "🚀 N l. to deploy". **When the operator confirms deployment, set `.claude/last-deployed-ref` = deployed commit (`git rev-parse main`) then commit/push** — that's what resets the counter to zero. Don't undo this hook.
- **Claude Code network access (cloud environment, 09/07, reaffirmed 10/07)**: custom-domain allowlist, configured ONLY via environment settings on claude.ai — never from a session. **Systematic reflex: as soon as an API/domain access is missing to verify a fact live, ASK the operator ("can you add this domain?") instead of concluding "inaccessible", guessing from the code alone, or defaulting the verification to the VPS** — explicit, repeated operator instruction. An addition takes effect **immediately, no session restart needed** (verified 09/07 with `*.virtuals.io`, `x.com`/`twitter.com`, `*.shekel.xyz`; re-verified 10/07 with `api.virtuals.io` + `www.clanker.world`, effective within seconds). Prefer a wildcard (`*.example.io`) over a single subdomain when several subdomains of the same service are likely (avoids back-and-forth).
- **Context ceiling at 60%**: `.claude/hooks/context-ceiling.sh` warns past the threshold, and a `SessionStart` hook re-injects the critical reminders right after every compaction. The native `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` setting is DEAD (verified 21/08: still in `settings.json`, no longer honoured) — the hook replaces it and cannot compact by itself, only make the overrun impossible to ignore. Detail: `docs/hooks-changelog.md`.
- **Ongoing "VPS Research" watch (18/07, cadence raised from 3h→2h, re-verified 03/08)**: VPS cron (`0 */2 * * *`, `/opt/aria-data/research-loop/run.sh`) — headless `claude -p`, tools HARD-CODED restricted (`--allowedTools "WebSearch WebFetch Read Write"` + `--disallowedTools "Bash Edit Agent Task"`, no executable command even under prompt injection). Prompt: `/opt/aria-data/research-loop/prompt.txt`. Log deliberately OUTSIDE the public repo (`/opt/aria-data/research-loop/research-log.md`) — distinct from `docs/aria-learning-inbox/` (promoted, git-tracked sheets) and from `knowledge_inbox_cycle` (ARIA's spoken knowledge). Research = divergent thinking, never code-anchored — distinct from the Devil's Advocate (convergent thinking, bounded to the diff) right below; the two never merge into the same agent. My role: re-read `research-log.md`, promote what's actionable (judgment on every re-read, never automatic). Design + first real test: `docs/HANDOFF_AUTOMATISATION.md`.
- **"Devil's Advocate" — post-push architectural critique (18/07) — LIVE on Claude Fable 5 via the direct Anthropic API.** Hook `.git/hooks/pre-push` → `scripts/devils-advocate-review.sh` (shares `scripts/devils-advocate-lib.sh` with the synchronous `devils-advocate-precommit.sh` check, callable manually before a commit). Reports in `/opt/aria-data/architect-reports/pending/<sha>.md` (outside the public repo); "read" = move to `archived/`. **Absolute rule**: verify EVERY claim against the real code before acting on a report — never swallow it blindly; read pending reports before writing new feature code. **Never treat a clean Fable 5 report as proof of absence of a bug on logic that crosses a build/submodule/dependency boundary** (#313, 16/08) — any diff touching a submodule pin, build script, vendored dependency, or lockfile deserves a manual second look regardless of the report's verdict. **Threshold: accumulate local commits and hold back BOTH `git push` AND `./vanguard/deploy.sh` until the cumulative RAW line count reaches 8000** (deploy gated by the same threshold as push, not just the API-call trigger). Exception: a push touching ONLY `.github/**` goes out immediately at any line count. Diffs past 60000 chars are condensed by Claude Haiku 4.5 before Fable 5 reviews them; every call's real cost logged to `/opt/aria-data/architect-reports/cost-log.csv`. Migration history and cost details: `docs/HANDOFF_AUTOMATISATION.md`. **Any NEW custom call to the Fable 5 API MUST read the "Checklist avant tout nouvel appel API Fable 5" at the top of `docs/HANDOFF_LLM.md` FIRST** — payload format, max_tokens, timeout, key handling, all already documented there.
- **Mechanical guardrails on recurring-but-fixed project topics (07/08, operator-delegated "vérifie si tu peux pas créer d'autres hooks mécaniques... jamais effacer" → "choi toi")**: 3 new invariants, none requiring manual re-vigilance going forward. (1) `test_handoff_file_indexed_in_claude_md` (`test_coherence.py`) — every `docs/HANDOFF_*.md` on disk must be cited by name in CLAUDE.md's own "Index des HANDOFF" section, mechanizing a rule CLAUDE.md already stated but never enforced. (2) `test_handoff_entries_use_valid_status_and_required_fields` (same file) — every HANDOFF entry block must open on a valid status (`DEPLOYE`/`CODE`/`CONFIG`/`ETAT ACTUEL`) + `Sujet`/`Subject`, and contain `Date`/`Probleme`/`Solution` — calibrated empirically against all 492 real entries (0 false positives) before being added, `Sujet`/`Subject` both accepted (23/07 English-repo cutover). (3) `.git/hooks/commit-msg` (unversioned stub) → `scripts/commit-msg-coauthor-check.sh` (versioned) — auto-appends the `Co-Authored-By: GoldenFarFR` line whenever a commit already carries the Claude one but is missing it, never rejects a commit, leaves operator-direct commits (no Claude co-author at all) untouched. **Deliberately excluded from this batch**: guardrail-file edit protection (`permission_mode`/`wallet_guard`/`regles-uniques`/`config.toml`) — highest-value candidate found, but outside the scoped autonomous-governance permission ("N'inclut JAMAIS les fichiers garde-fous") — needs its own explicit, separately-named operator confirmation before being built.
- **Lesson**: the desktop app's "Routine" mechanism CANNOT reach the VPS filesystem — never give it `/opt/aria` as a folder (usable only for a pure HTTPS call with no filesystem access). Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **Automated backlog promotion (18/07)**: `scripts/research-log-promotion.sh`, daily VPS cron (9am UTC) — re-reads `research-log.md`, judges each entry with real critical thinking, promotes what's actionable to CLAUDE.md/`aria-learning-inbox`, never into code or a guardrail file. Tools `Read Write Edit WebSearch WebFetch` + `Bash(git *)` only. Detail + fixed incident: `docs/HANDOFF_AUTOMATISATION.md`.
- **1M$ paper-trading watchdog (18/07)**: `/opt/aria-data/paper-watchdog/run.sh`, VPS cron every 3h. Meant for Claude Code sessions, not the operator (no Telegram notification). Written APPEND-ONLY to `watchdog-log.md`. **Any session resuming the 1M$ test thread must read the latest entries before assuming the portfolio state** — never guess. Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **Production log health monitoring (03/08)**: `/opt/aria-data/log-health-watch/run.sh`, hourly VPS cron. Distinct from CI (code) and `/api/health`/autoheal (HTTP liveness): scans the real container logs for `Traceback`/`CRITICAL` only, plus the Devil's Advocate's state (a blind spot plain `docker logs` doesn't cover). Telegram notification reserved for anomalies. Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **VPS memory/swap monitoring (05/08)**: `/opt/aria-data/memory-watch/run.sh`, VPS cron every 15 min (`5,20,35,50 * * * *`). Combined metric (RAM used + swap used) / (RAM total + swap total) -- swap alone can climb even when RAM looks fine (real incident: orphaned ccd-cli sessions pushed swap to 65%). Alerts Telegram at 75%, hysteresis at 65% before re-arming (never repeats while sustained above threshold). Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **Signal cascade watch (09/08)**: `/opt/aria-data/signal-cascade-watch/run.sh`, VPS cron every 15 min. Bash-only (no LLM call, mechanical DB polling) -- replaces manually re-armed session `Monitor` calls (1h cap, had to be relaunched by hand). Detects (1) a cycle pass on each of the 4 source columns (GitHub/Farcaster/web/X) and (2) a convergence change on `signal_cascade_triage_queue` (new candidate entering, or `convergence_count` rising on an already-queued one). Meant for Claude Code sessions, not the operator (no Telegram -- a triage decision needs real reasoning, never automatable by a cron). Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **VC watchdog (11/08, backlog #91)**: `/opt/aria-data/vc-watch/run.sh`, VPS cron hourly. Bash-only, reads `/opt/aria-data/heartbeat_state.json`'s `last_runs` (the SAME source heartbeat.py itself uses to decide if a task is due -- not a guessed business table) for `vc_crawl`/`vc_radar_x`/`vc_thesis_review`/`vc_resolve`/`vc_weekly_forecast`/`vc_self_report`. Flags a task silent past 2x its nominal cadence, hysteresis-gated (one alert per anomaly, auto-clears once the cycle resumes). Meant for Claude Code sessions (no Telegram). Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **outgoing-pause-watch (13/08, real operator gap: found out ~5h late that a session had armed `/stop`)**: `/opt/aria-data/outgoing-pause-watch/run.sh`, VPS cron hourly. Pure read of `pause_state.json` (never touches `outgoing_pause.py` itself -- guardrail file). Deliberately NOT one-shot hysteresis like every other watchdog -- sends a REPEATED Telegram alert every hour for as long as the kill-switch stays armed. Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **GoPlus Security X watch (18/08)**: `/opt/aria-data/goplus-security-watch/run.sh`, VPS cron daily. 3 chained stages: (1) fetches @GoPlusSecurity's tweets via the REAL x402 twit.sh API (`services/twitsh.py`, $0.01/call), dedupes by tweet_id, restricted to the last 24h; (2) a second restricted headless `claude -p` pass (same hard tool limits as `research-loop`) judges EACH tweet -- explicit TRAITER/NON verdict; (3) `promote_verdicts.py` (deterministic, no LLM) opens a `system_issues` entry per TRAITER verdict AND sends a direct Telegram notification. Actual implementation of a TRAITER verdict stays a task for a real session to pick up from `system_issues`/Telegram, never a further unsupervised stage (real automation ceiling found here — see HANDOFF for why). Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **`system_issues` -- centralized "GitHub Issues"-style registry (11/08, explicit operator request)**: `aria_core/system_issues.py` (open_issue/close_issue/list_open, `system_issues` table) + `.claude/hooks/system-issues-reminder.sh` (SessionStart, same pattern as `signal-cascade-queue-reminder.sh`) surfaces every OPEN issue at the start of every session, most severe first. Any watchdog/audit can open one (bash writes directly via `sqlite3`, Python via the module) -- `vc-watch/run.sh` is the first wired producer (hysteresis: opens once per anomaly via `dedup_key`, auto-closes when the underlying cycle resumes). A session is expected to close every open issue each time it sees one (either a real fix, or `close_issue(id, reason)` if it's a false positive) -- never leave one untouched. Tested end-to-end live (real DB) before wiring the first producer. Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **Self-healing throttles/bypasses, 5 mechanisms**: (1) `holder_concentration_outage_bypass.py` -- auto-arms after 3 sustained real failures, disarms on first real success; (2) `goplus_quota_suspension.py` -- auto-suspends on a real GoPlus rate-limit signal, exponential backoff 12h→48h; (3) `services/geckoterminal.py`'s throttle -- adaptive (tightens fast on a real 429, eases slowly only after 30 sustained successes, never past the operator's own last hand-calibrated floor); (4) `burn_in_cadence.py` -- generic auto-revert of an accelerated observation cadence to nominal after N clean heartbeat cycles; (5) `wallet_scan_concurrency.py` -- adaptive `MAX_WALLETS_PER_CYCLE`, same tighten-fast/ease-slow doctrine as (3) one layer up. None need a human to notice/edit/redeploy anymore for the SAME class of recurring event. Detail: `docs/HANDOFF_PIPELINE_MOMENTUM.md` (holder-concentration + GeckoTerminal), `docs/HANDOFF_GOPLUS.md` (quota suspension), `docs/HANDOFF_AUTOMATISATION.md` (burn-in cadence, real trigger + incidents replaced), `docs/HANDOFF_WALLET_SCORING.md` (wallet-scan concurrency).
- **Homemade website scraper (10/08, backlog #43)**: `services/website_scraper.py` -- plain HTTP fetch + regex extraction (reuses `site_snapshot.py`'s proven parser), follows internal links up to 15 pages, zero third-party quota. Wired FIRST in `website_substance._default_crawl` (scraper → Firecrawl → Tavily), never a hard replacement — both external providers stay real fallbacks for WAF/JS-only-SPA cases the scraper can't handle. `_default_crawl` itself refactored into an extensible ordered list (`_CRAWL_LAYERS`) — a future 4th candidate is one line, never a rewrite — and `website_crawl_failure_log.py` records every real case where all layers fail together (`failure_count_since`/`recent_failures`), the evidence to consult before actually adding one. Detail: `docs/HANDOFF_SIGNAL_CASCADE.md`.
- **circuit-breaker-watch (04/08, found undocumented 18/08 -- added retroactively)**: `/opt/aria-data/circuit-breaker-watch/run.sh`, VPS cron hourly. Reads the REAL in-memory state of the 5 external clients with a true open/closed circuit (blockscout, dexscreener, goplus, wallet_transfers_fast, the shared OHLCV cascade) via `/api/aria/diagnostics/circuit-breakers` (`aria_core.circuit_breaker_status`). Telegram alert reserved for `sustained_outage` (reopened >=2x within the last hour), never a normal isolated cooldown. Detail: `docs/HANDOFF_AUTOMATISATION.md`.
- **`solana-robinhood-shadow/shadow_persistent.py` -- standalone always-on process, OUTSIDE this git repo and outside Docker (found/documented 18/08)**: `/opt/aria-data/solana-robinhood-shadow/`, launched directly on the VPS (currently a bare `nohup`, no systemd unit, reparented to PID 1 -- no auto-restart on crash). Runs `solana_support_bounce_shadow`/`_v2_shadow`'s `record_signals()` in a tight loop against the real prod DB, importing `aria_core` as a library from the SAME repo checkout (so it picks up a library fix on its next restart, but any change to the SCRIPT ITSELF needs an explicit restart, not a redeploy). NOT wired to `heartbeat.py`/`bootstrap.py` -- a separate mechanism from every other shadow, sharing no throttle/circuit-breaker coordination with the `aria-api` container's own use of the same providers (confirmed root cause of the 18/08 `ohlcv_dexpaprika` sustained-outage alert: a same-day `max_pages` 1→3 change here tripled real DexPaprika load with no visibility from inside the container). `solana_pump_shadow`/`robinhood_pump_shadow` are a SEPARATE, currently-stopped mechanism (`shadow_loop.sh` → `shadow_kickoff.py`, one-shot per pass) -- not confused with this one. Standing gap, not yet resolved: this script should eventually be migrated into the tracked repo (or given a systemd unit) so a future session doesn't have to rediscover it via a multi-agent trace again. Detail: `docs/HANDOFF_PIPELINE_MOMENTUM.md` (18/08 entry).

### ci-health-watch (21/08) -- ACTIF
`/opt/aria-data/ci-health-watch/run.sh`, cron horaire (minute 25).
Surveille l'etat REEL des 8 workflows GitHub Actions sur `main`, plus les
alertes Dependabot ouvertes. Ouvre une entree `system_issues` par workflow
rouge (dedup par nom), la referme automatiquement des que le workflow
redevient vert.

**Pourquoi** : le 21/08, le job "Security -- secret scan" a echoue sur CHAQUE
push pendant sept heures, 18 runs consecutifs, sans que personne le voie --
ni l'agent malgre une consigne permanente de verifier ce baseline, ni
l'operateur, jusqu'a ce qu'il ouvre l'onglet Actions par hasard. Aucun des
~14 crons existants ne regardait la CI : log-health-watch surveille le
PROCESSUS en prod, uptime-watch le SITE, la CI elle-meme n'avait aucun
gardien. Les alertes Dependabot avaient le meme angle mort : elles arrivent
par email, et un email que personne ne lit ne protege de rien.

Complementaire du hook `scripts/pre-push-secret-baseline-check.sh` (meme
jour) : le hook empeche de POUSSER un secret non audite mais ne couvre qu'un
workflow sur huit ; ce cron voit les sept autres, apres coup.
Fail-open sur l'outillage (gh absent, API muette), jamais de bruit quand tout
est vert.

### runner-frequency-watch (22/08) -- ACTIF
- **Quoi** : `/opt/aria-data/runner-frequency-watch/run.sh`, cron toutes les 2 h,
  rapport cumulatif dans `report.md`.
- **Pourquoi** : toute la décision sur la distance du stop suiveur repose sur un
  seul inconnu, la FRÉQUENCE des vrais coureurs. Un trailing serré encaisse
  +2-3 % sur une poussée à +12 % ; un trailing large abandonne ça pour attraper
  un +50 %. Lequel gagne dépend uniquement de la fréquence du +50 %, et cette
  fréquence n'avait jamais été mesurée — chaque tentative passée rejouait des
  parcours TRONQUÉS à la sortie réelle, où un réglage plus large ne peut pas se
  déclencher et rend donc toujours le même chiffre.
- **Ce qu'il lit** : le pic réellement atteint sur les positions clôturées. Fiable
  depuis le 22/08 seulement : l'archivage a gagné un déclencheur sur le PRIX ce
  jour-là (le déclencheur sur la réserve ne pouvait pas se déclencher quand le
  prix bougeait à réserve constante), ce qui fait passer une position de 1 point
  archivé à 8-16.
- **Seuil de décision** : ~1 coureur sur 5 (20 %) rend un trailing large rentable.
- **Premier relevé** : 0/20, à reconfirmer au-delà de 200 clôtures.
