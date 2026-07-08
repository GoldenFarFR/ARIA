"""Simulation de bout en bout du cycle ARIA — A à Z, sur un vrai token.

But : PROUVER que toute la chaîne tourne, d'un coup, sur un token réel. Chaque étape
est imprimée pour être suivie à l'œil :

  1. SCAN on-chain complet (sécurité, autorité mint, launchpad, dev-wallet, liquidité, TA)
  2. FILTRE de sécurité (verdict binaire + raisons)
  3. ANALYSE VC (thèse, entrée/cible/invalidation) — LLM si dispo, sinon déterministe
  4. CARNET : entrée de journal + SCREENSHOT (chandeliers réels + simulation forward)
  5. SUIVI : un checkpoint de thèse (prix + activité projet)
  6. EXPORT : le carnet en .txt lisible

Usage (sur le VPS, réseau + LLM dispo) :
    docker exec aria-api python -m aria_core.simulate_lifecycle 0xCONTRAT
    docker exec aria-api python -m aria_core.simulate_lifecycle           # découvre un top pool

Aucune action financière : lecture + écriture dans le carnet local uniquement.
"""
from __future__ import annotations

import asyncio
import sys


def _line(txt: str = "") -> None:
    print(txt, flush=True)


def _configure_host() -> bool:
    """Configure la librairie comme le serveur au démarrage (spark/LLM/clés) → parité prod.

    Lancé en ligne de commande (`docker exec ... python -m ...`), ce process est SÉPARÉ du
    serveur : sans cet appel, la librairie n'est pas configurée et l'analyse VC retombe sur
    le déterministe (LLM off). On réutilise LE MÊME configurateur que l'hôte pour éviter
    toute dérive. Best-effort : hors conteneur (tests, local), l'import échoue proprement et
    on garde le repli déterministe. Aucun garde-fou touché.
    """
    try:
        from app.integrations.aria_host import register_aria_host_integrations
    except Exception:
        return False
    try:
        register_aria_host_integrations()
        return True
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, repli déterministe
        _line(f"    (config hote echouee: {exc}) — repli deterministe")
        return False


async def simulate(contract: str | None = None) -> dict:
    from aria_core.skills.acp_onchain_scan import scan_base_token
    from aria_core.skills.safety_screen import safety_screen

    _line("=" * 64)
    _line("SIMULATION CYCLE ARIA — A a Z")
    _line("=" * 64)

    # Config hôte (spark/LLM/clés) comme au démarrage du serveur → analyse identique à la prod.
    llm_on = _configure_host()
    _line(f"\n[*] Config hote : {'LLM actif (parite prod)' if llm_on else 'mode deterministe (LLM off)'}")

    # 0. Découverte si aucun contrat fourni.
    if not contract:
        _line("\n[0] Decouverte d'un token (top pools liquides)...")
        from aria_core.base_crawler import discover_top_pools

        toks = await discover_top_pools(limit=5, min_liquidity_usd=30_000)
        if not toks:
            _line("    aucun token decouvert (reseau ?). Fournis un contrat en argument.")
            return {"ok": False, "reason": "no_token"}
        contract = toks[0]
        _line(f"    -> {contract}")

    # 1. Scan complet.
    _line(f"\n[1] SCAN on-chain complet de {contract}")
    ctx = await scan_base_token(
        contract, include_smart_money=True, include_fundamentals=True,
        include_ta=True, include_dev_behavior=True, include_honeypot=True,
    )
    _line(f"    score={ctx.security_score} verdict={ctx.lite_verdict} verifie={ctx.contract_verified}")
    _line(f"    mint={ctx.has_mint} autorite={ctx.mint_authority} launchpad={ctx.launchpad}")
    _line(f"    liquidite/mcap={ctx.liq_mcap_ratio} dev={ctx.dev_signal}")
    if ctx.best_pair:
        _line(f"    paire={ctx.best_pair.base_symbol} liq=${ctx.best_pair.liquidity_usd:,.0f}")
    for pt in (ctx.dev_points or [])[:4]:
        _line(f"      dev: {pt}")

    # 2. Filtre de securite.
    _line("\n[2] FILTRE de securite")
    screen = safety_screen(ctx)
    _line(f"    passe={screen.passed} (hard_fail={screen.hard_fail})")
    for r in screen.reasons[:6]:
        _line(f"      - {r}")

    # 3. Analyse VC (LLM si dispo).
    _line("\n[3] ANALYSE VC")
    result = None
    try:
        from aria_core.skills.vc_analysis import analyze_vc_with_context

        result, ctx = await analyze_vc_with_context(contract)
        _line(f"    reco={result.recommandation} potentiel={result.potentiel} risque={result.risque}")
        _line(f"    these: {(result.these or '')[:180]}")
        _line(f"    entree={result.entree} cible={result.cible} invalidation={result.invalidation}")
    except Exception as exc:  # noqa: BLE001
        _line(f"    (analyse LLM indisponible ici: {exc}) — on continue avec les faits du scan")

    # 4. Carnet : entree + screenshot.
    _line("\n[4] CARNET : entree de journal + SCREENSHOT")
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
        _line(f"    screenshot -> {chart_ref or '(rendu indisponible)'}")
    else:
        _line("    (pas de bougies OHLCV -> pas de screenshot ce tour)")

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
    _line(f"    entree de carnet #{jid} enregistree")

    # 5. Suivi : un checkpoint de these.
    _line("\n[5] SUIVI : checkpoint de these")
    price_now = ctx.best_pair.price_usd if ctx.best_pair else None
    pct = None
    if price_now and entry_price:
        pct = round(100.0 * (price_now - entry_price) / entry_price, 1)
    activity = tj.assess_project_activity()  # sans capteurs live ici -> unknown
    verdict, note = tj.judge_thesis(
        price_vs_entry_pct=pct, invalidation_hit=False, activity=activity
    )
    await tj.record_checkpoint(
        contract, price=price_now, price_vs_entry_pct=pct,
        activity_status=activity.status, verdict=verdict, note=note,
    )
    _line(f"    checkpoint: prix={price_now} ({pct}%) activite={activity.status} -> {verdict}")

    # 6. Export .txt.
    _line("\n[6] CARNET (.txt)")
    _line("-" * 64)
    _line(await tj.export_txt(limit=5))
    _line("=" * 64)
    _line("SIMULATION TERMINEE — chaine complete OK")
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
