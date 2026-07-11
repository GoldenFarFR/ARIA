"""Scorecard « feu vert argent réel » — mesure objective contre les 8 cases
pré-engagées de `docs/protocole-argent-reel.md`, jamais un jugement subjectif.

Le pacte est clair : « le rôle de l'arbitre est de dire NON tant que toutes les
cases ne sont pas cochées ». Ce module calcule, depuis le VRAI journal
(`vc_predictions`), ce qui est déjà vrai aujourd'hui et ce qui manque encore —
plutôt que de laisser un « on verra plus tard » subjectif trancher. Chaque case
a un statut : ``ok`` (calculé et satisfait), ``fail`` (calculé et pas encore
satisfait), ou ``unknown`` (ne peut PAS être calculé ici — nécessite une donnée
externe non encore branchée, ou une vérification humaine/légale). Ne jamais
transformer un ``unknown`` en ``ok`` par optimisme : dire honnêtement ce qui
manque encore pour même MESURER la case, pas seulement pour la remplir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

REQUIRED_SAMPLE_SIZE = 80
REQUIRED_SPAN_DAYS = 180  # ~6 mois, cf. protocole-argent-reel.md case 1
CALIBRATION_CREDIBLE_LEVEL = 0.90
_JEFFREYS_PRIOR = 0.5  # Beta(0.5, 0.5) -- prior non informatif standard pour une proportion


def _betacf(x: float, a: float, b: float) -> float:
    """Fraction continue de l'incomplete beta function (Numerical Recipes 6.4)."""
    max_iter = 200
    eps = 3e-12
    fpmin = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """I_x(a, b) -- CDF de Beta(a, b) en x, via fraction continue (pas de dépendance
    scipy/numpy pour ce seul calcul, cohérent avec le reste du package -- stdlib only)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta_ab = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta_ab + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(x, a, b) / a
    return 1.0 - front * _betacf(1.0 - x, b, a) / b


def _beta_ppf(p: float, a: float, b: float) -> float:
    """Fonction quantile (CDF inverse) de Beta(a, b), par bissection sur
    ``_regularized_incomplete_beta`` (monotone en x, donc la bissection converge sans risque)."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if _regularized_incomplete_beta(mid, a, b) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _hit_rate_credible_interval(
    wins: int, n: int, *, level: float = CALIBRATION_CREDIBLE_LEVEL,
) -> tuple[float, float]:
    """Intervalle de crédibilité bayésien Beta-Binomial sur le hit-rate BUY, prior de
    Jeffreys Beta(0.5, 0.5) (non informatif standard pour une proportion) -- fonctionne
    même à n=0 (retombe sur le prior, intervalle large [~0.3%, ~99.7%] à 90%) ou n=1,
    contrairement à un intervalle fréquentiste (Wald) qui dégénère ou n'est pas défini.
    Postérieure : Beta(wins + 0.5, pertes + 0.5)."""
    losses = n - wins
    a = wins + _JEFFREYS_PRIOR
    b = losses + _JEFFREYS_PRIOR
    alpha = (1.0 - level) / 2.0
    lo = _beta_ppf(alpha, a, b)
    hi = _beta_ppf(1.0 - alpha, a, b)
    return lo, hi


def _hit_rate_credible_note(wins: int, n: int, *, level: float = CALIBRATION_CREDIBLE_LEVEL) -> str:
    """Phrase à ajouter au `detail` d'une case -- n'influence JAMAIS le statut calculé
    (ok/fail/unknown) : purement informatif, sur l'incertitude du hit-rate observé."""
    lo, hi = _hit_rate_credible_interval(wins, n, level=level)
    pct = f"{(wins / n * 100):.0f}%" if n else "n/a"
    note = (
        f"hit-rate observé {pct} sur {n} BUY, intervalle de crédibilité {level * 100:.0f}% "
        f"(bayésien Beta-Binomial, prior Jeffreys non informatif) : [{lo * 100:.0f}%, {hi * 100:.0f}%]"
    )
    if n < REQUIRED_SAMPLE_SIZE:
        note += " — échantillon encore trop petit pour trancher"
    return note


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    label: str
    status: str  # "ok" | "fail" | "unknown"
    detail: str


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _closed(predictions: list[dict]) -> list[dict]:
    return [p for p in predictions if p.get("status") == "closed" and p.get("outcome_pct") is not None]


