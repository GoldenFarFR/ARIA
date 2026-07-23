"""ACP offering workflows — templates YAML + create/update via acp-cli."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.skills import acp_cli
from aria_core.skills.acp_schema import enrich_json_schema, get_acp_strict_rules

_TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_offerings.yaml"
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_config.yaml"

_CREATE_RE = re.compile(
    r"(?:"
    r"cr[ée]er?\s+(?:une?\s+)?(?:offre|offering|workflow)\s+acp"
    r"|create\s+(?:acp\s+)?(?:offering|workflow)"
    r"|acp\s+(?:offering|workflow)\s+create"
    r"|publier\s+(?:offre|workflow)\s+acp"
    r")",
    re.I,
)
_TEMPLATES_RE = re.compile(
    r"templates?\s+(?:offres?|offerings?|workflows?)\s+acp"
    r"|liste\s+(?:les\s+)?templates?\s+acp"
    r"|(?:^|\s)acp\s+templates?\s*$",
    re.I,
)
_TEMPLATE_EXPLICIT_RE = re.compile(r"template\s+([a-z][a-z0-9_]+)", re.I)
_RESERVED_KEYS = frozenset({"acp", "offre", "offering", "workflow", "template"})
_PRICE_RE = re.compile(r"(?:prix|price)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.I)

_DELETE_ALL_RE = re.compile(
    r"(?i)\b(?:supprim\w*|retir\w*|effac\w*|delete|remove)\b"
    r".*\b(?:tous|toutes|all|every)\b"
    r".*\b(?:workflow|offre|offering|workflows|offres|offerings)\b"
    r"|\b(?:supprim\w*|delete)\b.*\b(?:workflow|offre|offering)s?\b.*\bacp\b"
)
_DELETE_HEAD_RE = re.compile(
    r"(?i)\b(?:supprim\w*|retir\w*|effac\w*|delete|remove)\b"
    r".{0,40}?\b(?:workflow|offre|offering)\b"
)
_DELETE_NAME_RE = re.compile(
    r"(?i)\b(?:workflow|offre|offering)\b(?:\s+acp)?\s+(.+)$"
)
_DELETE_STOP = frozenset(
    {"maintenant", "sur", "acp", "stp", "svp", "please", "now", "immediatement", "immédiatement"}
)

_ADHOC_RE = re.compile(
    r"(?i)(?:"
    r"cr[ée]e[r]?\s+(?:un\s+)?(?:workflow|offre|offering)"
    r"|nouveau\s+workflow"
    r"|lancer\s+(?:un\s+)?(?:workflow|produit|offre)"
    r"|ajoute[r]?\s+(?:un\s+)?workflow"
    r").*\bacp\b"
    r"|\bacp\b.*(?:cr[ée]er|nouveau|appeler|workflow|offre)"
)
_NAME_RE = re.compile(r"(?i)(?:appeler|nomm(?:é|e)|named?)\s+([a-z][a-z0-9_]*)")
_NAME_FALLBACK_RE = re.compile(r"(?i)workflow\s+([a-z][a-z0-9_]*)")
_PRICE_DOLLARS_CENTS_RE = re.compile(
    r"(?i)(\d+)\s*(?:\$|dollars?)?\s*(?:et\s+)?(\d{1,2})\s*(?:centimes?|cents?)"
)
_PRICE_FLOAT_RE = re.compile(r"(?i)(?:à|a|@|pour)\s*(\d+(?:[.,]\d{1,2})?)\s*\$?|(\d+[.,]\d{2})\s*\$")
_DESC_RE = re.compile(
    r"(?i)qui propose\s+(.+?)(?:\.\s*$|\.\s+(?:sur|et)\s+|\s*$)"
    r"|proposant\s+(.+?)(?:\.\s*$|\s*$)"
    r"|services?\s+d[''']?(.+?)(?:\.\s*$|\s*$)"
)


@lru_cache(maxsize=1)
def _load_acp_config() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _subscription_update_kw(payload: dict[str, Any]) -> dict[str, str]:
    sid = str(payload.get("subscription_ids") or resolve_subscription_ids() or "").strip()
    return {"subscription_ids": sid} if sid else {}


def resolve_subscription_ids(*, attach_full_access: bool = True) -> str:
    """
    Subscription UUIDs to attach to offerings (comma-separated).
    SSOT: ARIA_ACP_SUBSCRIPTION_IDS > acp_config.yaml > acp subscription list.
    """
    env = (os.environ.get("ARIA_ACP_SUBSCRIPTION_IDS") or "").strip()
    if env:
        return env
    if not attach_full_access:
        return ""
    doc = _load_acp_config()
    subs = doc.get("subscriptions") or {}
    if isinstance(subs, dict):
        full = subs.get("aria_full_access") or {}
        if isinstance(full, dict):
            sid = str(full.get("id") or "").strip()
            if sid:
                return sid
    rows, _ = acp_cli.list_subscriptions()
    for row in rows or []:
        name = str(row.get("name") or "").strip().lower()
        if name == "aria_full_access":
            sid = str(row.get("id") or "").strip()
            if sid:
                return sid
    return ""


@lru_cache(maxsize=1)
def _load_templates_doc() -> dict[str, Any]:
    if not _TEMPLATES_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_TEMPLATES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_offering_templates() -> dict[str, dict[str, Any]]:
    doc = _load_templates_doc()
    raw = doc.get("templates") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def template_dashboard_examples(template: dict[str, Any]) -> tuple[str, str]:
    """Human-readable example text (logs / Telegram)."""
    req = str(template.get("sample_request") or "").strip()
    deliv = str(template.get("sample_deliverable") or "").strip()
    name = str(template.get("name") or "offering")
    if not req:
        req = f"Premium scope request for {name} — include all context for a complete deliverable."
    if not deliv:
        deliv = f"Structured premium deliverable for {name} — summary first, full report attached."
    return req, deliv


def template_example_objects(template: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """JSON objects injected into requirements/deliverable.examples (Virtuals API)."""
    req = template.get("requirement_example")
    deliv = template.get("deliverable_example")
    name = str(template.get("name") or "offering")
    if isinstance(req, dict) and isinstance(deliv, dict):
        return req, deliv
    text_req, text_deliv = template_dashboard_examples(template)
    return (
        {"brief": text_req} if name else {"brief": text_req},
        {"summary": text_deliv, "report": text_deliv},
    )


def premium_example_objects(kind: str, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Structured examples for adhoc workflows."""
    if kind == "x_account":
        return (
            {
                "xHandle": "@ExampleBuilder",
                "objective": "ZHC partnership fit — compare vs @GoldenFarFR",
                "timeHorizonDays": 30,
                "competitorHandles": "@GoldenFarFR",
            },
            {
                "relevanceScore": "78/100 — consistent builder narrative",
                "executiveSummary": "Strong thesis alignment; moderate engagement quality.",
                "engagementAnalysis": "Steady cadence; replies skew builder/operator audience.",
                "narrativeAlignment": "Aligns Vanguard ship-in-public + ACP marketplace thesis.",
                "botRiskFlags": "No strong bot-farm pattern in sample window.",
                "recommendation": "WATCH",
                "fullReport": "Markdown report — revisit after next ship post.",
            },
        )
    if kind == "quantitative":
        return (
            {
                "brief": "Quant review BASE token — liquidity depth, holder concentration, 7d flow.",
                "symbols": "0xabc…",
                "dataSources": "on-chain Base",
            },
            {
                "summary": "Elevated concentration risk; liquidity adequate for small size only.",
                "metrics": "top10 holders 62%; 7d net flow -4.2%",
                "assumptions": "Snapshot block N; no CEX flow included.",
                "riskFlags": "Whale exit would thin book quickly.",
                "report": "Full quantitative markdown report.",
            },
        )
    return (
        {"brief": f"Premium scope for {name.replace('_', ' ')}."},
        {"summary": "Executive summary.", "report": "Full structured report.", "metrics": "Key scores."},
    )


