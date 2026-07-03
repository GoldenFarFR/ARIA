"""État quota Cursor Pro — saisi depuis cursor.com/dashboard/usage (pas d'API publique)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def cursor_usage_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    path = Path(base) / "GoldenFar" / "cursor-usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def default_cursor_usage() -> dict[str, Any]:
    return {
        "plan": "pro",
        "composer_pool_pct": None,
        "api_pool_pct": None,
        "updated_at": None,
        "source": "cursor.com/dashboard/usage",
    }


def load_cursor_usage() -> dict[str, Any]:
    path = cursor_usage_path()
    if not path.is_file():
        return default_cursor_usage()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {**default_cursor_usage(), **data}
    except (OSError, json.JSONDecodeError):
        pass
    return default_cursor_usage()


def save_cursor_usage(doc: dict[str, Any]) -> dict[str, Any]:
    merged = {**default_cursor_usage(), **(doc or {})}
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = cursor_usage_path()
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


def update_cursor_usage(
    *,
    composer_pool_pct: float | None = None,
    api_pool_pct: float | None = None,
    plan: str | None = None,
) -> dict[str, Any]:
    doc = load_cursor_usage()
    if composer_pool_pct is not None:
        doc["composer_pool_pct"] = max(0.0, min(100.0, float(composer_pool_pct)))
    if api_pool_pct is not None:
        doc["api_pool_pct"] = max(0.0, min(100.0, float(api_pool_pct)))
    if plan:
        doc["plan"] = plan.strip().lower()
    return save_cursor_usage(doc)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/d"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "n/d"


def _fmt_updated(iso: str | None) -> str:
    if not iso:
        return "jamais"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m %Hh")
    except ValueError:
        return "?"


def format_cursor_usage_dashboard(*, lang: str = "fr") -> str:
    doc = load_cursor_usage()
    plan = (doc.get("plan") or "pro").upper()
    comp = _fmt_pct(doc.get("composer_pool_pct"))
    api = _fmt_pct(doc.get("api_pool_pct"))
    maj = _fmt_updated(doc.get("updated_at"))
    if lang == "fr":
        return f"plan {plan} | Composer {comp} | API {api} | maj {maj}"
    return f"plan {plan} | Composer {comp} | API {api} | updated {maj}"