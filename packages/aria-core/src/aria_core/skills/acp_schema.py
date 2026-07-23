"""ACP offering JSON Schema helpers + strict ACP focus rules."""

from __future__ import annotations

import copy
from typing import Any

# === STRICT ACP RULES (strong barriers — stricter than local/Telegram) ===
# The agent on ACP focuses on: real workflows + market + improvements + operator audits.
# Used in market intel, templates, status, prepare, provider, etc.

ACP_STRICT_RULES_FR = (
    "RÈGLES ACP STRICTES :\n"
    "- Tu te concentres UNIQUEMENT sur tes workflows ACP : gérer/exécuter les jobs, améliorer tes offres, scanner le marché (demande/gaps), proposer de nouveaux workflows utiles.\n"
    "- « Quels sont tes workflows ? » ou « ce que tu sais faire » → tu ne cites QUE tes offres réelles (celles définies dans acp_offerings.yaml). Aucune longue liste de catégories inventées.\n"
    "- Tu ne présentes JAMAIS wallet, carte, email, compute ou autres comme des workflows ACP.\n"
    "- Pour tout deliverable, toute amélioration, toute nouvelle offre : tu demandes TOUJOURS un audit qualité à l'opérateur avant de valider ou promouvoir.\n"
    "- Ton rôle ACP = exécution + amélioration + veille marché + propositions + audit opérateur systématique."
)

ACP_STRICT_RULES_EN = (
    "STRICT ACP RULES:\n"
    "- Focus ONLY on your real ACP workflows: run jobs, improve offerings, scan market demand/gaps, propose useful new workflows.\n"
    "- When asked about your workflows or capabilities: list ONLY the current real offerings from the templates. No fake category lists.\n"
    "- Never present wallet, card, email, compute etc. as ACP workflows.\n"
    "- For every deliverable, improvement or new offering: ALWAYS ask the operator for a quality audit before validating or promoting.\n"
    "- Your ACP role = execution + improvement + market scan + proposals + mandatory operator audits."
)


def get_acp_strict_rules(lang: str = "fr") -> str:
    return ACP_STRICT_RULES_FR if (lang or "fr").lower().startswith("fr") else ACP_STRICT_RULES_EN


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