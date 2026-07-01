"""Pont TOTP operateur — DESACTIVE (2026-06-20).

TOTP uniquement via agent IDE (Grok/Cursor) : Sylvain donne les 6 chiffres dans le chat,
scripts relances avec ``-TotpCode`` ou ``$env:GOLDENFAR_VAULT_TOTP_CODE``.
"""

from __future__ import annotations

# SSOT : plus de demande TOTP sur Telegram (latence, timeout, doublon avec IDE).
RELAY_ENABLED = False

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

_TOTP_CODE_RE = re.compile(r"^\d{6}$")
_TTL_SECONDS = 120


def _relay_path() -> Path:
    root = data_dir() / "totp_relay"
    root.mkdir(parents=True, exist_ok=True)
    return root / "pending.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict[str, Any] | None:
    path = _relay_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("totp_relay load failed: %s", exc)
        return None


def _save(data: dict[str, Any]) -> None:
    _relay_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _expire_if_needed(data: dict[str, Any]) -> dict[str, Any]:
    expires = datetime.fromisoformat(data["expires_at"])
    if data.get("status") == "pending" and _utcnow() >= expires:
        data["status"] = "expired"
        data.pop("code", None)
        _save(data)
    return data


def relay_disabled_payload(*, machine: str = "", purpose: str = "") -> dict[str, Any]:
    return {
        "disabled": True,
        "reason": "totp_ide_only",
        "message": "TOTP Telegram desactive — fournir le code dans le chat Grok/Cursor (-TotpCode).",
        "machine": machine,
        "purpose": purpose,
    }


async def create_request(*, machine: str, purpose: str = "vault-sync") -> dict[str, Any]:
    """Cree une demande TOTP — desactive : utiliser IDE agent."""
    if not RELAY_ENABLED:
        return relay_disabled_payload(machine=machine, purpose=purpose)
    now = _utcnow()
    req = {
        "request_id": uuid.uuid4().hex[:12],
        "machine": (machine or "unknown").strip()[:64],
        "purpose": (purpose or "vault-sync").strip()[:64],
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=_TTL_SECONDS)).isoformat(),
        "status": "pending",
    }
    _save(req)

    from aria_core.gateway.telegram_bot import notify_admin

    msg = (
        "🔐 Code GoldenFar Vault requis\n\n"
        f"Machine: {req['machine']}\n"
        f"Action: {req['purpose']}\n"
        f"Expire dans {_TTL_SECONDS // 60} min\n\n"
        "Reponds avec les 6 chiffres Google Authenticator (GoldenFar Vault)."
    )
    sent = await notify_admin(msg)
    req["telegram_notified"] = sent
    return {
        "request_id": req["request_id"],
        "expires_at": req["expires_at"],
        "telegram_notified": sent,
    }


def poll_request(request_id: str) -> dict[str, Any]:
    """Etat de la demande — code renvoye une seule fois puis efface."""
    if not RELAY_ENABLED:
        return {"status": "disabled", "request_id": request_id, "reason": "totp_ide_only"}
    data = _load()
    if not data or data.get("request_id") != request_id:
        return {"status": "missing", "request_id": request_id}

    data = _expire_if_needed(data)
    status = data.get("status", "pending")
    out: dict[str, Any] = {
        "status": status,
        "request_id": request_id,
        "expires_at": data.get("expires_at"),
    }
    if status == "fulfilled" and data.get("code"):
        out["code"] = data["code"]
        data.pop("code", None)
        data["status"] = "consumed"
        _save(data)
    return out


async def try_fulfill_from_admin_message(text: str, admin_id: int) -> str | None:
    """Si message admin = 6 chiffres et demande pending, enregistre le code."""
    if not RELAY_ENABLED:
        return None
    from aria_core.gateway.telegram_bot import is_admin

    if not is_admin(admin_id):
        return None
    clean = (text or "").strip()
    if not _TOTP_CODE_RE.fullmatch(clean):
        return None

    data = _load()
    if not data or data.get("status") != "pending":
        return None

    data = _expire_if_needed(data)
    if data.get("status") != "pending":
        return "Demande expiree — relance l'action sur le PC."

    data["status"] = "fulfilled"
    data["code"] = clean
    data["fulfilled_at"] = _utcnow().isoformat()
    _save(data)
    return (
        "✅ Code recu — je le transmets au PC.\n"
        f"Machine: {data.get('machine', '?')}"
    )