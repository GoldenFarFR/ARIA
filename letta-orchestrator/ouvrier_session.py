"""Mémoire courte KART — suite de conversation (regarde / d'accord / vas-y)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from aria_config import ARIA_REPO_ROOT
from ouvrier_vision import extract_image_paths

_SESSION_PATH = ARIA_REPO_ROOT / "memory" / "kart-session.json"
_CONTINUATION_RE = re.compile(
    r"(?i)^\s*(?:"
    r"d'?\s*accord|dac+ord|ok|oui|go|vas[- ]?y|allez[- ]?y|"
    r"regarde|vérifie|verifie|check|fais[- ]?le|lance|continue"
    r")\b"
)
_OPINION_RE = re.compile(
    r"(?i)(?:tu en pense|ton avis|qu'en pense|what do you think|"
    r"ton opinion|tu penses quoi|en penses?-tu|tu choisirais|choisirais quoi)"
)


def is_continuation(message: str) -> bool:
    text = (message or "").strip()
    if not text or len(text) > 80:
        return False
    return bool(_CONTINUATION_RE.search(text))


def load_session() -> dict:
    if not _SESSION_PATH.is_file():
        return {}
    try:
        data = json.loads(_SESSION_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_session(
    user_message: str,
    assistant_reply: str,
    *,
    image_path: str | None = None,
) -> None:
    body = (assistant_reply or "").strip()
    if not body or body in ("OK.", "OK"):
        return
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev = load_session()
    resolved_image = image_path
    if not resolved_image:
        for raw in extract_image_paths(user_message or ""):
            candidate = Path(raw)
            if candidate.is_file():
                resolved_image = str(candidate.resolve())
                break
    if not resolved_image:
        resolved_image = prev.get("last_image_path")
    payload = {
        "last_user": (user_message or "").strip()[:500],
        "last_reply": body[:800],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if resolved_image:
        payload["last_image_path"] = resolved_image
    _SESSION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def enrich_continuation(message: str) -> str:
    """Relie « regarde » / « d'accord » à la demande précédente."""
    if not is_continuation(message):
        return message
    session = load_session()
    prev = (session.get("last_user") or "").strip()
    if not prev:
        return (
            f"{message}\n\n(Pas de demande précédente en session — "
            "précise ce que je dois lire ou vérifier.)"
        )
    image_hint = ""
    img = (session.get("last_image_path") or "").strip()
    if img:
        image_hint = f"\nImage en session : {img}"
    return (
        f"[Suite — exécuter la demande précédente, pas de nouveau plan]\n"
        f"Sylvain avait demandé : {prev}\n"
        f"Sylvain confirme : {message}{image_hint}\n"
        "→ Lis les fichiers concernés (read_repo_file / run_powershell) et réponds "
        "avec un avis concret en français. Interdit : « je vais regarder », « une fois que j'ai lu »."
    )


def wants_opinion(message: str) -> bool:
    return bool(_OPINION_RE.search(message or ""))