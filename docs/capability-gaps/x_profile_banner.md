# Capability gap: `x_profile_banner`

Genere par ARIA le 2026-07-03T21:40:01.473287+00:00

## Contexte
apply_profile_banner a retourne False

## Fichiers cibles
- `packages/aria-core/src/aria_core/gateway/x_twitter.py`
- `packages/aria-core/src/aria_core/x_banner.py`
- `packages/aria-core/src/aria_core/self_maintenance.py`

## Criteres d'acceptation
- [ ] apply_profile_banner() upload via API X v1.1
- [ ] Boucle curiosite self_maintenance reussit sans blocage
- [ ] Test unitaire mock upload

## Boucle curiosite

1. Observer le gap
2. Ouvrir cette issue + PR spec
3. Implementer dans aria-core
4. Deploy Render + preuve operateur