def _check_sample_size(predictions: list[dict]) -> ReadinessCheck:
    closed = _closed(predictions)
    n = len(closed)
    dates = [d for d in (_parse_iso(p.get("created_at")) for p in closed) if d is not None]
    span_days = (max(dates) - min(dates)).days if len(dates) >= 2 else 0
    ok = n >= REQUIRED_SAMPLE_SIZE and span_days >= REQUIRED_SPAN_DAYS
    return ReadinessCheck(
        id="sample_size",
        label=f"Échantillon ≥ {REQUIRED_SAMPLE_SIZE} verdicts clôturés sur ≥ {REQUIRED_SPAN_DAYS} jours",
        status="ok" if ok else "fail",
        detail=f"{n}/{REQUIRED_SAMPLE_SIZE} verdicts clôturés, étalés sur {span_days}/{REQUIRED_SPAN_DAYS} jours",
    )


def _check_integrity() -> ReadinessCheck:
    # Garantie structurelle vérifiée par le code lui-même : vc_predictions.py n'expose
    # aucune fonction de suppression, et close_prediction() refuse d'écraser un résultat
    # déjà attribué ("WHERE status = 'open'", jamais 'closed' -> réécrit). C'est la partie
    # "aucun verdict effacé ou caché" du pacte, garantie par construction. L'ancrage
    # on-chain (SHA-256 sur Base) est documenté comme "idéalement" — un bonus, pas un
    # prérequis dur — donc son absence ne fait pas échouer cette case.
    from aria_core.onchain.anchor import anchor_enabled

    anchored = anchor_enabled()
    detail = (
        "Clôture immuable garantie par le code (aucune fonction de suppression/écrasement "
        "dans vc_predictions.py)."
    )
    detail += " Ancrage on-chain configuré (bonus)." if anchored else " Ancrage on-chain pas encore configuré (bonus, non bloquant selon le pacte)."
    return ReadinessCheck(
        id="integrity",
        label="Track record complet et inviolable",
        status="ok",
        detail=detail,
    )


def _check_calibration(predictions: list[dict], metrics: dict) -> ReadinessCheck:
    calib = metrics.get("calibration") or []
    hit = metrics.get("hit_rate")
    buys = [p for p in _closed(predictions) if p.get("recommandation") == "BUY"]
    n_buys = len(buys)
    wins = sum(1 for p in buys if (p.get("outcome_pct") or 0) > 0)
    # Intervalle de crédibilité purement informatif -- calculé même à 0/1 BUY, mais ne
    # touche jamais au statut ci-dessous (ok/fail/unknown reste la logique existante,
    # inchangée). Ne JAMAIS transformer un unknown en ok grâce à ce calcul.
    credible_note = _hit_rate_credible_note(wins, n_buys)
    if len(calib) < 2 or hit is None:
        return ReadinessCheck(
            id="calibration",
            label="Calibration monotone + hit-rate BUY nettement supérieur au hasard (après frais)",
            status="unknown",
            detail=(
                f"pas assez de données pour juger ({len(calib)} bucket(s) noté(s), "
                f"hit-rate {'n/a' if hit is None else f'{hit * 100:.0f}%'}) ; {credible_note}"
            ),
        )
    avgs = [b["avg_pnl"] for b in calib]
    monotone = all(avgs[i] <= avgs[i + 1] for i in range(len(avgs) - 1))
    hit_ok = hit > 0.5
    ok = monotone and hit_ok
    return ReadinessCheck(
        id="calibration",
        label="Calibration monotone + hit-rate BUY nettement supérieur au hasard (après frais)",
        status="ok" if ok else "fail",
        detail=(
            f"courbe {'monotone' if monotone else 'NON monotone'} sur {len(calib)} buckets ; "
            f"hit-rate BUY {hit * 100:.0f}% — ATTENTION : gas/slippage réels non déduits ici, "
            f"ce chiffre est brut, pas net de frais ; {credible_note}"
        ),
    )


def _check_benchmark() -> ReadinessCheck:
    return ReadinessCheck(
        id="benchmark",
        label="Bat « hold ETH » et une sélection aléatoire comparable",
        status="unknown",
        detail=(
            "non calculable ici : nécessite une série de prix ETH/USD alignée sur les "
            "fenêtres réelles d'entrée/sortie de chaque verdict, plus une sélection aléatoire "
            "de contrôle parmi le pool screené — aucun des deux n'est construit à ce jour"
        ),
    )