def _normalize_offering_name(raw: str) -> str:
    parts = re.split(r"[\s_\-]+", (raw or "").strip().lower())
    cleaned: list[str] = []
    for part in parts:
        if part in _DELETE_STOP:
            break
        if part:
            cleaned.append(part)
    return "_".join(cleaned)


def parse_delete_workflow_name(message: str) -> str | None:
    """Extracts the name of an ACP offering to delete (e.g. test 1 → test_1)."""
    text = (message or "").strip()
    if not text or not _DELETE_HEAD_RE.search(text):
        return None
    m = _DELETE_NAME_RE.search(text)
    if not m:
        return None
    name = _normalize_offering_name(m.group(1))
    return name or None


def wants_acp_offering_delete_all(message: str) -> bool:
    text = (message or "").strip()
    if not text or not re.search(r"(?i)\bacp\b", text):
        return False
    return bool(_DELETE_ALL_RE.search(text))


def wants_acp_offering_delete(message: str) -> bool:
    if wants_acp_offering_delete_all(message):
        return True
    return parse_delete_workflow_name(message) is not None


def wants_adhoc_acp_workflow(message: str) -> bool:
    text = (message or "").strip()
    if not text or _TEMPLATE_EXPLICIT_RE.search(text):
        return False
    if not _ADHOC_RE.search(text):
        return False
    return parse_adhoc_workflow(text) is not None


