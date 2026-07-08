"""Boucle d'entraînement hebdomadaire (walk-forward) — le cœur qui accumule la preuve.

- **Lundi** : ``run_weekly_forecasts`` tire N tokens du pool screené, les analyse et
  enregistre N pronostics horodatés (avec prix d'entrée + pool + poche 85/15) →
  falsifiables.
- **Échéance** : ``resolve_due`` clôture les pronostics arrivés à horizon en comparant
  le prix d'entrée au **prix OHLCV réel** courant (spec ~7 j, VC ~30 j). Multi-horizon :
  une thèse VC ne se juge pas en une semaine.
- **Rapport** : ``weekly_report`` agrège calibration + valeur du wallet suivi + pool.

Toutes les dépendances externes (tirage, analyse, prix OHLCV, horloge) sont
**injectables** → testable hors-ligne. En prod, les défauts branchent le vrai pipeline.
Aucune action financière : on enregistre et on mesure, on ne trade rien.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aria_core import screened_pool, vc_predictions

logger = logging.getLogger(__name__)

# Horizons de résolution par poche (jours).
HORIZON_DAYS = {"vc": 30, "spec": 7}


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


async def run_weekly_forecasts(
    *, n: int = 20, drawer=None, analyzer=None
) -> list[int]:
    """Tire N tokens du pool, les analyse, enregistre N pronostics datés. Retourne les ids.

    ``drawer()`` → liste de tokens du pool (défaut : loterie du pool screené).
    ``analyzer(contract)`` → ``(VCResult, TokenScanContext)`` (défaut : analyse VC réelle).
    Le prix d'entrée (spot au moment du pronostic) et le pool sont capturés depuis le
    contexte → indispensables à la résolution automatique ultérieure.
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
        except Exception as exc:  # noqa: BLE001 — un token qui échoue ne casse pas la fournée
            logger.warning("weekly: analyse échouée pour %s (%s) — ignoré", contract, exc)
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
        # Carnet de bord : consigne la thèse + un screenshot (chandeliers + simulation).
        # Jamais bloquant : un échec d'écriture du carnet ne casse pas la fournée.
        try:
            await _journal_forecast(contract, result, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.info("weekly: écriture carnet échouée pour %s (%s)", contract, exc)
    logger.info("weekly: %s pronostics enregistrés sur %s tokens tirés", len(ids), len(tokens))
    return ids


def _num(v) -> float | None:
    """Parse un prix éventuellement en texte ('$0,012' -> 0.012). None si impossible."""
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
    """Consigne un pronostic dans le carnet de bord + génère un screenshot (data-gated)."""
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
    """Tour de surveillance autonome : re-vérifie chaque position ouverte (prix + activité).

    Assemble les pronostics ouverts (BUY, non clôturés), résout le prix courant via
    l'OHLCV du pool, tente l'activité GitHub, consigne un checkpoint par position et
    remonte les ALERTES (thèse stagnante/invalidée). Retourne {reviewed, alerts:[...]}.
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
            "github_url": p.get("github_url"),  # souvent absent -> activité 'unknown'
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
    logger.info("thesis_review: %s positions, %s alertes", len(positions), len(alerts))
    return {"reviewed": len(positions), "alerts": alerts}


async def resolve_due(*, now: datetime | None = None, price_fn=None) -> dict:
    """Clôture les pronostics arrivés à horizon via le prix OHLCV réel courant.

    ``price_fn(pool_address, network)`` → prix courant (défaut : dernier close OHLCV).
    Un pronostic sans prix d'entrée / pool, ou dont le prix courant est indisponible,
    est laissé ouvert (jamais résolu sur une valeur inventée).
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
            continue  # pas encore à échéance (multi-horizon)
        current = await price_fn(pool, p.get("network") or "base")
        if current is None or entry <= 0:
            continue
        pct = (current - entry) / entry * 100.0
        await vc_predictions.close_prediction(
            p["id"], outcome_pct=round(pct, 2), note=f"auto OHLCV @{current:.6g}"
        )
        resolved += 1
    logger.info("weekly: %s pronostics résolus (échéance atteinte)", resolved)
    return {"resolved": resolved, "open_checked": len(open_preds)}


async def weekly_report() -> dict:
    """Digest hebdo : calibration + valeur du wallet suivi + taille du pool."""
    metrics = await vc_predictions.metrics()
    wallet = await vc_predictions.live_wallet()
    pool_active = await screened_pool.count_pool("active")
    return {
        "calibration": metrics,
        "wallet": wallet,
        "pool_active": pool_active,
    }


async def self_report() -> str:
    """Digest « santé & réglages » d'ARIA, destiné à l'opérateur (Telegram).

    C'est ainsi qu'ARIA fait remonter ce qui a besoin d'attention/réglage : état de
    la calibration, valeur du wallet suivi, taille du pool (assez de candidats ?),
    et candidats d'amélioration en attente de validation. Texte court, factuel.
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
