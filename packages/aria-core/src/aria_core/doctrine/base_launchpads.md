# BASE launchpads — pointer

**Runtime SSOT (scores, registry, verdict):** `aria_core/knowledge/base_launchpads.py`

- `LAUNCHPADS` tuple — all candidates with numeric scores
- `rank_launchpads()` / `primary_pick()` / `recommendation_verdict()` — live picks
- `registry_markdown()` — full registry for skills and LLM context

**Human narrative (no scores):** `GoldenFarFR/aria-token-base/docs/launchpad-selection.md`

Do not duplicate score tables in this file or in `directives.md` — they drift. Edit `base_launchpads.py` when market data changes.