def wants_acp_offering_create(message: str) -> bool:
    text = (message or "").strip()
    if wants_adhoc_acp_workflow(text):
        return False
    return bool(_CREATE_RE.search(text))


def wants_acp_offering_templates(message: str) -> bool:
    return bool(_TEMPLATES_RE.search((message or "").strip()))


def _parse_template_key(message: str) -> str | None:
    m = _TEMPLATE_EXPLICIT_RE.search(message or "")
    if m:
        return m.group(1).lower()
    lower = (message or "").lower()
    for key in sorted(load_offering_templates(), key=len, reverse=True):
        if key in lower:
            return key
    return None


def _parse_price_override(message: str) -> float | None:
    text = message or ""
    m = _PRICE_DOLLARS_CENTS_RE.search(text)
    if m:
        return float(m.group(1)) + float(m.group(2)) / 100.0
    m = _PRICE_FLOAT_RE.search(text)
    if m:
        raw = (m.group(1) or m.group(2) or "").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            pass
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_adhoc_workflow(message: str) -> dict[str, Any] | None:
    """Extracts name, price, description from natural language."""
    text = (message or "").strip()
    if not text:
        return None
    name_m = _NAME_RE.search(text) or _NAME_FALLBACK_RE.search(text)
    if not name_m:
        return None
    name = name_m.group(1).lower()
    if name in _RESERVED_KEYS:
        return None
    price = _parse_price_override(text)
    if price is None or price <= 0:
        return None
    desc_m = _DESC_RE.search(text)
    description = ""
    if desc_m:
        description = next((g.strip() for g in desc_m.groups() if g), "")
    if not description:
        description = f"Service ARIA — {name.replace('_', ' ')}."
    sla = 60 if any(w in text.lower() for w in ("quantitatif", "analyse", "audit", "rapport")) else 15
    return {
        "name": name,
        "description": description[:500],
        "price_usd": price,
        "sla_minutes": sla,
    }


def _infer_service_kind(spec: dict[str, Any]) -> str:
    text = f"{spec.get('name', '')} {spec.get('description', '')}".lower()
    if any(w in text for w in ("compte x", "twitter", " x ", "x account", "pertinence")):
        return "x_account"
    if any(w in text for w in ("quantitatif", "quantitative", "audit", "token", "crypto")):
        return "quantitative"
    return "generic"


