# aria-core — Architecture

> **Phase A (doc only)** — carte de l'existant, sans modification de code ni d'imports.  
> Dernière mise à jour : 2026-06-20

## Rôle dans l'écosystème

| Composant | Repo | Rôle |
|-----------|------|------|
| **Cerveau** | `aria-sandbox/packages/aria-core` | Runtime pip `aria-core` — skills, mémoire, Telegram, QI, heartbeat |
| **Hôte deploy** | `aria-vanguard/backend` | FastAPI, Render `aria-api`, pin SHA dans `requirements.txt` |
| **Opérateur** | `aria-vanguard/operator` | Secrets, `build-local.ps1`, `deploy-render.ps1` |
| **Skills IDE** | `aria-skills` | Workflows Grok/Cursor (hors runtime prod) |

`aria-core` ne déploie jamais seul : le hôte appelle `bootstrap.configure()` puis importe `brain`, `heartbeat`, etc.

## Flux principal (requête → réponse)

```
Telegram / API chat
       ↓
  brain.py (BrainAgent)
       ↓
  routing skill (portfolio, github, comms, capability, …)
       ↓
  skill.execute_*  +  grounding / epistemic gate
       ↓
  memory.append + cognitive (si insight) + truth_ledger (si fait canonique)
       ↓
  ChatResponse → gateway (telegram_bot, x_twitter)
```

**Heartbeat** (`heartbeat.py`) : boucle autonome planifiée (portfolio, QI promote, curriculum, health, …) — indépendante du chat.

## Arborescence `src/aria_core/`

### Racine — orchestration & runtime

| Module | Responsabilité |
|--------|----------------|
| `bootstrap.py` | Point d'entrée hôte : `DATA_DIR`, `settings`, hooks marché |
| `runtime.py` | Proxy vers settings pydantic du hôte |
| `paths.py` | Chemins `data_dir`, `memory_dir`, `aria.db`, avatar |
| `brain.py` | Agent principal — dispatch skills, LLM enhance |
| `heartbeat.py` | Tâches autonomes planifiées |
| `models.py` | Types Pydantic partagés (ChatRequest, AgentStatus, …) |
| `llm.py` / `llm_vision.py` | Appels LLM provider (Groq, xAI, …) |
| `grounding.py` | Contexte LLM : identité, mémoire, faits canoniques |
| `narrative.py` | Blocs system prompt (ton, holding, ZHC) |
| `identity.py` | Identité publique ARIA (@Aria_ZHC, holding) |
| `public_mode.py` | Garde-fous visiteur vs opérateur |
| `technical_claims.py` | Anti-hallucination deploy/commit |

### Capacité & autonomie (QI)

| Module | Responsabilité |
|--------|----------------|
| `capability_levels.py` | Indice QI 0→1000, rubric YAML, progression JSON |
| `capability_gap.py` | Lacunes capacité (`capability_gap` issues) |
| `qi_auto_judge.py` | Juge déterministe sur métriques réelles |
| `qi_self_judge_shadow.py` | Juge LLM shadow (test, sans effet QI) |
| `qi_judge_calibration.py` | Calibration ouvrier vs ARIA |
| `qi_promote.py` | Promotion QI heartbeat + notify Telegram |
| `curiosity.py` / `autonomous.py` | Apprentissage X → mémoire cognitive |
| `self_maintenance.py` | Auto-audit, issues GitHub |
| `aria_worker_queue.py` | File ouvrier Cursor (ARIA bloquée) |

### Mémoire — façade `aria_core/memory/` (Phase B)

```
┌─────────────────────────────────────────────────────────────┐
│ Package aria_core/memory/ (façade unifiée)                  │
│  journal.py → markdown DATA_DIR/memory/                     │
│  cognitive_sql.py → SQLite cognitive_knowledge              │
│  llm_context.py → build_llm_context (journal+cognitive+vector) │
│  vector/ → Chroma embedded (aria_vector_memory=false défaut)  │
├─────────────────────────────────────────────────────────────┤
│ Truth ledger — truth_ledger/ (hors package memory)          │
│ calibration_ledger.json — Brier épistémique                 │
└─────────────────────────────────────────────────────────────┘
```

| Module | Couche |
|--------|--------|
| `memory/journal.py` | Journal markdown (`append_memory` rétrocompat) |
| `memory/cognitive_sql.py` | SQLite — wrapper `knowledge/cognitive.py` |
| `memory/llm_context.py` | Contexte LLM unifié — injection vectorielle opt-in |
| `memory/vector/` | Chroma embedded — types dans `schema.yaml` |
| `member_memory.py` | Mémoire visiteurs (chat public) |
| `knowledge/calibration_ledger.py` | Prédictions P(vrai) + Brier |
| `truth_ledger/` | Faits vérifiés, sync GitHub |
| `repertoire_db.py` | Ventures holding (≠ cognitive) |