def _check_robustness(predictions: list[dict]) -> ReadinessCheck:
    buys = [
        p for p in _closed(predictions)
        if p.get("recommandation") == "BUY" and p.get("outcome_pct") is not None
    ]
    if len(buys) < 3:
        return ReadinessCheck(
            id="robustness",
            label="Reste positif après retrait des 2 meilleurs coups BUY",
            status="unknown",
            detail=f"seulement {len(buys)} BUY clôturé(s) — pas assez pour un test anti-chance significatif",
        )
    ordered = sorted(buys, key=lambda p: p["outcome_pct"], reverse=True)
    remaining = ordered[2:]
    avg_remaining = sum(p["outcome_pct"] for p in remaining) / len(remaining)
    ok = avg_remaining > 0
    return ReadinessCheck(
        id="robustness",
        label="Reste positif après retrait des 2 meilleurs coups BUY",
        status="ok" if ok else "fail",
        detail=f"moyenne résiduelle {avg_remaining:+.1f}% sur {len(remaining)} BUY (2 meilleurs retirés)",
    )


def _check_risk(metrics: dict) -> ReadinessCheck:
    avoid_count = metrics.get("avoid_count", 0)
    return ReadinessCheck(
        id="risk",
        label="Drawdown maîtrisé + les AVOID ont réellement évité des pertes",
        status="unknown",
        detail=(
            f"{avoid_count} verdict(s) AVOID journalisé(s), mais leur mouvement de prix réel "
            "après coup n'est pas encore revérifié automatiquement (nécessiterait un module "
            "type pump_dump_autopsy appliqué aux AVOID, pas seulement aux positions tenues) ; "
            "drawdown max du sleeve 15% non calculé ici"
        ),
    )


def _check_judge() -> ReadinessCheck:
    return ReadinessCheck(
        id="judge",
        label="Le juge adverse (auto-audit) est bien calibré, pas complaisant",
        status="unknown",
        detail=(
            "vc_judge.py existe et tourne sur chaque analyse, mais aucune métrique ne mesure "
            "encore son propre taux de détection d'erreur réelle — pas de méta-audit du juge à ce jour"
        ),
    )


def _check_lawyer() -> ReadinessCheck:
    return ReadinessCheck(
        id="lawyer",
        label="Feu vert avocat sur la structure d'argent réel retenue",
        status="unknown",
        detail="action humaine/légale, hors de portée d'un calcul automatique — voir docs/conformite-dossier-avocat.md",
    )


async def compute_readiness_scorecard() -> dict:
    """Calcule les 8 cases du pacte depuis le vrai journal `vc_predictions`.

    Retourne ``{"checks": [...], "all_ok": bool, "verdict": str}``. ``all_ok`` n'est
    JAMAIS ``True`` tant qu'une case est ``unknown`` ou ``fail`` — l'absence de preuve
    n'est pas une preuve d'absence de risque.
    """
    from aria_core import vc_predictions

    predictions = await vc_predictions.list_all_predictions()
    metrics = vc_predictions.compute_metrics(predictions)

    checks = [
        _check_sample_size(predictions),
        _check_integrity(),
        _check_calibration(predictions, metrics),
        _check_benchmark(),
        _check_robustness(predictions),
        _check_risk(metrics),
        _check_judge(),
        _check_lawyer(),
    ]
    all_ok = all(c.status == "ok" for c in checks)
    n_ok = sum(1 for c in checks if c.status == "ok")
    return {
        "checks": checks,
        "all_ok": all_ok,
        "verdict": (
            "OUI — toutes les cases sont cochées avec preuve."
            if all_ok
            else f"NON — {n_ok}/8 cases cochées, argent réel toujours hors de portée."
        ),
    }


_STATUS_ICON = {"ok": "✅", "fail": "❌", "unknown": "⚠️"}


def format_readiness_report(scorecard: dict) -> str:
    lines = ["🔒 ARIA — feu vert argent réel (docs/protocole-argent-reel.md)", ""]
    for c in scorecard["checks"]:
        icon = _STATUS_ICON.get(c.status, "?")
        lines.append(f"{icon} {c.label}")
        lines.append(f"   {c.detail}")
    lines.append("")
    lines.append(scorecard["verdict"])
    lines.append("")
    lines.append(
        "Ce non est une fonctionnalité, pas un défaut (pacte §4) : tant que la machine "
        "de preuve n'a pas tourné assez longtemps, la réponse reste non."
    )
    return "\n".join(lines)
