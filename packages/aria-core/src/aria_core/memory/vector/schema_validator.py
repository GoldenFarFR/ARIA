"""Vector entry validation — ``schema.yaml`` schema."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_SCHEMA_PATH = Path(__file__).with_name("schema.yaml")


def load_schema() -> dict[str, Any]:
    if not _SCHEMA_PATH.exists():
        return {}
    return yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8")) or {}


def collection_name() -> str:
    schema = load_schema()
    return str((schema.get("collection") or {}).get("name") or "aria_cognitive_vectors")


def validate_entry(entry_type: str, metadata: dict[str, Any] | None) -> tuple[bool, str]:
    schema = load_schema()
    types = schema.get("entry_types") or {}
    if entry_type not in types:
        return False, f"unknown entry_type: {entry_type}"
    meta = dict(metadata or {})
    required = list(types[entry_type].get("required_metadata") or [])
    missing = [k for k in required if not meta.get(k)]
    if missing:
        return False, f"missing metadata for {entry_type}: {', '.join(missing)}"
    return True, ""


def normalize_metadata(entry_type: str, metadata: dict[str, Any] | None) -> dict[str, str]:
    """Metadata serialized to JSON on the store side — everything is stringified except native str/int/float/bool."""
    out: dict[str, str] = {"entry_type": entry_type}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = str(value) if not isinstance(value, str) else value
        else:
            out[key] = str(value)[:500]
    return out