def _premium_description(spec: dict[str, Any], kind: str) -> str:
    name = str(spec["name"]).replace("_", " ")
    if kind == "x_account":
        return (
            f"{name.upper()}: Premium X account relevance audit — audience fit, narrative "
            "alignment with ZHC/Vanguard, engagement quality, bot-risk heuristics, and "
            "actionable outreach verdict. Structured report, not vibes."
        )
    if kind == "quantitative":
        return (
            f"{name.upper()}: Premium quantitative analysis — scoped metrics, risk framing, "
            "assumption ledger, and executive verdict. Over-delivered detail for operator-grade "
            "decisions."
        )
    return (
        f"{name.upper()}: Premium ARIA service — explicit scope, structured deliverables, "
        "and operator-ready detail. Built for revenue-grade marketplace quality."
    )


def _premium_schemas(
    name: str, kind: str
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    if kind == "x_account":
        requirements = {
            "type": "object",
            "required": ["xHandle", "objective"],
            "properties": {
                "xHandle": {
                    "type": "string",
                    "description": "Target X handle (with or without @).",
                },
                "objective": {
                    "type": "string",
                    "description": "Why this account matters — partnership, watch, or outreach.",
                },
                "competitorHandles": {
                    "type": "string",
                    "description": "Optional peer accounts for comparative context.",
                },
                "timeHorizonDays": {
                    "type": "integer",
                    "description": "Lookback window for posts and engagement (default 30).",
                },
            },
        }
        deliverable = {
            "type": "object",
            "required": [
                "relevanceScore",
                "executiveSummary",
                "engagementAnalysis",
                "recommendation",
            ],
            "properties": {
                "relevanceScore": {
                    "type": "string",
                    "description": "0-100 score with one-line rationale.",
                },
                "executiveSummary": {
                    "type": "string",
                    "description": "Top findings — fit, risks, opportunity.",
                },
                "engagementAnalysis": {
                    "type": "string",
                    "description": "Quality of audience interaction and posting cadence.",
                },
                "narrativeAlignment": {
                    "type": "string",
                    "description": "Alignment with ZHC / Vanguard / operator thesis.",
                },
                "botRiskFlags": {
                    "type": "string",
                    "description": "Heuristic bot/farm signals if any.",
                },
                "recommendation": {
                    "type": "string",
                    "enum": ["PURSUE", "WATCH", "SKIP"],
                    "description": "Clear next action for the buyer.",
                },
                "fullReport": {
                    "type": "string",
                    "description": "Full structured markdown report.",
                },
            },
        }
        req_desc = "X handle, objective, optional peers — enough context for a premium audit."
        deliv_desc = "Scores, engagement breakdown, alignment note, and PURSUE/WATCH/SKIP verdict."
        return requirements, deliverable, req_desc, deliv_desc

    if kind == "quantitative":
        requirements = {
            "type": "object",
            "required": ["brief"],
            "properties": {
                "brief": {
                    "type": "string",
                    "description": "Client brief — scope, hypotheses, and decision context.",
                },
                "symbols": {
                    "type": "string",
                    "description": "Tickers, pairs, or contract addresses.",
                },
                "dataSources": {
                    "type": "string",
                    "description": "Preferred sources or constraints (on-chain, CEX, social).",
                },
            },
        }
        deliverable = {
            "type": "object",
            "required": ["summary", "report", "metrics", "assumptions"],
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Executive summary — verdict first.",
                },
                "metrics": {
                    "type": "string",
                    "description": "Key quantitative metrics, tables, and scores.",
                },
                "assumptions": {
                    "type": "string",
                    "description": "Explicit assumptions and data limitations.",
                },
                "riskFlags": {
                    "type": "string",
                    "description": "Material risks and sensitivity notes.",
                },
                "report": {
                    "type": "string",
                    "description": "Full structured analysis report (markdown).",
                },
            },
        }
        req_desc = "Brief plus optional symbols/sources — scoped for deep quantitative work."
        deliv_desc = "Summary, metrics, assumption ledger, risks, and full report."
        return requirements, deliverable, req_desc, deliv_desc

    requirements = {
        "type": "object",
        "required": ["brief"],
        "properties": {
            "brief": {
                "type": "string",
                "description": "Client brief — scope, assets, or questions.",
            },
            "symbols": {
                "type": "string",
                "description": "Optional tickers or contract addresses.",
            },
        },
    }
    deliverable = {
        "type": "object",
        "required": ["report", "summary"],
        "properties": {
            "summary": {
                "type": "string",
                "description": "Executive summary of findings.",
            },
            "report": {
                "type": "string",
                "description": "Full structured analysis report.",
            },
            "metrics": {
                "type": "string",
                "description": "Key quantitative metrics and scores.",
            },
        },
    }
    return requirements, deliverable, f"What the client provides for {name}.", f"Structured deliverable for {name}."


