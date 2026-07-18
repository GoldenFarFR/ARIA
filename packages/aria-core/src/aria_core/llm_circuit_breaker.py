"""Disjoncteur de fournisseur LLM — bascule le routage PAR DÉFAUT (celui que
``_resolve_routes`` utilise quand aucun ``provider``/``model`` explicite n'est passé
par l'appelant) vers un autre fournisseur, sans redéploiement.

Construit le 18/07 pour le cas concret « le solde x.ai (Grok) tombe à sec » — mais
générique (n'importe quelle raison peut armer le disjoncteur). État persisté sur
disque (`data_dir()/llm_circuit_breaker.json`), même patron que `outgoing_pause.py` :
- Fichier absent = état propre "jamais armé" (pas un doute, tout passe normalement).
- Fichier illisible/corrompu = loggé, traité comme "non armé" (fail-open sur le
  ROUTAGE — un disjoncteur cassé ne doit jamais faire taire toute la conversation
  d'ARIA ; contrairement à `wallet_guard`/`outgoing_pause(strict=True)` qui gèlent
  l'argent dans le doute, ici le pire cas d'un fail-open est "on continue d'essayer
  le fournisseur primaire", jamais une dépense incontrôlée).

N'affecte QUE le routage par défaut : un appelant qui passe déjà `provider=`
explicitement (ex. le tie-breaker momentum sur Haiku via OpenRouter, déjà indépendant
de Grok) n'est jamais impacté, armé ou non.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    return data_dir() / "llm_circuit_breaker.json"


def _read_raw() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("llm_circuit_breaker: état illisible/corrompu (%s) — traité comme non armé", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("llm_circuit_breaker: état de forme inattendue (%r) — traité comme non armé", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def get_override() -> dict[str, Any] | None:
    """Retourne l'override actif ({"provider", "model", "fallback_model", "reason",
    "since", "triggered_by"}) si armé, sinon None. Ne lève jamais — un état
    illisible/absent retombe silencieusement sur None (routage inchangé)."""
    raw = _read_raw()
    if not raw:
        return None
    if not raw.get("armed"):
        return None
    provider = str(raw.get("provider") or "").strip()
    if not provider:
        return None
    return raw


def is_armed() -> bool:
    return get_override() is not None


def arm(
    *,
    provider: str,
    model: str,
    fallback_model: str = "",
    reason: str,
    triggered_by: str = "system",
) -> dict[str, Any]:
    """Arme le disjoncteur. Écrase tout état précédent (une seule bascule active
    à la fois — pas d'empilement)."""
    payload = {
        "armed": True,
        "provider": provider.strip().lower(),
        "model": model.strip(),
        "fallback_model": fallback_model.strip(),
        "reason": reason.strip(),
        "triggered_by": str(triggered_by),
        "since": datetime.now(timezone.utc).isoformat(),
    }
    _write(payload)
    logger.warning(
        "llm_circuit_breaker: ARMÉ -> provider=%s model=%s (raison: %s, par: %s)",
        payload["provider"], payload["model"], payload["reason"], payload["triggered_by"],
    )
    return payload


def disarm(*, by: str = "operator") -> dict[str, Any]:
    payload = {
        "armed": False,
        "disarmed_by": str(by),
        "disarmed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(payload)
    logger.warning("llm_circuit_breaker: désarmé par %s", by)
    return payload


def status() -> dict[str, Any]:
    """Statut lisible pour /status ou un futur /api — jamais None (toujours un dict)."""
    override = get_override()
    if override:
        return {
            "armed": True,
            "provider": override.get("provider"),
            "model": override.get("model"),
            "fallback_model": override.get("fallback_model"),
            "reason": override.get("reason"),
            "since": override.get("since"),
        }
    return {"armed": False}
