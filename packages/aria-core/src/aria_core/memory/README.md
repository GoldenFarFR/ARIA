# aria_core.memory — façade mémoire (Phase B–D)

Point d'entrée cible pour la mémoire ARIA. **Désactivé par défaut** pour le vectoriel.

## Modules

| Fichier | Rôle | SSOT actuel |
|---------|------|-------------|
| `journal.py` | Journal épisodique markdown | `aria_core/memory.py` |
| `cognitive_sql.py` | Leçons approuvées SQLite | `knowledge/cognitive.py` |
| `llm_context.py` | `build_llm_context` + rappel vectoriel | Phase D |
| `values.py` | Valeurs opérationnelles (`aria_values.yaml`) | Phase E |
| `goals.py` | Objectifs opérationnels (`aria_goals.yaml` + état dynamique) | Phase F |
| `vector/chroma_store.py` | Embeddings Chroma embedded | Phase C |
| `vector/health.py` | Diagnostic Chroma (Phase 2 prep) | Phase 2 prep |
| `vector/schema.yaml` | Types `insight`, `lesson`, `reflection`, `decision` | — |

## Usage (nouveau code)

```python
from aria_core.memory import append, get_approved, is_vector_enabled

append("capability", "[qi-judge] indice monté")
items = await get_approved(limit=10)
```

Le code existant peut continuer à importer `aria_core.memory` (fichier) et
`aria_core.knowledge.cognitive` — aucun changement requis Phase B.

## Vector (opt-in) — Phase C

Install local :

```bash
pip install -e ".[dev,vector]"
```

```python
from aria_core.memory import is_vector_enabled, vector_store_status
from aria_core.memory.vector.chroma_store import store, search
```

Tant que `aria_vector_memory=false` (défaut) : `store()` et `search()` sont no-op.  
Données : `DATA_DIR/chroma/` (embedded, embeddings locaux ONNX via Chroma).

## Phase E — valeurs opérationnelles

SSOT : `knowledge/aria_values.yaml` — injecté dans `build_llm_context` (opérateur uniquement).

```python
from aria_core.memory import get_values_text, values_count
```

## Phase F — objectifs opérationnels

SSOT : `knowledge/aria_goals.yaml` — injecté dans `build_llm_context` (opérateur uniquement).  
État dynamique : QI global, revenu mois, objectifs personnels (`revenue_ledger.json`).

```python
from aria_core.memory import get_goals_text, goals_count
```

## Phase D — injection LLM

`build_llm_context` vit dans `llm_context.py` : journal + cognitive SQLite + rappel Chroma (si flag on).  
Filtre secrets (`sanitize_recall_text`) ; budget ~1200 car. pour le rappel vectoriel.

```python
from aria_core.memory import build_llm_context
ctx = await build_llm_context(public=False, query_hint="dernière question utilisateur")
```

Deploy Render : extra `[vector]` optionnel — **flag prod reste off** tant que non validé.