def premium_examples(kind: str, name: str) -> tuple[str, str]:
    """Sample request + deliverable for Virtuals dashboard (manual paste)."""
    if kind == "x_account":
        return (
            "Audit @ExampleBuilder for ZHC partnership fit — 30d horizon, compare vs @GoldenFarFR.",
            (
                "relevanceScore: 78/100 — consistent builder narrative, moderate engagement quality.\n"
                "recommendation: WATCH — strong thesis alignment, revisit after next ship post."
            ),
        )
    if kind == "quantitative":
        return (
            "Quant review: BASE token 0xabc… — liquidity depth, holder concentration, 7d flow.",
            (
                "summary: Elevated concentration risk; liquidity adequate for small size only.\n"
                "metrics: top10 holders 62%; 7d net flow -4.2%."
            ),
        )
    return (
        f"Scope premium analysis for {name.replace('_', ' ')} — deliver executive summary + full report.",
        "summary: Findings condensed. report: Full markdown with metrics and next steps.",
    )


def build_adhoc_payload(spec: dict[str, Any]) -> dict[str, Any]:
    name = str(spec["name"])
    kind = _infer_service_kind(spec)
    desc = _premium_description(spec, kind)
    requirements, deliverable, req_desc, deliv_desc = _premium_schemas(name, kind)
    req_ex, deliv_ex = premium_example_objects(kind, name)
    payload = {
        "name": name,
        "description": desc,
        "price_value": float(spec["price_usd"]),
        "sla_minutes": int(spec.get("sla_minutes") or 15),
        "service_kind": kind,
        "requirements": enrich_json_schema(
            requirements,
            title=f"{name} — requirements",
            description=req_desc,
            examples=[req_ex],
        ),
        "deliverable": enrich_json_schema(
            deliverable,
            title=f"{name} — deliverable",
            description=deliv_desc,
            examples=[deliv_ex],
        ),
    }
    sub_ids = resolve_subscription_ids()
    if sub_ids:
        payload["subscription_ids"] = sub_ids
    return payload


def resolve_template(template_key: str) -> dict[str, Any] | None:
    templates = load_offering_templates()
    key = (template_key or "").strip().lower()
    if not key:
        return None
    return templates.get(key)


def build_offering_payload(
    template: dict[str, Any],
    *,
    price_override: float | None = None,
) -> dict[str, Any]:
    name = str(template.get("name") or "").strip()
    if not name:
        raise ValueError("template sans name")
    price = price_override if price_override is not None else float(template.get("price_usd") or 0)
    if price <= 0:
        raise ValueError("prix invalide")
    req_desc = str(template.get("requirements_description") or f"Inputs required for {name}.")
    deliv_desc = str(template.get("deliverable_description") or f"Deliverable returned for {name}.")
    req_ex, deliv_ex = template_example_objects(template)
    payload = {
        "name": name,
        "description": str(template.get("description") or "").strip(),
        "price_value": price,
        "sla_minutes": int(template.get("sla_minutes") or 5),
        "requirements": enrich_json_schema(
            template.get("requirements"),
            title=f"{name} — requirements",
            description=req_desc,
            examples=[req_ex],
        ),
        "deliverable": enrich_json_schema(
            template.get("deliverable"),
            title=f"{name} — deliverable",
            description=deliv_desc,
            examples=[deliv_ex],
        ),
    }
    sub_ids = resolve_subscription_ids()
    if sub_ids:
        payload["subscription_ids"] = sub_ids
    return payload


