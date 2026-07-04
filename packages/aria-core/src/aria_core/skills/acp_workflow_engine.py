"""ACP workflow engine — build deliverables per offering with on-chain data."""
from __future__ import annotations

import re
from typing import Any

from aria_core.skills.acp_deliverable_quality import resolve_workflow_key
from aria_core.skills.acp_onchain_scan import TokenScanContext, scan_base_token

_ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")


def _extract_requirements(history: dict) -> dict[str, Any]:
    for container in (history, history.get("job") or {}, history.get("requirements") or {}):
        if not isinstance(container, dict):
            continue
        req = container.get("requirements")
        if isinstance(req, dict) and req:
            return req
        for key in ("contractAddress", "contract_address", "brief", "symbols"):
            if container.get(key) is not None:
                return {k: v for k, v in container.items() if k in (
                    "contractAddress", "contract_address", "brief", "symbols", "ca"
                )}
    return {}


def _contract_from_requirements(req: dict[str, Any]) -> str:
    for key in ("contractAddress", "contract_address", "ca"):
        val = req.get(key)
        if isinstance(val, str) and _ADDR_RE.match(val.strip()):
            return val.strip()
    brief = str(req.get("brief") or "")
    m = _ADDR_RE.search(brief)
    return m.group(0) if m else ""


def _full_verdict_from_lite(lite: str, score: int) -> str:
    if lite == "DANGER" or score < 25:
        return "AVOID"
    if lite == "SAFE" and score >= 70:
        return "SAFE"
    return "SPECULATIVE"


def _format_full_audit_report(ctx: TokenScanContext) -> str:
    pair = ctx.best_pair
    lines = [
        f"# ARIA Full Audit — Base contract",
        "",
        "## Executive summary",
        f"Contract `{ctx.contract or 'N/A'}` — heuristic scan via DexScreener (Base).",
        f"Security score: **{ctx.security_score}/100** · Lite verdict: **{ctx.lite_verdict}**.",
        "",
        "## Liquidity",
    ]
    if pair:
        lines.extend([
            f"- Best pair: {pair.base_symbol}/{pair.quote_symbol} ({pair.dex_id})",
            f"- Liquidity USD: ${pair.liquidity_usd:,.2f}",
            f"- Pair address: `{pair.pair_address}`",
        ])
    else:
        lines.append("- No DexScreener pair found — liquidity unverified.")

    lines.extend(["", "## Volume"])
    if pair:
        lines.extend([
            f"- Volume 24h: ${pair.volume_24h_usd:,.2f}",
            f"- Txns 24h: {pair.buys_24h} buys / {pair.sells_24h} sells",
            f"- Price change 24h: {pair.price_change_24h:+.2f}%",
        ])
    else:
        lines.append("- Volume data unavailable.")

    lines.extend(["", "## Risk flags"])
    if ctx.risk_flags:
        for flag in ctx.risk_flags:
            lines.append(f"- {flag}")
    else:
        lines.append("- No major flags from heuristic scan.")

    lines.extend([
        "",
        "## Recommendation",
        "Confirm ownership, honeypot checks, and holder distribution on Basescan before sizing.",
        "Use DexScreener embed for live chart — ARIA signals are heuristics, not execution advice.",
        "",
        "## Disclaimer",
        "This report is a heuristic research deliverable — not financial advice.",
        f"Data source: {ctx.data_source} · pairs found: {ctx.pairs_found}.",
    ])
    return "\n".join(lines)


def build_lite_deliverable(ctx: TokenScanContext) -> dict[str, Any]:
    alerts = "; ".join(ctx.risk_flags) if ctx.risk_flags else (
        "Scan heuristique ARIA — confirmer liquidité et ownership sur Basescan."
    )
    if ctx.best_pair:
        pair = ctx.best_pair
        alerts += (
            f" Meilleure liquidité: ${pair.liquidity_usd:,.0f} "
            f"({pair.base_symbol}/{pair.quote_symbol} sur {pair.dex_id})."
        )
    if len(alerts) < 40:
        alerts += " Confirm on Basescan — heuristic only, not financial advice."
    return {
        "liteVerdict": ctx.lite_verdict,
        "riskAlerts": alerts[:500],
    }


def build_full_deliverable(ctx: TokenScanContext) -> dict[str, Any]:
    verdict = _full_verdict_from_lite(ctx.lite_verdict, ctx.security_score)
    return {
        "verdict": verdict,
        "securityScore": str(ctx.security_score),
        "auditReport": _format_full_audit_report(ctx),
    }


def build_veille_deliverable(req: dict[str, Any], ctx: TokenScanContext | None = None) -> dict[str, Any]:
    brief = str(req.get("brief") or "ZHC ecosystem watch").strip()
    symbols = str(req.get("symbols") or "").strip()
    text = f"{brief} {symbols}".lower()

    signal = "WATCH"
    if any(w in text for w in ("urgent", "alert", "rug", "hack", "exploit", "drain")):
        signal = "ALERT"
    elif any(w in text for w in ("clear", "stable", "ok", "resolved", "all clear")):
        signal = "CLEAR"

    summary_parts = [
        f"ZHC/Vanguard watch — scope: {brief[:200]}.",
    ]
    if symbols:
        summary_parts.append(f"Symbols/theme: {symbols[:120]}.")
    if ctx and ctx.best_pair:
        pair = ctx.best_pair
        summary_parts.append(
            f"On-chain context ({ctx.contract[:10]}…): "
            f"liq ${pair.liquidity_usd:,.0f}, vol24h ${pair.volume_24h_usd:,.0f}, "
            f"verdict {ctx.lite_verdict}."
        )
    elif ctx and ctx.risk_flags:
        summary_parts.append(ctx.risk_flags[0])

    action = (
        "Revisit watchlist sizing; confirm on-chain and macro before allocation. "
        "Not financial advice — operator due diligence required."
    )
    if signal == "ALERT":
        action = (
            "Elevated risk language in brief — pause new size, verify sources, "
            "document in truth-ledger before any action."
        )
    elif signal == "CLEAR":
        action = "No immediate action — maintain watch, log if thesis changes."

    return {
        "signal": signal,
        "summary": " ".join(summary_parts)[:600],
        "actionNote": action[:400],
    }


async def build_deliverable_for_job(
    offering_name: str,
    history: dict,
) -> tuple[dict[str, Any] | None, str, TokenScanContext | None]:
    """Returns (deliverable, workflow_key, scan_context)."""
    workflow = resolve_workflow_key(offering_name)
    req = _extract_requirements(history)
    contract = _contract_from_requirements(req)

    ctx: TokenScanContext | None = None
    if contract or workflow in ("analyse_lite_x1", "analyse_full_x1"):
        ctx = await scan_base_token(contract)

    if workflow == "analyse_full_x1":
        return build_full_deliverable(ctx or await scan_base_token(contract)), workflow, ctx
    if workflow == "veille_zhc_x1":
        if contract and not ctx:
            ctx = await scan_base_token(contract)
        return build_veille_deliverable(req, ctx), workflow, ctx
    return build_lite_deliverable(ctx or await scan_base_token(contract)), workflow, ctx