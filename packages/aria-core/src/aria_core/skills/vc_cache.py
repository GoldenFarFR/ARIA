"""Cache TTL en mémoire pour les analyses VC coûteuses (scan + LLM).

Contexte : le VPS est sur-dimensionné (CPU ~0 %, RAM ~12 %). Le vrai coût d'une
analyse `/vc` est l'appel LLM (~30 s) + les tokens. Un même contrat redemandé
dans la fenêtre TTL renvoie le résultat mémorisé — **quasi instantané, zéro
token**. C'est le seul vrai levier de vitesse (cf. bilan perf).

Discipline :
- **Désactivé par défaut** (TTL absent/0). Activé en prod via `ARIA_VC_CACHE_TTL`
  (le Dockerfile le fixe à 300 s). Les tests hors-ligne ne sont donc pas pollués.
- **Facts-only compatible** : les faits on-chain bougent peu sur quelques minutes,
  et l'humain valide TOUJOURS l'ordre — un résultat vieux de ≤ TTL reste sûr.
- Clé = (contrat normalisé, langue) : deux langues = deux entrées distinctes.
- Borné (LRU + purge des expirés) : aucune fuite mémoire.
- Horloge injectable (`_now`) pour des tests déterministes sans `sleep`.
"""
from __future__ import annotations

import time as _time
from collections import OrderedDict

_CAP = 256
_now = _time.monotonic  # monkeypatchable en test

# clé -> (timestamp d'expiration, valeur)
_store: "OrderedDict[tuple, tuple[float, object]]" = OrderedDict()


def get(key):
    """Valeur mémorisée si présente ET non expirée, sinon ``None``."""
    entry = _store.get(key)
    if entry is None:
        return None
    expiry, value = entry
    if _now() >= expiry:
        _store.pop(key, None)
        return None
    _store.move_to_end(key)  # LRU : rafraîchit la récence
    return value


def put(key, value, ttl: float) -> None:
    """Mémorise ``value`` pour ``ttl`` secondes. ``ttl<=0`` = no-op (cache off)."""
    if ttl <= 0:
        return
    _purge_expired()
    _store[key] = (_now() + ttl, value)
    _store.move_to_end(key)
    while len(_store) > _CAP:
        _store.popitem(last=False)  # évince le plus ancien


def _purge_expired() -> None:
    now = _now()
    for k in [k for k, (exp, _) in _store.items() if now >= exp]:
        _store.pop(k, None)


def clear() -> None:
    """Vide le cache (tests, ou invalidation manuelle)."""
    _store.clear()


def size() -> int:
    return len(_store)
