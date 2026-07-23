"""Weekly (walk-forward) training loop — the core that accumulates proof.

- **Monday**: ``run_weekly_forecasts`` draws N tokens from the screened pool,
  analyzes them and records N timestamped predictions (with entry price +
  pool + 85/15 bucket) → falsifiable.
- **Due date**: ``resolve_due`` closes predictions that reach their horizon by
  comparing the entry price to the **real current OHLCV price** (spec ~7 days,
  VC ~30 days). Multi-horizon: a VC thesis isn't judged in one week.
- **Report**: ``weekly_report`` aggregates calibration + tracked wallet value + pool.

All external dependencies (drawing, analysis, OHLCV price, clock) are
**injectable** → testable offline. In prod, the defaults wire the real
pipeline. No financial action: this records and measures, it trades nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aria_core import screened_pool, vc_predictions

logger = logging.getLogger(__name__)

# Resolution horizons per bucket (days).
HORIZON_DAYS = {"vc": 30, "spec": 7}


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


async def run_weekly_forecasts(
    *, n: int = 20, drawer=None, analyzer=None
) -> list[int]:
    """Draws N tokens from the pool, analyzes them, records N dated predictions. Returns the ids.

    ``drawer()`` → list of tokens from the pool (default: screened pool lottery).
    ``analyzer(contract)`` → ``(VCResult, TokenScanContext)`` (default: real VC analysis).
    The entry price (spot at prediction time) and the pool are captured from
    the context → essential for later automatic resolution.
    """
    draw = drawer or (lambda: screened_pool.draw_lottery(n))
    if analyzer is None:
        from aria_core.skills.vc_analysis import analyze_vc_with_context

        analyzer = analyze_vc_with_context

    tokens = await draw()
    ids: list[int] = []
    for tok in tokens:
        contract = tok["contract"] if isinstance(tok, dict) else tok
        try:
            result, ctx = await analyzer(contract)
        except Exception as exc:  # noqa: BLE001 — one failing token doesn't break the batch
            logger.warning("weekly: analysis failed for %s (%s) — skipped", contract, exc)
            continue
        best = ctx.best_pair
        pid = await vc_predictions.record_prediction(
            contract=contract,
            recommandation=result.recommandation,
            potentiel=result.potentiel,
            risque=result.risque,
            taille_pct=result.taille_pct,
            security_score=result.security_score,
            llm_used=result.llm_used,
            report_ref=f"weekly-{contract[:10]}",
            strategy="vc",
            entry_price=(best.price_usd if best else None),
            pool_address=(best.pair_address if best else ""),
            network="base",
        )
        ids.append(pid)
        # Journal: records the thesis + a screenshot (candles + simulation).
        # Never blocking: a journal write failure doesn't break the batch.
        try:
            await _journal_forecast(contract, result, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.info("weekly: journal write failed for %s (%s)", contract, exc)
    logger.info("weekly: %s predictions recorded out of %s tokens drawn", len(ids), len(tokens))
    return ids


def _num(v) -> float | None:
    """Parses a price possibly given as text ('$0,012' -> 0.012). None if impossible."""
    try:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


async def _journal_forecast(contract: str, result, ctx) -> None:
    """Records a prediction in the journal + generates a screenshot (data-gated)."""
    from aria_core import thesis_journal as tj

    entry = _num(getattr(result, "entree", None)) or (ctx.ta_entry.entree if ctx.ta_entry else None)
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    chart_ref = ""
    if ctx.ta_candles:
        chart_ref = tj.save_entry_screenshot(
            contract, ctx.ta_candles, entry=entry, invalidation=inval, target=target
        )
    await tj.record_entry(tj.JournalEntry(
        contract=contract,
        symbol=(ctx.best_pair.base_symbol if ctx.best_pair else ""),
        decision=getattr(result, "recommandation", "") or "",
        thesis=(getattr(result, "these", "") or "")[:1000],
        reasoning="; ".join((ctx.risk_flags or [])[:3]),
        facts=list((ctx.risk_flags or [])[:8]),
        entry_price=entry, target_price=target, invalidation_price=inval,
        chart_ref=chart_ref,
    ))


async def run_thesis_review() -> dict:
    """Autonomous monitoring pass: re-checks every open position (price + activity).

    Assembles open predictions (BUY, not closed), resolves the current price
    via the pool's OHLCV, attempts GitHub activity, records a checkpoint per
    position and surfaces ALERTS (stagnant/invalidated thesis). Returns
    {reviewed, alerts:[...]}.
    """
    from aria_core import thesis_journal as tj

    open_preds = await vc_predictions.list_open_predictions(limit=1000)
    positions: list[dict] = []
    pool_by_contract: dict[str, tuple[str, str]] = {}
    for p in open_preds:
        c = p.get("contract")
        if not c or (p.get("recommandation") not in ("BUY", "WATCH")):
            continue
        positions.append({
            "contract": c,
            "entry_price": p.get("entry_price"),
            "invalidation_price": p.get("invalidation_price"),
            "github_url": p.get("github_url"),  # often absent -> activity 'unknown'
        })
        pool_by_contract[c] = ((p.get("pool_address") or "").strip(), p.get("network") or "base")

    from aria_core.services.ohlcv import ohlcv_client

    async def price_fn(contract: str):
        pool, network = pool_by_contract.get(contract, ("", "base"))
        if not pool:
            return None
        res = await ohlcv_client.get_ohlcv(pool, network=network)
        return res.candles[-1].close if (res.available and res.candles) else None

    alerts = await tj.review_open_theses(positions, price_fn=price_fn)
    logger.info("thesis_review: %s positions, %s alerts", len(positions), len(alerts))
    return {"reviewed": len(positions), "alerts": alerts}


async def due_predictions_summary(*, now: datetime | None = None) -> dict:
    """How many open predictions are actually due now, vs still within their
    horizon -- a distinction missing from ``proactive.py::_real_state_snapshot``
    until 14/07: a simple "at least one open prediction" status let the
    initiative LLM propose to "finalize the open prediction" when none had
    actually reached its due date (30-day VC horizon, none of the 10 existing
    predictions can resolve before early August) -- a confabulation observed
    live on Telegram, retracted only because the operator asked for the exact
    numbers. Reuses HORIZON_DAYS/the calculation already proven in
    ``resolve_due``, never duplicated."""
    now = now or datetime.now(timezone.utc)
    open_preds = await vc_predictions.list_open_predictions(limit=1000)
    due = 0
    nearest_due_at: datetime | None = None
    for p in open_preds:
        created = _parse_iso(p.get("created_at") or "")
        if created is None:
            continue
        horizon = HORIZON_DAYS.get(p.get("strategy") or "vc", 30)
        due_at = created + timedelta(days=horizon)
        if now >= due_at:
            due += 1
        elif nearest_due_at is None or due_at < nearest_due_at:
            nearest_due_at = due_at
    return {
        "open_total": len(open_preds),
        "due_now": due,
        "nearest_due_at": nearest_due_at.date().isoformat() if nearest_due_at else None,
    }


async def resolve_due(*, now: datetime | None = None, price_fn=None) -> dict:
    """Closes predictions that reached their horizon via the real current OHLCV price.

    ``price_fn(pool_address, network)`` → current price (default: last OHLCV close).
    A prediction with no entry price / pool, or whose current price is
    unavailable, is left open (never resolved on a made-up value).
    """
    now = now or datetime.now(timezone.utc)
    if price_fn is None:
        from aria_core.services.ohlcv import ohlcv_client

        async def price_fn(pool: str, network: str) -> float | None:
            res = await ohlcv_client.get_ohlcv(pool, network=network)
            return res.candles[-1].close if (res.available and res.candles) else None

    open_preds = await vc_predictions.list_open_predictions(limit=1000)
    resolved = 0
    for p in open_preds:
        entry = p.get("entry_price")
        pool = (p.get("pool_address") or "").strip()
        created = _parse_iso(p.get("created_at") or "")
        if not entry or not pool or created is None:
            continue
        horizon = HORIZON_DAYS.get(p.get("strategy") or "vc", 30)
        if (now - created).days < horizon:
            continue  # not yet due (multi-horizon)
        current = await price_fn(pool, p.get("network") or "base")
        if current is None or entry <= 0:
            continue
        pct = (current - entry) / entry * 100.0
        await vc_predictions.close_prediction(
            p["id"], outcome_pct=round(pct, 2), note=f"auto OHLCV @{current:.6g}"
        )
        resolved += 1
    logger.info("weekly: %s predictions resolved (due date reached)", resolved)
    return {"resolved": resolved, "open_checked": len(open_preds)}


async def weekly_report() -> dict:
    """Weekly digest: calibration + tracked wallet value + pool size."""
    metrics = await vc_predictions.metrics()
    wallet = await vc_predictions.live_wallet()
    pool_active = await screened_pool.count_pool("active")
    return {
        "calibration": metrics,
        "wallet": wallet,
        "pool_active": pool_active,
    }


async def self_report() -> str:
    """ARIA's "health & settings" digest, intended for the operator (Telegram).

    This is how ARIA surfaces what needs attention/adjustment: calibration
    status, tracked wallet value, pool size (enough candidates?), and
    improvement candidates awaiting validation. Short, factual text.
    """
    rep = await weekly_report()
    m, w = rep["calibration"], rep["wallet"]
    lines = ["🩺 ARIA — santé & réglages"]

    hit = m.get("hit_rate")
    hit_str = f"{hit * 100:.0f}%" if hit is not None else "n/a (pas encore de BUY clôturé)"
    lines.append(
        f"• Calibration : {m['closed']} clôturés / {m['open']} ouverts · "
        f"hit-rate BUY {hit_str} · {m.get('avoid_count', 0)} AVOID (Wall of NO)"
    )
    lines.append(
        f"• Wallet suivi : indice {w['index']} ({w['total_return_pct']:+.1f}%) · "
        f"VC {w['vc_return_pct']:+.1f}% / spéc {w['spec_return_pct']:+.1f}% · "
        f"{w['positions_valued']} positions valorisées"
    )

    pool = rep["pool_active"]
    warn = " ⚠️ pool maigre" if pool < 20 else ""
    lines.append(f"• Pool screené actif : {pool}{warn}")

    try:
        from aria_core.skills.candidate_ranking import top_candidates

        tops = await top_candidates(5)
        if tops:
            head = ", ".join(f"{t.symbol or t.contract[:8]} ({t.rank_score:.0f})" for t in tops)
            lines.append(f"• 🥇 Top candidats (tri) : {head}")
    except Exception:  # noqa: BLE001 — le digest ne casse jamais
        pass

    try:
        from aria_core import improvement_ledger

        counts = await improvement_ledger.count_by_status()
        proposed = counts.get("proposed", 0)
        if proposed:
            lines.append(f"• 💡 {proposed} amélioration(s) en attente de ta validation (carnet)")
    except Exception:  # noqa: BLE001 — le digest ne casse jamais
        pass

    try:
        from aria_core import recalibration

        pending = await recalibration.count_pending()
        if pending:
            lines.append(
                f"• 🔎 {pending} token(s) prometteur(s) mais opaque(s) : recalibrage demandé "
                "(transparence insuffisante pour trancher)"
            )
    except Exception:  # noqa: BLE001 — le digest ne casse jamais
        pass

    if hit is not None and m.get("closed", 0) >= 10 and hit < 0.4:
        lines.append("• 🔧 Réglage suggéré : hit-rate bas → durcir le filtre ou revoir le seuil R/R")

    return "\n".join(lines)