def _offering_exists(name: str, existing: list[dict]) -> dict | None:
    target = name.strip().lower()
    for row in existing:
        row_name = str(row.get("name") or "").strip().lower()
        if row_name == target:
            return row
    return None


async def format_templates_help(lang: str) -> tuple[str, dict]:
    templates = load_offering_templates()
    if lang == "fr":
        lines = ["Templates ACP (workflows marketplace) :", ""]
        for key, tpl in templates.items():
            price = tpl.get("price_usd", "?")
            sla = tpl.get("sla_minutes", "?")
            lines.append(f"• {key} — {tpl.get('name')} — {price} USDC — SLA {sla}m")
        lines.extend(
            [
                "",
                "Créer : « créer offre acp template analyse_lite_x1 »",
                "        « créer workflow acp veille_zhc_x1 prix 2.99 »",
            ]
        )
        lines.extend(["", get_acp_strict_rules("fr")])
        return "\n".join(lines), {"acp": "templates", "count": len(templates)}
    lines = ["ACP offering templates:"]
    for key, tpl in templates.items():
        lines.append(f"- {key}: {tpl.get('name')} @ {tpl.get('price_usd')} USDC")
    lines.extend(["", get_acp_strict_rules("en")])
    return "\n".join(lines), {"acp": "templates", "count": len(templates)}


async def execute_offering_delete_all(message: str, lang: str) -> tuple[str, dict]:
    """Deletes all ACP offerings of the active agent."""
    lang_key = "fr" if lang == "fr" else "en"
    if not acp_cli.is_acp_available():
        return "ACP — acp-cli introuvable.", {"acp": "no_cli"}

    existing, err_list = acp_cli.list_offerings()
    if err_list:
        return f"Liste offerings : {err_list[:200]}", {"acp": "offering_delete_all_list_error"}

    rows = existing or []
    if not rows:
        msg = "Aucune offre ACP active." if lang_key == "fr" else "No ACP offerings."
        return msg, {"acp": "offering_delete_all_empty", "deleted": []}

    lines = ["Suppression offres ACP :", ""]
    deleted: list[str] = []
    errors: list[str] = []
    for row in rows:
        name = str(row.get("name") or "?")
        oid = str(row.get("id") or "")
        if not oid:
            errors.append(f"• {name} — pas d'ID")
            continue
        ok, detail = acp_cli.delete_offering(oid)
        if ok:
            deleted.append(name)
            lines.append(f"• {name} — supprimé")
        else:
            errors.append(f"• {name} — {str(detail)[:80]}")

    if errors:
        lines.extend(["", "Erreurs :", *errors])

    if lang_key == "fr":
        lines.append("")
        lines.append(f"Total : {len(deleted)} supprimé(s) sur {len(rows)}.")
    else:
        lines.append(f"Deleted {len(deleted)}/{len(rows)}.")
    return "\n".join(lines), {
        "acp": "offering_delete_all",
        "deleted": deleted,
        "errors": errors,
    }