> **Phase C (2026-06)** : Chroma local opt-in — `pip install -e ".[dev,vector]"` + `aria_vector_memory=true`.  
> **Phase D (2026-06)** : `llm_context.py` injecte le rappel sémantique dans `build_llm_context` (flag off en prod par défaut).  
> **Phase E (2026-07)** : `memory/values.py` + `knowledge/aria_values.yaml` — valeurs opérationnelles injectées dans le contexte LLM.  
> **Phase F (2026-07)** : `memory/goals.py` + `knowledge/aria_goals.yaml` — objectifs + état dynamique (QI, revenu).  
> **Phase G (2026-07)** : `memory/reflection.py` + `reflections.jsonl` — synthèse journal/QI injectée dans le contexte LLM.
> **DDG cache (2026-07)** : `knowledge/ddg_cache.py` — cache fichier opt-in (`aria_ddg_search_cache=false` défaut).

### `skills/` — capacités exécutables

Chaque skill = `*_skill.py` avec `execute_*` + helpers. Routage dans `brain.py`.

| Skill | Domaine |
|-------|---------|
| `github_skill` | Commits, PR, repos protégés |
| `holding_site_skill` | Patch vitrine holding |

| `comms_skill` | Brouillons X/Telegram |
| `portfolio_skill` | Analyse watchlist (hook hôte) |
| `capability_skill` | `/level up`, statut QI |
| `epistemic_skill` / `calibrate_skill` | Calibration, vérif web |
| `faq_skill` | FAQ YAML |
| `repertoire_skill` | Répertoire ventures |
| `zhc_bridge` | Pont ZHC Institute |

### `knowledge/` — savoir statique & pipelines épistémiques

| Fichier | Type |
|---------|------|
| `*.yaml` | Rubric QI, watchlist X, épistémique |
| `cognitive.py` | CRUD mémoire cognitive (SQLite) |
| `epistemic*.py` | Pipeline vérif web, critic, replay |
| `operator_pitfalls.yaml` | SSOT pièges opérateur (agents IDE) |
| `web_verify.py` | Recherche DuckDuckGo si incertain |
| `*_curriculum.py` | Entraînement épistémique heartbeat |

### `gateway/` — I/O externes

| Module | Canal |
|--------|-------|
| `telegram_bot.py` | Webhook Telegram, commandes opérateur |
| `x_twitter.py` / `x_engagement.py` | Publication et engagement X |

### `integrations/` — hooks hôte (marché, auth)

Callbacks enregistrés via `bootstrap.register_host_integrations()` — watchlist, rate limit, auth DB. Le cerveau reste agnostique du produit marché.

### `content/` + `doctrine/` — contenu embarqué

FAQ YAML, doctrine engineering — packagés via `pyproject.toml` `[package-data]`.

## Données runtime (`DATA_DIR`)

Sur Render : `/app/backend/data` (disque persistant 1 Go).

| Chemin | Contenu |
|--------|---------|
| `memory/` | Journaux markdown |
| `aria.db` | Cognitive + répertoire |
| `calibration_ledger.json` | Brier |
| `qi_judge_calibration.json` | Shadow juge QI |
| `capability_progress.json` | Niveaux QI |
| `truth-ledger/` | Événements vérité |
| `aria/avatar/` | Galerie identité |

## Deploy & pins (sans toucher au code)

1. Modifier `aria-core` → commit `aria-sandbox`
2. `aria-vanguard/backend/scripts/bump-aria-core-pin.ps1`
3. `operator/build-local.ps1` puis `deploy-render.ps1 -Reason "..."`  
   (1 redeploy — quota pipeline ~2 min)

**Pin actuel** (`requirements.txt`) : `1a5e0e0c`  
**HEAD sandbox** : voir `git rev-parse HEAD` après chaque session  
**Prod live** : vérifier `GET /api/health` → champ `commit`

## Tests

319 tests dans `packages/aria-core/tests/` — SSOT cerveau, sans hôte FastAPI complet.

```bash
cd packages/aria-core && pip install -e ".[dev]" && pytest tests -q
```

## Principes de modification (prod-safe)

1. **Pas de rename/move** de modules sans pin + deploy validé
2. **Nouveau skill** → `skills/` + entrée routage `brain.py` (changement import — deploy requis)
3. **Nouvelle donnée** → `knowledge/*.yaml` ou `DATA_DIR` (souvent sans deploy si lecture seule)
4. **Doc seule** → `docs/` — zéro risque prod

Voir `WHERE-TO-PUT.md` pour les règles détaillées.