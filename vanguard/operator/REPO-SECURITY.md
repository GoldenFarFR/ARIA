# Sécurité repos GoldenFar

## Règle

| Quoi | Où |
|------|-----|
| **Secrets** (clés API, tokens) | Coffre `%LOCALAPPDATA%\GoldenFar\vault` — jamais Git |
| **Scripts opérateur** | `aria-vanguard/operator/` — exemples `.example` seulement |
| **Code produit** | `aria-vanguard`, `aria-sandbox`, etc. — repos privés GitHub |

## Repos actifs (2026-06-19)

| Repo | Rôle |
|------|------|
| `aria-vanguard` | Holding + API + scripts `operator/` |
| `aria-sandbox` | Cerveau `aria-core` |
| `aria-skills` | Skills IDE |
| `collegue-memoire` | Mémoire opérateur (hors produit ARIA) |
| `aria-local-sync` | État local multi-PC (mémoire ARIA, IDE, métier) — **pas de secrets** |

Repos supprimés : `dexpulse`, `aria-vanguard/operator` (fusionnés dans `aria-vanguard`).

## Ne jamais commiter

- `production.env`, `local.env`, `vanguard.env`
- `keys/*.api-key`
- `stripe/recovery-codes.txt`