async def execute_offering_delete(message: str, lang: str) -> tuple[str, dict]:
    """Deletes an ACP offering by name — real acp-cli call."""
    lang_key = "fr" if lang == "fr" else "en"
    if not acp_cli.is_acp_available():
        return "ACP — acp-cli introuvable.", {"acp": "no_cli"}

    if wants_acp_offering_delete_all(message):
        return await execute_offering_delete_all(message, lang)

    name = parse_delete_workflow_name(message)
    if not name:
        if lang_key == "fr":
            return (
                "Précise le workflow à supprimer.\n"
                "Ex. : supprime le workflow test_1",
                {"acp": "offering_delete_parse_failed"},
            )
        return "Specify workflow name to delete.", {"acp": "offering_delete_parse_failed"}

    existing, err_list = acp_cli.list_offerings()
    if err_list:
        return f"Liste offerings : {err_list[:200]}", {"acp": "offering_delete_list_error"}

    hit = _offering_exists(name, existing or [])
    if not hit:
        if lang_key == "fr":
            return (
                f"Workflow « {name} » introuvable sur ACP.",
                {"acp": "offering_delete_not_found", "name": name},
            )
        return f"Offering « {name} » not found.", {"acp": "offering_delete_not_found", "name": name}

    offering_id = str(hit.get("id") or "")
    if not offering_id:
        return "Offre sans ID — suppression impossible.", {"acp": "offering_delete_no_id", "name": name}

    ok, detail = acp_cli.delete_offering(offering_id)
    if not ok:
        return (detail or "échec")[:300], {
            "acp": "offering_delete_error",
            "name": name,
            "offering_id": offering_id,
        }

    if lang_key == "fr":
        body = f"C'est fait — workflow {name} supprimé sur ACP (ID {offering_id})."
    else:
        body = f"Workflow {name} deleted on ACP (ID {offering_id})."
    return body, {
        "acp": "offering_delete",
        "name": name,
        "offering_id": offering_id,
        "detail": detail,
    }


async def execute_adhoc_workflow_create(message: str, lang: str) -> tuple[str, dict]:
    """Creates an ACP workflow from natural language — short reply."""
    lang_key = "fr" if lang == "fr" else "en"
    if not acp_cli.is_acp_available():
        return "ACP — acp-cli introuvable.", {"acp": "no_cli"}

    spec = parse_adhoc_workflow(message)
    if not spec:
        if lang_key == "fr":
            return (
                "Précise : nom (appeler test_1), prix (25$ et 99 centimes), service.\n"
                "Ex. : crée un workflow appeler audit_q1 à 9.99$ sur acp qui propose une analyse quantitative",
                {"acp": "adhoc_parse_failed"},
            )
        return "Specify workflow name, price, and service.", {"acp": "adhoc_parse_failed"}

    payload = build_adhoc_payload(spec)
    kind = str(payload.pop("service_kind", "generic"))
    existing, err_list = acp_cli.list_offerings()
    if err_list:
        return f"Erreur listings : {err_list[:120]}", {"acp": "adhoc_list_error"}

    hit = _offering_exists(payload["name"], existing or [])
    if hit:
        oid = str(hit.get("id") or "")
        if not oid:
            return "Offre existante sans ID.", {"acp": "adhoc_error"}
        row, err = acp_cli.update_offering(
            oid,
            description=payload["description"],
            price_value=payload["price_value"],
            sla_minutes=payload["sla_minutes"],
            requirements=payload["requirements"],
            deliverable=payload["deliverable"],
            **_subscription_update_kw(payload),
        )
        action = "mis à jour" if lang_key == "fr" else "updated"
    else:
        row, err = acp_cli.create_offering(**payload)
        action = "créé" if lang_key == "fr" else "created"

    if err or not row:
        return (err or "échec")[:200], {"acp": "adhoc_error"}

    from aria_core.skills.acp_product_launch_skill import _promote_product

    sample_req, sample_deliv = premium_examples(kind, payload["name"])
    promo = await _promote_product(
        name=payload["name"],
        description=payload["description"],
        price_usd=float(payload["price_value"]),
        sla_minutes=int(payload["sla_minutes"]),
        lang=lang_key,
        do_social=True,
    )

    off_id = row.get("id") or "?"
    price = row.get("priceValue") or payload["price_value"]
    if lang_key == "fr":
        lines = [
            f"C'est fait — workflow {payload['name']} {action} sur ACP (qualité premium).",
            f"{price} USDC · SLA {payload['sla_minutes']}m · ID {off_id}",
            "",
            "Exemples demande/livrable : injectés dans schémas API (requirements.examples / deliverable.examples).",
            f"• Demande : {sample_req[:120]}{'…' if len(sample_req) > 120 else ''}",
            f"• Livrable : {sample_deliv[:120]}{'…' if len(sample_deliv) > 120 else ''}",
            "",
            "Promo revenus :",
            f"• X : {'publié' if promo.get('x_posted') else 'brouillon — valider ou configurer X'}",
        ]
        if promo.get("tweet_text"):
            lines.append(f"  « {promo['tweet_text']} »")
        lines.append(
            f"• Telegram opérateur : {'notifié' if promo.get('telegram_notified') else 'non'}"
        )
        body = "\n".join(lines)
    else:
        body = (
            f"Workflow {payload['name']} {action} — premium · {price} USDC (ID {off_id})\n"
            f"X: {'posted' if promo.get('x_posted') else 'draft'}"
        )
    return body, {
        "acp": "adhoc_create",
        "offering_id": off_id,
        "name": payload["name"],
        "service_kind": kind,
        "sample_request": sample_req,
        "sample_deliverable": sample_deliv,
        **promo,
    }


