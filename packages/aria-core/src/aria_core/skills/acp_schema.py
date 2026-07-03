"""ACP offering JSON Schema helpers."""

from __future__ import annotations

import copy
from typing import Any


def enrich_json_schema(
    schema: dict[str, Any] | None,
    *,
    title: str = "",
    description: str = "",
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach ACP metadata to a JSON Schema object."""
    out: dict[str, Any] = copy.deepcopy(schema) if schema else {"type": "object", "properties": {}}
    if title:
        out["title"] = title
    if description:
        out["description"] = description
        props = out.get("properties")
        if isinstance(props, dict):
            for field in props.values():
                if isinstance(field, dict) and not field.get("description"):
                    field["description"] = description
    if examples is not None:
        out["examples"] = examples
    return out