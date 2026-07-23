"""End-to-end simulation of the ARIA cycle — A to Z, on a real token.

Goal: PROVE that the entire chain runs, in one go, on a real token. Each step
is printed to be followed by eye:

  1. Full on-chain SCAN (security, mint authority, launchpad, dev-wallet, liquidity, TA)
  2. Safety FILTER (binary verdict + reasons)
  3. VC ANALYSIS (thesis, entry/target/invalidation) — LLM if available, otherwise deterministic
  4. JOURNAL: log entry + SCREENSHOT (real candles + forward simulation)
  5. TRACKING: a thesis checkpoint (price + project activity)
  6. EXPORT: the journal as readable .txt

Usage (on the VPS, network + LLM available):
    docker exec aria-api python -m aria_core.simulate_lifecycle 0xCONTRACT
    docker exec aria-api python -m aria_core.simulate_lifecycle           # discovers a top pool

No financial action: read + write to the local journal only.
"""
from __future__ import annotations

import asyncio
import sys


def _line(txt: str = "") -> None:
    print(txt, flush=True)


def _configure_host() -> bool:
    """Configures the library like the server at startup (spark/LLM/keys) -> prod parity.

    Launched from the command line (`docker exec ... python -m ...`), this process is SEPARATE
    from the server: without this call, the library isn't configured and VC analysis falls
    back to deterministic mode (LLM off). Reuses the SAME configurator as the host to avoid
    any drift. Best-effort: outside the container (tests, local), the import fails cleanly and
    the deterministic fallback is kept. No guardrail touched.
    """
    try:
        from app.integrations.aria_host import register_aria_host_integrations
    except Exception:
        return False
    try:
        register_aria_host_integrations()
        return True
    except Exception as exc:  # noqa: BLE001 -- never blocking, deterministic fallback
        _line(f"    (host config failed: {exc}) -- deterministic fallback")
        return False


async def simulate(contract: str | None = None) -> dict:
    from aria_core.skills.acp_onchain_scan import scan_base_token
    from aria_core.skills.safety_screen import safety_screen

    _line("=" * 64)
    _line("ARIA CYCLE SIMULATION — A to Z")
    _line("=" * 64)

    # Host config (spark/LLM/keys) like at server startup -> identical analysis to prod.
    llm_on = _configure_host()
    _line(f"\n[*] Host config: {'LLM active (prod parity)' if llm_on else 'deterministic mode (LLM off)'}")

    # 0. Discovery if no contract supplied.
    if not contract:
        _line("\n[0] Discovering a token (top liquid pools)...")
        from aria_core.base_crawler import discover_top_pools

        toks = await discover_top_pools(limit=5, min_liquidity_usd=30_000)
        if not toks:
            _line("    no token discovered (network?). Supply a contract as an argument.")
            return {"ok": False, "reason": "no_token"}
        contract = toks[0]
        _line(f"    -> {contract}")

    # 1. Full scan.
    _line(f"\n[1] Full on-chain SCAN of {contract}")
    ctx = await scan_base_token(
        contract, include_smart_money=True, include_fundamentals=True,
        include_ta=True, include_dev_behavior=True, include_honeypot=True,
    )
    _line(f"    score={ctx.security_score} verdict={ctx.lite_verdict} verified={ctx.contract_verified}")
    _line(f"    mint={ctx.has_mint} authority={ctx.mint_authority} launchpad={ctx.launchpad}")
    _line(f"    liquidity/mcap={ctx.liq_mcap_ratio} dev={ctx.dev_signal}")
    if ctx.best_pair:
        _line(f"    pair={ctx.best_pair.base_symbol} liq=${ctx.best_pair.liquidity_usd:,.0f}")
    for pt in (ctx.dev_points or [])[:4]:
        _line(f"      dev: {pt}")

    # 2. Safety filter.
    _line("\n[2] Safety FILTER")
    screen = safety_screen(ctx)
    _line(f"    passed={screen.passed} (hard_fail={screen.hard_fail})")
    for r in screen.reasons[:6]:
        _line(f"      - {r}")

    # 3. VC analysis (LLM if available).
    _line("\n[3] VC ANALYSIS")
    result = None
    try:
        from aria_core.skills.vc_analysis import analyze_vc_with_context

        result, ctx = await analyze_vc_with_context(contract)
        _line(f"    reco={result.recommandation} potential={result.potentiel} risk={result.risque}")
        _line(f"    thesis: {(result.these or '')[:180]}")
        _line(f"    entry={result.entree} target={result.cible} invalidation={result.invalidation}")
    except Exception as exc:  # noqa: BLE001
        _line(f"    (LLM analysis unavailable here: {exc}) -- continuing with scan facts")

    # 4. Journal: entry + screenshot.
    _line("\n[4] JOURNAL: log entry + SCREENSHOT")
    from aria_core import thesis_journal as tj

    entry_price = _num(getattr(result, "entree", None)) or (
        ctx.ta_entry.entree if ctx.ta_entry else (ctx.best_pair.price_usd if ctx.best_pair else None)
    )
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    chart_ref = ""
    if ctx.ta_candles:
        chart_ref = tj.save_entry_screenshot(
            contract, ctx.ta_candles, entry=entry_price, invalidation=inval, target=target,
        )
        _line(f"    screenshot -> {chart_ref or '(rendering unavailable)'}")
    else:
        _line("    (no OHLCV candles -> no screenshot this round)")

    facts = [f for f in (ctx.risk_flags or [])[:8]]
    jid = await tj.record_entry(tj.JournalEntry(
        contract=contract,
        symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
        decision=(getattr(result, "recommandation", None) or ("PASS" if screen.passed else "AVOID")),
        thesis=(getattr(result, "these", "") or ""),
        reasoning="; ".join(screen.reasons[:4]),
        facts=facts,
        entry_price=entry_price, target_price=target, invalidation_price=inval,
        chart_ref=chart_ref,
    ))
    _line(f"    journal entry #{jid} recorded")

    # 5. Tracking: a thesis checkpoint.
    _line("\n[5] TRACKING: thesis checkpoint")
    price_now = ctx.best_pair.price_usd if ctx.best_pair else None
    pct = None
    if price_now and entry_price:
        pct = round(100.0 * (price_now - entry_price) / entry_price, 1)
    activity = tj.assess_project_activity()  # no live sensors here -> unknown
    verdict, note = tj.judge_thesis(
        price_vs_entry_pct=pct, invalidation_hit=False, activity=activity
    )
    await tj.record_checkpoint(
        contract, price=price_now, price_vs_entry_pct=pct,
        activity_status=activity.status, verdict=verdict, note=note,
    )
    _line(f"    checkpoint: price={price_now} ({pct}%) activity={activity.status} -> {verdict}")

    # 6. Export .txt.
    _line("\n[6] JOURNAL (.txt)")
    _line("-" * 64)
    _line(await tj.export_txt(limit=5))
    _line("=" * 64)
    _line("SIMULATION COMPLETE — full chain OK")
    return {"ok": True, "contract": contract, "journal_id": jid, "screenshot": chart_ref}


def _num(v) -> float | None:
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


def main() -> None:
    contract = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(simulate(contract))


if __name__ == "__main__":
    main()