async def execute_offering_create(message: str, lang: str) -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    if not acp_cli.is_acp_available():
        msg = (
            "ACP — acp-cli introuvable (npm i -g @virtuals-protocol/acp-cli)."
            if lang_key == "fr"
            else "ACP — acp-cli not found."
        )
        return msg, {"acp": "no_cli"}

    template_key = _parse_template_key(message)
    if not template_key:
        templates = load_offering_templates()
        keys = ", ".join(sorted(templates.keys())[:6])
        if lang_key == "fr":
            return (
                "Précise le template workflow.\n"
                f"Disponibles : {keys}\n"
                "Exemple : « créer offre acp template analyse_lite_x1 »",
                {"acp": "offering_create_missing_template"},
            )
        return (
            f"Specify template key. Available: {keys}",
            {"acp": "offering_create_missing_template"},
        )

    template = resolve_template(template_key)
    if not template:
        if lang_key == "fr":
            return (
                f"Template inconnu : {template_key}. "
                "Demande « templates offres acp » pour la liste.",
                {"acp": "offering_create_unknown_template"},
            )
        return f"Unknown template: {template_key}", {"acp": "offering_create_unknown_template"}

    try:
        payload = build_offering_payload(template, price_override=_parse_price_override(message))
    except ValueError as exc:
        return str(exc), {"acp": "offering_create_invalid_template"}

    existing, err_list = acp_cli.list_offerings()
    if err_list:
        return f"Liste offerings : {err_list[:200]}", {"acp": "offering_create_list_error"}

    hit = _offering_exists(payload["name"], existing or [])
    if hit:
        offering_id = str(hit.get("id") or "")
        if not offering_id:
            if lang_key == "fr":
                return (
                    f"Offre « {payload['name']} » déjà active (id manquant pour update).",
                    {"acp": "offering_exists"},
                )
            return f"Offering {payload['name']} already exists.", {"acp": "offering_exists"}
        row, err = acp_cli.update_offering(
            offering_id,
            description=payload["description"],
            price_value=payload["price_value"],
            sla_minutes=payload["sla_minutes"],
            requirements=payload["requirements"],
            deliverable=payload["deliverable"],
            **_subscription_update_kw(payload),
        )
        action = "update"
    else:
        row, err = acp_cli.create_offering(**payload)
        action = "create"

    if err or not row:
        return (err or "échec offering")[:400], {"acp": "offering_create_error", "action": action}

    off_id = row.get("id") or "?"
    off_name = row.get("name") or payload["name"]
    price = row.get("priceValue") or payload["price_value"]
    if lang_key == "fr":
        body = (
            f"Workflow ACP {action} — {off_name}\n"
            f"ID : {off_id}\n"
            f"Prix : {price} USDC · SLA {payload['sla_minutes']}m"
        )
    else:
        body = f"ACP offering {action} — {off_name} ({off_id}) @ {price} USDC"
    return body, {
        "acp": "offering_create",
        "action": action,
        "offering_id": off_id,
        "template": template_key,
    }