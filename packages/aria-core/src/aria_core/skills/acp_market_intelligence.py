"""ACP marketplace intelligence — offre/demande, gaps, suggestions workflows."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.paths import memory_dir
from aria_core.skills.acp_cli import browse_agents, is_acp_available, list_offerings
from aria_core.skills.acp_offering_skill import load_offering_templates

_SCAN_CACHE = memory_dir() / "acp_market_scan.json"

_MARKET_RE = re.compile(
    r"(?i)(?:"
    r"étud(?:e|ier).*(?:offre|demande|marketplace)|"
    r"analys(?:e|er).*(?:agents?|marketplace|acp)|"
    r"offre\s+et\s+la\s+demande|supply.*demand|"
    r"gap(?:s)?\s+(?:marketplace|acp|offre)|"
    r"quelle?\s+offre\s+créer|workflow.*créer|"
    r"intelligence\s+(?:marché|marche|market)|"
    r"scan\s+(?:marché|marche|marketplace)\s+acp"
    r")",
)

_SCAN_QUERIES: tuple[str, ...] = (
    "token audit",
    "security scan",
    "research",
    "watch digest",
    "trading",
    "social analysis",
    "defi",
    "agent automation",
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "audit_security": ("audit", "security", "scan", "honeypot", "rug", "contract"),
    "research_intel": ("research", "analysis", "report", "intel", "due diligence"),
    "watch_digest": ("watch", "digest", "veille", "monitor", "alert", "news"),
    "trading_exec": ("trade", "swap", "buy", "sell", "execution", "snipe"),
    "social_narrative": ("social", "twitter", "x ", "sentiment", "narrative"),
    "dev_build": ("code", "build", "deploy", "github", "app", "website"),
    "automation": ("autom", "workflow", "agent", "bot", "schedule"),
}

_GAP_SUGGESTIONS: dict[str, dict[str, str]] = {
    "audit_security": {
        "template": "analyse_full_x1",
        "action_fr": "Renforcer analyse_full_x1 (4,99 $) + promo X « pre-entry audit ».",
        "action_en": "Promote analyse_full_x1 ($4.99) with pre-entry audit positioning.",
    },
    "watch_digest": {
        "template": "veille_zhc_x1",
        "action_fr": "Pousser veille_zhc_x1 (2,49 $) — niche ZHC/agents autonomes.",
        "action_en": "Push veille_zhc_x1 ($2.49) — ZHC autonomous-agent niche.",
    },
    "research_intel": {
        "template": "analyse_lite_x1",
        "action_fr": "Bundle lite→full : entrée 1,99 $ → upsell audit 4,99 $.",
        "action_en": "Bundle lite→full upsell ($1.99 → $4.99).",
    },
    "social_narrative": {
        "template": "veille_zhc_x1",
        "action_fr": "Créer social_crosscheck_x1 — croiser on-chain + X.",
        "action_en": "Create social_crosscheck_x1 — on-chain + X cross-check.",
    },
    "trading_exec": {
        "template": "custom",
        "action_fr": "Éviter trade direct — proposer sizing_brief_x1 post-audit.",
        "action_en": "Avoid direct trades — offer sizing_brief_x1 post-audit.",
    },
    "dev_build": {
        "template": "custom",
        "action_fr": "Offre landing_audit_x1 — review conversion page Vanguard.",
        "action_en": "Offer landing_audit_x1 — Vanguard conversion review.",
    },
    "automation": {
        "template": "custom",
        "action_fr": "Offre acp_setup_x1 — aider un agent à publier sa 1ère offre.",
        "action_en": "Offer acp_setup_x1 — help agents publish first offering.",
    },
}


def wants_acp_market_research(message: str) -> bool:
    return bool(_MARKET_RE.search((message or "").strip()))


def _load_cache() -> dict[str, Any]:
    if not _SCAN_CACHE.is_file():
        return {}
    try:
        return json.loads(_SCAN_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(doc: dict[str, Any]) -> None:
    _SCAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _SCAN_CACHE.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def _agent_metrics(agent: dict) -> dict[str, Any]:
    return {
        "id": agent.get("id") or agent.get("agentId") or "?",
        "name": agent.get("name") or agent.get("agentName") or "?",
        "jobs": int(agent.get("successfulJobCount") or agent.get("jobCount") or 0),
        "buyers": int(agent.get("uniqueBuyerCount") or agent.get("buyerCount") or 0),
        "offerings": [
            {
                "name": o.get("name"),
                "price": o.get("priceValue") or o.get("price"),
                "description": (o.get("description") or "")[:120],
            }
            for o in (agent.get("offerings") or [])
            if isinstance(o, dict)
        ],
    }


def _categorize_text(text: str) -> list[str]:
    lower = (text or "").lower()
    hits = [cat for cat, keys in _CATEGORY_KEYWORDS.items() if any(k in lower for k in keys)]
    return hits or ["other"]


def _aggregate_market(agents: list[dict]) -> dict[str, Any]:
    by_category: Counter[str] = Counter()
    top_agents: list[dict] = []
    offering_prices: dict[str, list[float]] = defaultdict(list)

    for raw in agents:
        row = _agent_metrics(raw)
        top_agents.append(row)
        blob = f"{row['name']} " + " ".join(
            f"{o.get('name','')} {o.get('description','')}" for o in row["offerings"]
        )
        for cat in _categorize_text(blob):
            by_category[cat] += max(1, row["jobs"])
        for off in row["offerings"]:
            try:
                price = float(off.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0:
                cat = _categorize_text(f"{off.get('name','')} {off.get('description','')}")[0]
                offering_prices[cat].append(price)

    top_agents.sort(key=lambda r: (r["jobs"], r["buyers"]), reverse=True)
    return {
        "agent_count": len(agents),
        "categories": dict(by_category.most_common()),
        "top_agents": top_agents[:10],
        "median_prices": {
            cat: round(sorted(prices)[len(prices) // 2], 2)
            for cat, prices in offering_prices.items()
            if prices
        },
    }


def _our_offering_names() -> set[str]:
    ours, _ = list_offerings()
    names = {str(o.get("name", "")).lower() for o in ours or [] if o.get("name")}
    if names:
        return names
    templates = load_offering_templates()
    return {str(t.get("name", "")).lower() for t in templates.values() if t.get("name")}


def _gap_analysis(market: dict[str, Any], lang: str) -> list[str]:
    ours = _our_offering_names()
    cats = market.get("categories") or {}
    lines: list[str] = []
    ranked = sorted(cats.items(), key=lambda kv: kv[1], reverse=True)

    header = "Signaux demande (agrégat browse) :" if lang == "fr" else "Demand signals (browse aggregate):"
    lines.append(header)

    for cat, weight in ranked[:6]:
        if cat == "other":
            continue
        sug = _GAP_SUGGESTIONS.get(cat, {})
        action = sug.get("action_fr" if lang == "fr" else "action_en", "")
        covered = sug.get("template") in ours if sug.get("template") not in (None, "custom") else False
        status = "couvert" if covered else "GAP"
        if lang != "fr":
            status = "covered" if covered else "GAP"
        lines.append(f"• [{status}] {cat.replace('_', ' ')} (score {weight}) — {action}")

    if not ranked:
        msg = (
            "• API browse indisponible — relancer « scan marché acp » plus tard."
            if lang == "fr"
            else "• Browse API unavailable — retry later."
        )
        for hint in _GAP_SUGGESTIONS.values():
            action = hint.get("action_fr" if lang == "fr" else "action_en", "")
            lines.append(f"• [heuristique] {action}")
        if len(lines) == 1:
            lines.append(msg)
    return lines


async def run_market_scan(*, use_cache_on_fail: bool = True) -> dict[str, Any]:
    if not is_acp_available():
        return {"ok": False, "error": "no_cli", "agents": []}

    seen: dict[str, dict] = {}
    errors: list[str] = []

    fail_streak = 0
    for query in _SCAN_QUERIES:
        rows, err = browse_agents(
            query, top_k=8, sort_by="successfulJobCount", mode="mixed", timeout=12
        )
        if err:
            errors.append(f"{query}: {err[:120]}")
            fail_streak += 1
            if fail_streak >= 3 and not seen:
                errors.append("browse: abort apres 3 echecs consecutifs (API Virtuals)")
                break
            continue
        fail_streak = 0
        for row in rows:
            aid = str(row.get("id") or row.get("agentId") or row.get("name") or "")
            if aid and aid not in seen:
                seen[aid] = row

    agents = list(seen.values())
    doc: dict[str, Any] = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "queries": list(_SCAN_QUERIES),
        "agent_count": len(agents),
        "errors": errors[:8],
        "market": _aggregate_market(agents) if agents else {},
    }

    if agents:
        _save_cache(doc)
        doc["ok"] = True
        doc["source"] = "live"
    elif use_cache_on_fail:
        cached = _load_cache()
        if cached.get("market"):
            doc["ok"] = True
            doc["source"] = "cache"
            doc["market"] = cached.get("market", {})
            doc["cached_at"] = cached.get("scanned_at")
        else:
            doc["ok"] = False
            doc["source"] = "none"
    else:
        doc["ok"] = False
        doc["source"] = "none"
    return doc


async def execute_acp_market_research(message: str, lang: str = "fr") -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    scan = await run_market_scan()
    market = scan.get("market") or {}
    ours = _our_offering_names()

    if lang_key == "fr":
        verdict = (
            "Verdict : aligner nos workflows sur la demande browse + combler les gaps moat ZHC."
            if scan.get("ok")
            else "Verdict : browse Virtuals en erreur — analyse heuristique + cache."
        )
    else:
        verdict = (
            "Verdict: align workflows to browse demand + fill ZHC moat gaps."
            if scan.get("ok")
            else "Verdict: Virtuals browse errors — heuristic + cache."
        )

    lines = [
        "═══ ACP MARKET INTELLIGENCE ═══",
        "",
        verdict,
        "",
        f"Source : {scan.get('source', '?')} — {scan.get('agent_count', 0)} agent(s)"
        if lang_key == "fr"
        else f"Source: {scan.get('source', '?')} — {scan.get('agent_count', 0)} agents",
        f"Nos offres : {', '.join(sorted(ours)) or '(aucune)'}"
        if lang_key == "fr"
        else f"Our offerings: {', '.join(sorted(ours)) or '(none)'}",
    ]

    if scan.get("errors"):
        lines.extend(["", "Erreurs API (extrait) :" if lang_key == "fr" else "", "API errors:"])
        for e in scan["errors"][:3]:
            lines.append(f"  - {e}")

    lines.append("")
    lines.extend(_gap_analysis(market, lang_key))

    top = market.get("top_agents") or []
    if top:
        lines.extend(["", "Top agents (jobs) :"])
        for ag in top[:5]:
            off_txt = ", ".join(f"{o.get('name')}@{o.get('price')}$" for o in ag.get("offerings", [])[:3])
            lines.append(
                f"  • {ag.get('name')} — {ag.get('jobs', 0)} jobs — {off_txt or '—'}"
            )

    lines.extend([
        "",
        "Actions : scan marché acp | créer offre acp template <nom> | traiter jobs acp",
    ])
    append_memory("acp_market", f"[scan] source={scan.get('source')} agents={scan.get('agent_count')}")
    return "\n".join(lines), {"acp": "market_intelligence", "scan": scan, "our_offerings": sorted(ours)}