# Où placer quoi — aria-core

> **Phase A + B** — règles pour les prochains ajouts sans casser la prod.  
> Dernière mise à jour : 2026-06-20

## Règle d'or

| Question | Réponse |
|----------|---------|
| Ça **exécute** une action (GitHub, Telegram, patch site) ? | → `skills/` |
| C'est du **savoir durable** (rubric, backlog, pièges) ? | → `knowledge/` |
| C'est un **fait vérifié** canonique ? | → `truth_ledger/` |
| C'est une **entrée journal** horodatée ? | → `aria_core.memory.append()` ou `append_memory()` |
| C'est une **leçon approuvée** long terme ? | → `aria_core.memory.add_knowledge()` |
| C'est un **embedding / recall vectoriel** ? | → `aria_core.memory.vector` (stub, opt-in) |
| C'est de la **config opérateur** ? | → `aria-vanguard/operator/` (pas aria-core) |
| C'est un **workflow IDE** ? | → `aria-skills/.grok/skills/` |
| C'est de la **doc** ? | → `packages/aria-core/docs/` |

## Nouveau skill

```
skills/mon_skill.py
  ├── execute_mon_skill(message, lang) -> ChatResponse | dict
  ├── wants_mon_skill(message) -> bool   (optionnel)
  └── tests/test_mon_skill.py
```

**Obligatoire après ajout :**
1. Import + branche dans `brain.py` (⚠️ change les imports — deploy requis)
2. Entrée dans `models.SkillName` si exposé publiquement
3. Test unitaire dans `tests/`
4. Si skill touche GitHub prod : vérifier `GITHUB_WRITE_REPOS` + repos protégés

**Ne pas** mettre la logique métier lourde à la racine `aria_core/` — garder la racine pour orchestration.

## Mémoire — package `aria_core/memory/` (Phase B)

**Nouveau code** : importer depuis `aria_core.memory` (façade).  
**Ancien code** : `append_memory`, `build_llm_context`, etc. — **toujours valides** (rétrocompat).

| Besoin | Module | API |
|--------|--------|-----|
| Log session, heartbeat | `memory/journal.py` | `append()`, `append_memory()` |
| Leçon approuvée SQLite | `memory/cognitive_sql.py` | `add_knowledge()`, `get_approved()` |
| Embedding sémantique | `memory/vector/` | `store()`, `search()` — **stub, flag off** |
| Fait canonique | `truth_ledger/store.py` | hors package memory |
| Calibration Brier | `knowledge/calibration_ledger.py` | hors package memory |
| Progression QI (fichier) | `DATA_DIR/memory/capability_progress.json` | via `capability_levels.py` |

```
aria_core/memory/
  ├── __init__.py         # façade + exports legacy
  ├── journal.py          # journal markdown
  ├── cognitive_sql.py    # SQLite cognitive_knowledge
  ├── _legacy_journal.py  # impl SSOT journal (ex-memory.py)
  ├── vector/
  │   ├── chroma_store.py # stub Chroma
  │   └── schema.yaml     # insight | lesson | reflection | decision
  └── README.md
```

Flag opérateur : `aria_vector_memory=false` (défaut). Install local : `pip install -e ".[dev,vector]"`.  
Ingest auto : `approve_knowledge()` → `vector/ingest.py` si flag on. Test : `scripts/test-vector-memory.ps1 -EnableVector`.

**Ne pas** ajouter de stockage mémoire hors `memory/`, `truth_ledger/` ou `knowledge/calibration_ledger.py`.

## Knowledge — YAML vs Python

| Format | Quand |
|--------|-------|
| `knowledge/*.yaml` | Données statiques, rubrics, listes, config lue au boot |
| `knowledge/*.py` | Logique (épistémique, curriculum, triage, cognitive CRUD) |

Nommage YAML : `snake_case.yaml`. Charger via chemin relatif au module (`Path(__file__).parent`).

**Pièges opérateur** : toujours `knowledge/operator_pitfalls.yaml` (SSOT machine) + miroir humain `aria-vanguard/operator/OPERATOR-RUNBOOK.md`.

## Truth ledger

| Fichier | Rôle |
|---------|------|
| `canonical_facts.yaml` | Faits seed embarqués |
| `canonical.py` | Chargement + merge |
| `store.py` | Append événements DATA_DIR |
| `sync.py` | Batch GitHub |

Nouveau fait canonique : d'abord événement store, promotion via `canonical_promotion.py` — pas d'édition manuelle YAML en prod sans review.

## Gateway & I/O

| Canal | Dossier |
|-------|---------|
| Telegram | `gateway/telegram_bot.py` |
| X / Twitter | `gateway/x_twitter.py`, `x_engagement.py` |
| Politique publication | `x_publication_policy.py` (racine — lié identité) |

Pas de HTTP handlers ici — ils vivent dans `aria-vanguard/backend/app/`.

## Heartbeat — nouvelle tâche autonome

1. Ajouter `HeartbeatTask` dans `heartbeat.py` → `HEARTBEAT_TASKS`
2. Branch `elif task_id == "..."` dans la boucle
3. Test si logique non triviale
4. Intervalle ≥ 30 min pour tâches coûteuses (quota API, GitHub)

## Tests

| Type | Emplacement |
|------|-------------|
| Unit skill | `tests/test_*_skill.py` |
| Knowledge / épistémique | `tests/test_epistemic_*.py` |
| Intégration légère | `tests/test_*_phase*.py` |

Convention : `pytest -q` depuis `packages/aria-core`. Pas de tests dans `src/`.

## Ce qui ne va **pas** dans aria-core

| Élément | Bon repo |
|---------|----------|
| Secrets, `production.env` | `aria-vanguard/operator` (coffre local) |
| Frontend React | `aria-vanguard/product-frontend` |
| Scripts deploy Render | `aria-vanguard/operator/` |
| Skills Cursor/Grok | `aria-skills` |
| Mémoire collègue humain | `collegue-memoire/COLLEGUE.md` |
| Journal actions IDE | `collegue-memoire/JOURNAL.md` |

## Checklist avant merge (cerveau)

- [ ] `pytest tests -q` local (319+ tests)
- [ ] `build-local.ps1` si le hôte importe le changement
- [ ] Pas de rename module sans nécessité absolue
- [ ] Pin bump + **un seul** `deploy-render.ps1` si deploy requis
- [ ] Doc mise à jour si nouvelle zone (`docs/` ou commentaire YAML)

## Anti-patterns observés

| ❌ Éviter | ✅ Préférer |
|----------|------------|
| Nouveau fichier à la racine par défaut | Sous-dossier thématique |
| Dupliquer mémoire fichier + SQLite + JSON | Une couche par type (voir tableau mémoire) |
| `sync-render` en rafale | `deploy-render.ps1` groupé |
| CI sur chaque push main | `build-local.ps1` + CI sur PR |
| Toucher imports pour de la doc | `docs/` uniquement |