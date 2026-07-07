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
    logger.info("weekly: %s pronostics enregistrés sur %s tokens tirés", len(ids), len(tokens))
    return ids


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
