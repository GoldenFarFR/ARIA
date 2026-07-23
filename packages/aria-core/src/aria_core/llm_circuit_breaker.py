"""LLM provider circuit breaker — switches the DEFAULT routing (the one
``_resolve_routes`` uses when no explicit ``provider``/``model`` is passed by
the caller) to another provider, with no redeploy.

Built on 07/18 for the concrete case "the x.ai (Grok) balance runs dry" — but
generic (any reason can arm the breaker). State persisted to disk
(`data_dir()/llm_circuit_breaker.json`), same pattern as `outgoing_pause.py`:
- Missing file = clean "never armed" state (not a doubt, everything passes
  through normally).
- Unreadable/corrupted file = logged, treated as "not armed" (fail-open on
  ROUTING — a broken breaker must never silence all of ARIA's conversation;
  unlike `wallet_guard`/`outgoing_pause(strict=True)` which freeze money when
  in doubt, here the worst case of a fail-open is "we keep trying the primary
  provider," never an uncontrolled spend).

Affects ONLY the default routing: a caller that already passes `provider=`
explicitly (e.g. the momentum tie-breaker on Haiku via OpenRouter, already
independent of Grok) is never impacted, armed or not.
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
        logger.warning("llm_circuit_breaker: unreadable/corrupted state (%s) — treated as not armed", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("llm_circuit_breaker: unexpected state shape (%r) — treated as not armed", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def get_override() -> dict[str, Any] | None:
    """Returns the active override ({"provider", "model", "fallback_model", "reason",
    "since", "triggered_by"}) if armed, otherwise None. Never raises — an
    unreadable/missing state silently falls back to None (routing unchanged)."""
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
    """Arms the breaker. Overwrites any previous state (only one active switch
    at a time — no stacking)."""
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
        "llm_circuit_breaker: ARMED -> provider=%s model=%s (reason: %s, by: %s)",
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
    logger.warning("llm_circuit_breaker: disarmed by %s", by)
    return payload


def status() -> dict[str, Any]:
    """Readable status for /status or a future /api — never None (always a dict)."""
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
