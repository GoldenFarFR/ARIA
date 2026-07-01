# Patterns backend ARIA (référence aria-core + aria-vanguard)

À réutiliser quand un nouveau repo embarque un agent ou s'inspire de l'écosystème Aria.

## Architecture 2026

```
aria-sandbox/packages/aria-core/   # Cerveau SSOT (pip install)
├── skills/                        # Skills runtime ARIA
├── gateway/                       # Telegram, X
├── brain.py                       # Routing intents
└── knowledge/                     # YAML canonique

aria-vanguard/backend/             # Hôte deploy (FastAPI)
├── app/integrations/aria_host.py  # Plugins marché uniquement
├── app/api/routes/aria.py
└── requirements.txt               # pin SHA aria-core
```

**Règle :** ne pas dupliquer le cerveau dans un nouveau repo — consommer `aria-core` via pip ou appeler l'API holding.

## Checklist nouveau skill (aria-core)

1. `skills/<name>_skill.py` avec `execute_<name>()`
2. Intent dans `brain.py` `INTENT_PATTERNS`
3. Tests `packages/aria-core/tests/test_<name>.py`
4. Entrée FAQ/canonical si surface publique
5. Bump pin : `aria-vanguard/backend/scripts/bump-aria-core-pin.ps1` puis deploy

## Fichiers mémoire (runtime, pas git)

`data/` sur l'hôte Render ou local — initiative, ledger, truth-ledger.  
Template nouveau repo : documenter dans README, ne pas committer `data/`.

## Holding vs produit

- Site vitrine lambda → repo séparé (scaffold + `setup-holding-render.ps1`)
- API/agent → `aria-vanguard` (hôte unique) ou consommation API publique
- Cerveau → `aria-sandbox` / `aria-core` uniquement

## Opérateur (hors template public)

Scripts : `aria-vanguard/operator/`  
Secrets : `%LOCALAPPDATA%\GoldenFar\vault` (variable `GOLDENFAR_VAULT`)  
Audit : `operator/check-aria-status.ps1`