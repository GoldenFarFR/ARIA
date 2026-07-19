"""Gestion du risque portefeuille (#186, 15/07) — sizing ajusté au risque +
coupe-circuit de drawdown, appliqués pour l'instant au portefeuille papier
1 M$ uniquement (``paper_trader.py``). Aucun câblage vers un pilote de
capital réel (pas encore construit) -- mais ce module est conçu comme un
seam réutilisable tel quel le jour où un pilote réel existera : les deux
fonctions ci-dessous ne connaissent rien de « papier » vs « réel », elles ne
travaillent qu'avec des USD/prix/compteurs génériques.

Recherche à l'origine de ce chantier : Paul Tudor Jones (jamais >1 % du
capital risqué par trade, indépendant de la taille de position) et Ray
Dalio/Bridgewater (jamais laisser un drawdown dépasser ~1/3 du capital --
au-delà, la remontée mathématique devient punitive : -50 % exige +100 % pour
revenir à zéro). ``RISK_CAP_PCT``/``HARD_DRAWDOWN_PCT`` ci-dessous sont
délibérément plus conservateurs que ces bornes extrêmes (2 %/20 % plutôt que
1 %/33 %), cohérent avec un capital encore fictif mais dont l'objectif est
de prouver une discipline transposable au réel.

Deux mécanismes distincts, à ne jamais confondre :
1. Sizing par trade (``size_position_by_risk``) -- fonction PURE, aucun état
   persisté, plafonne une allocation en fonction de la distance à
   l'invalidation. Ne relève JAMAIS une allocation au-delà de sa valeur
   d'entrée -- un plafond, jamais un bonus.
2. Coupe-circuit de portefeuille (``evaluate_portfolio_risk``/
   ``blocks_new_entries``) -- état persisté (fichier JSON dédié, PAS
   ``outgoing_pause.py`` -- ce kill-switch global coupe aussi des cycles
   sans rapport avec l'argent, ex. ``knowledge_inbox``). ``blocks_new_entries``
   respecte lui-même ``outgoing_pause`` (une pause globale bloque aussi les
   nouvelles entrées paper) SANS jamais être confondu avec lui -- deux
   fichiers d'état séparés, deux raisons distinctes rapportées à l'appelant.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

# ── 1. Sizing ajusté au risque (fonction pure, aucun état) ─────────────────

RISK_CAP_PCT = 0.02  # 2 % du capital total risqué au pire cas (entre le 1 % très
# conservateur de PTJ et le maximum actuel implicite ~5 % du flat ALLOC_PCT).


def size_position_by_risk(
    alloc_usd: float, entry_price: float, invalidation_price: float | None, capital_total: float,
) -> float:
    """Plafonne ``alloc_usd`` pour que la perte au pire cas (si le prix touche
    ``invalidation_price``) ne dépasse jamais ``RISK_CAP_PCT * capital_total``.
    Ne relève JAMAIS ``alloc_usd`` au-delà de sa valeur d'entrée -- un
    plafond, jamais un bonus (une position à stop TRÈS serré garde son
    allocation flat d'origine, elle n'est jamais gonflée par ce mécanisme).

    Sans invalidation connue (``None``, ou ``>= entry_price`` -- risque non
    mesurable ou donnée incohérente), ``alloc_usd`` est renvoyé inchangé : le
    stop suiveur (``TRAIL_STOP_PCT`` dans ``paper_trader.py``) reste alors le
    seul garde-fou, comme avant ce chantier."""
    if alloc_usd <= 0 or entry_price <= 0 or capital_total <= 0:
        return alloc_usd
    if invalidation_price is None or invalidation_price <= 0 or invalidation_price >= entry_price:
        return alloc_usd

    risk_fraction = (entry_price - invalidation_price) / entry_price  # perte % si stop touché
    if risk_fraction <= 0:
        return alloc_usd

    risked_usd = alloc_usd * risk_fraction
    cap_usd = RISK_CAP_PCT * capital_total
    if risked_usd <= cap_usd:
        return alloc_usd

    capped_alloc = cap_usd / risk_fraction
    return min(alloc_usd, capped_alloc)


# 18/07 -- décision opérateur explicite : "plus agressive" veut dire plus gros sur les
# MEILLEURS setups, pas plus gros partout (jamais un bonus flat). Deuxième fonction PURE,
# aucun état -- s'applique en AMONT de size_position_by_risk ci-dessus, qui reste le vrai
# plafond de perte au pire cas (2 % du capital) : une allocation gonflée par conviction
# reste plafonnée exactement comme avant sur un stop large, ce n'est jamais un pari sans
# filet.
CONVICTION_RR_THRESHOLD = 2.5
# 19/07 -- abaissé de 3 à 2 (décision opérateur explicite, via AskUserQuestion) : sur
# les 5 premiers trades réels du pipeline momentum (#194), align_score n'a JAMAIS
# atteint 3/3 -- toujours "MACD au-dessus de sa ligne de signal" + "pattern de bougie
# bullish", jamais "EMA12 > EMA26" en même temps. Hypothèse vérifiée dans le code (pas
# un bug) : un achat golden-pocket (rechargement PROFOND) est structurellement en
# tension avec "EMA courte déjà repassée au-dessus de la longue" -- au moment où le
# prix recharge en profondeur, l'EMA rapide est souvent encore sous la lente. Avec le
# seuil à 3, le bonus de conviction technique était donc quasi inatteignable pour ce
# style d'entrée précis, jamais un pari sans filet pour autant (R/R minimum inchangé).
CONVICTION_ALIGN_SCORE_THRESHOLD = 2

# 19/07 (suite) -- REDESIGN complet du sizing (feedback opérateur direct, après avoir vu
# le portefeuille réel : "les position sont trop grosse, l'achat maxi doit etre de 5% et
# mini de 2%"). Remplace le binaire précédent (base flat 5 % / bonus exceptionnel -> 8 %,
# ``CONVICTION_SIZE_MULTIPLIER=1.6`` -- RETIRÉ, l'opérateur plafonne explicitement à 5 %
# max désormais) par 3 paliers de conviction, mappés directement sur le pourcentage réel
# du capital de départ (jamais un multiplicateur > 1.0 -- 5 % EST le plafond, pas un
# multiplicateur d'un multiplicateur). ``MODERATE_RR_THRESHOLD`` reprend exactement le
# R/R minimum du chemin d'achat DIRECT (``momentum_entry._RR_MIN_FOR_DIRECT_BUY``, 2.0) --
# volontairement une constante indépendante ici (pas un import cross-module) pour garder
# ``risk_guard`` autonome de ``momentum_entry``, même doctrine que ``CONVICTION_RR_
# THRESHOLD`` déjà indépendant depuis l'origine de ce chantier.
MODERATE_RR_THRESHOLD = 2.0

MIN_ALLOC_MULTIPLIER = 0.4       # 5 % * 0.4 = 2 % du capital de départ (palier faible)
MODERATE_ALLOC_MULTIPLIER = 0.7  # 5 % * 0.7 = 3.5 % du capital de départ (palier modéré)
MAX_ALLOC_MULTIPLIER = 1.0       # 5 % * 1.0 = 5 % du capital de départ (palier fort, plafond dur)

# 19/07 -- décision opérateur explicite (choix confirmé via AskUserQuestion, "s'ajoute
# en ET") : le potentiel fondamental (conviction_research.py -- site web/X/cadence de
# publication/corroboration de contrat) devient un TROISIÈME critère du palier fort,
# EN PLUS du R/R+alignement technique déjà exigés -- jamais à leur place.
# Seuil sous lequel un score fondamental CONFIRMÉ (pas absent) rétrograde le palier --
# fail-closed sur une donnée confirmée mauvaise, fail-open sur une donnée INCONNUE
# (``fundamental_score=None``, ex. recherche indisponible/gate OFF) : un setup
# technique parfait sans recherche fondamentale disponible garde EXACTEMENT le palier
# qu'il aurait eu avant ce chantier -- jamais réduit sous ce qu'il a aujourd'hui, même
# doctrine fail-open/fail-closed déjà validée sur le wallet-scoring (smart_money.py).
FUNDAMENTAL_WEAK_THRESHOLD = 4.0


def conviction_size_multiplier(
    rr: float | None, align_score: int | None, *,
    fundamental_score: float | None = None, volume_confirmed: bool | None = None,
) -> float:
    """Multiplicateur appliqué sur ``ALLOC_PCT`` (5 %, ``paper_trader.py``) -- jamais
    au-delà de ``MAX_ALLOC_MULTIPLIER`` (1.0 = 5 % du capital, le plafond dur demandé
    par l'opérateur), jamais en dessous de ``MIN_ALLOC_MULTIPLIER`` (0.4 = 2 %) pour
    tout signal réellement mesuré. 3 paliers, sur le R/R (le seul signal qui discrimine
    encore une fois l'alignement technique passé à un seuil de 2/3 -- cf. ci-dessus) :
    - FORT (``MAX_ALLOC_MULTIPLIER``, 5 %) : R/R >= ``CONVICTION_RR_THRESHOLD`` (2.5) ET
      alignement >= ``CONVICTION_ALIGN_SCORE_THRESHOLD`` (2/3) -- le setup le plus solide.
    - MODÉRÉ (``MODERATE_ALLOC_MULTIPLIER``, 3.5 %) : R/R >= ``MODERATE_RR_THRESHOLD``
      (2.0, le plancher même du chemin d'achat direct) sans atteindre le palier fort.
    - FAIBLE (``MIN_ALLOC_MULTIPLIER``, 2 %) : tout le reste avec un signal mesuré
      (typiquement un achat confirmé par LLM sur R/R sous le plancher direct).

    Données absentes/incomplètes (``rr`` ou ``align_score`` = ``None``) ->
    ``MAX_ALLOC_MULTIPLIER`` : comportement INCHANGÉ pour tout appelant qui ne fournit
    pas ces signaux (ex. l'ancien pilote VC-thesis, dormant) -- jamais réduit sous ce
    qu'il avait avant ce chantier, seul le pipeline momentum (qui fournit toujours ces
    deux champs sur un BUY) est concerné par le nouveau plafond à 5 %.

    ``fundamental_score`` (19/07, optionnel) : si le palier FORT est atteint MAIS qu'une
    recherche fondamentale a CONFIRMÉ un potentiel faible (< ``FUNDAMENTAL_WEAK_
    THRESHOLD``), rétrograde le palier (voir cumul ci-dessous). ``None`` (recherche non
    menée/indisponible) ne rétrograde JAMAIS le palier technique.

    ``volume_confirmed`` (19/07, revue croisée Gemini, optionnel) : même doctrine de
    véto que ``fundamental_score`` -- ``False`` (le volume relatif de la bougie
    d'entrée n'a pas pu être vérifié, cf. ``momentum_entry._check_volume_confirmation``,
    état "unknown") rétrograde le palier (voir cumul ci-dessous). ``None``/``True`` ne
    rétrogradent jamais -- un ``False`` avec DONNÉE RÉELLE confirmant l'absence de
    volume (état "not_confirmed") n'atteint jamais cette fonction : ce cas-là est déjà
    un rejet dur en amont (``hold_reason="volume_not_confirmed"``), jamais une question
    de taille.

    Cumul des deux vétos (19/07, revue croisée Gemini, round 5 -- corrige un vrai défaut
    de gestion du risque : composer les deux drapeaux au MÊME palier MODÉRÉ traitait un
    setup avec DEUX signaux d'alerte indépendants (fondamentaux faibles ET volume non
    vérifié) comme équivalent à un setup avec un seul -- sous-estimant le risque cumulé)
    -- un seul drapeau -> palier MODÉRÉ (3.5 %) ; les DEUX en même temps -> chute directe
    au palier FAIBLE (2 %), jamais un 3e palier en dessous (le plancher ``MIN_ALLOC_
    MULTIPLIER`` reste le vrai plancher, quel que soit le nombre de vétos)."""
    if rr is None or align_score is None:
        return MAX_ALLOC_MULTIPLIER
    if rr >= CONVICTION_RR_THRESHOLD and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        flags = int(weak_fundamentals) + int(unconfirmed_volume)
        if flags >= 2:
            return MIN_ALLOC_MULTIPLIER
        if flags == 1:
            return MODERATE_ALLOC_MULTIPLIER
        return MAX_ALLOC_MULTIPLIER
    if rr >= MODERATE_RR_THRESHOLD:
        return MODERATE_ALLOC_MULTIPLIER
    return MIN_ALLOC_MULTIPLIER


# 18/07 (suite, revue croisée validée par l'opérateur) -- "frein à main" DÉTERMINISTE,
# jamais un LLM : une fois l'objectif hebdomadaire (+10 %) DÉJÀ atteint, les NOUVELLES
# entrées sont réduites de moitié plutôt que laissées pleine taille -- protège le gain
# déjà acquis sans jamais couper les nouvelles entrées à zéro (le marché ne sait pas
# qu'on a "fait sa semaine" ; un setup exceptionnel doublement vérifié garde une
# asymétrie positive, juste avec une mise réduite). Composé APRÈS conviction_size_
# multiplier (8 % -> 4 %, 5 % -> 2.5 %), lui-même plafonné ENSUITE par
# size_position_by_risk (2 % de perte max) -- jamais un contournement du plafond.
WEEKLY_PACING_DAMPENING_MULTIPLIER = 0.5


def weekly_pacing_size_multiplier(weekly_context: dict | None) -> float:
    """1.0 par défaut (comportement inchangé, y compris si ``weekly_context`` est absent
    ou incomplet -- jamais un frein sans preuve du contexte). ``WEEKLY_PACING_DAMPENING_
    MULTIPLIER`` UNIQUEMENT quand l'équité courante a déjà atteint/dépassé l'objectif de
    la semaine (``weekly_context["equity"] >= weekly_context["target_equity"]``)."""
    if not weekly_context:
        return 1.0
    equity = weekly_context.get("equity")
    target = weekly_context.get("target_equity")
    if equity is None or target is None:
        return 1.0
    if equity >= target:
        return WEEKLY_PACING_DAMPENING_MULTIPLIER
    return 1.0


# 19/07 -- plafond de position auto-calibré par IMPACT DE PRIX (revue croisée Gemini,
# relayée par l'opérateur, 19/07). Remplace le débat sur "quel % fixe du pool" par un
# calcul qui s'auto-ajuste à CHAQUE pool réel, sans nouveau seuil arbitraire de taille à
# choisir. Rien ne plafonnait jusqu'ici une position en fonction de la liquidité RÉELLE
# du pool ciblé (seul un plancher absolu existe, ``momentum_entry._MIN_LIQUIDITY_USD``)
# -- un ordre trop gros pour un pool mince fait bouger le prix artificiellement (ARIA se
# ferait son propre "price impact"), une réalité que le paper-trading ne modélisait pas.
#
# Principe (approximation AMM standard, citée par Gemini) : un ordre représentant X % de
# la liquidité totale du pool produit environ 2*X % d'impact de prix sur un pool
# équilibré (constant-product, x*y=k). Cette fonction DÉGRADE le prix d'entrée par cet
# impact estimé, recalcule le R/R structurel (cible/invalidation restent des niveaux
# Fibonacci/RSI fixes, indépendants de la taille de l'ordre) avec ce prix dégradé, et
# réduit ``alloc_usd`` (solution fermée, aucune itération) jusqu'à ce que le R/R dégradé
# revienne au moins à ``PRICE_IMPACT_MIN_RR`` -- volontairement un plancher FIXE et non
# le R/R brut du trade lui-même (piste envisagée puis écartée par le calcul : un R/R brut
# très élevé rendrait le plancher quasi inatteignable à N'IMPORTE QUELLE taille -- car
# tout impact positif fait strictement baisser le R/R en dessous de sa propre valeur de
# départ -- l'inverse de l'effet recherché : un signal plus fort doit tolérer PLUS de
# taille, pas moins).
PRICE_IMPACT_RATIO = 2.0  # règle AMM standard : X % du pool -> ~2*X % d'impact de prix
# Reprend délibérément la même valeur que ``momentum_entry._RR_AMBIGUOUS_FLOOR`` (R/R
# structurel minimum pour qu'un signal soit ne serait-ce que considéré comme un achat)
# SANS importer ce module -- même doctrine d'autonomie déjà appliquée à
# ``CONVICTION_RR_THRESHOLD``/``MODERATE_RR_THRESHOLD`` ci-dessus (constante
# indépendante, jamais un import cross-module).
PRICE_IMPACT_MIN_RR = 1.0


def cap_alloc_to_price_impact(
    alloc_usd: float, entry_price: float, target_price: float | None,
    invalidation_price: float | None, pool_liquidity_usd: float | None,
) -> float:
    """Réduit ``alloc_usd`` si l'impact de prix de CET ordre sur CE pool ferait tomber le
    R/R structurel sous ``PRICE_IMPACT_MIN_RR`` -- jamais une hausse au-delà de la valeur
    d'entrée (même doctrine que ``size_position_by_risk``). Peut renvoyer ``0.0`` (aucune
    taille viable, même infinitésimale, sur ce pool avec cette structure de trade).
    Données manquantes/incohérentes (cible, invalidation ou liquidité absentes, ou
    structure non haussière) -> inchangé, fail-open -- le garde-fou dur sur la liquidité
    du pool vit déjà dans ``momentum_entry._MIN_LIQUIDITY_USD``, ce n'est pas le rôle de
    cette fonction."""
    if alloc_usd <= 0 or entry_price <= 0:
        return alloc_usd
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return alloc_usd
    if not target_price or not invalidation_price:
        return alloc_usd
    if target_price <= entry_price or invalidation_price >= entry_price:
        return alloc_usd  # structure non haussière -- pas le rôle de cette fonction

    impact_pct = PRICE_IMPACT_RATIO * (alloc_usd / pool_liquidity_usd)
    degraded_entry = entry_price * (1.0 + impact_pct)
    if degraded_entry < target_price:
        degraded_rr = (target_price - degraded_entry) / (degraded_entry - invalidation_price)
        if degraded_rr >= PRICE_IMPACT_MIN_RR:
            return alloc_usd  # impact négligeable à cette taille, rien à réduire

    # Solution fermée : prix d'entrée dégradé exact pour lequel R/R == PRICE_IMPACT_MIN_RR
    # (dérivé de (target - e) / (e - invalidation) = PRICE_IMPACT_MIN_RR), puis remontée
    # vers l'allocation qui produit ce prix dégradé (impact_pct linéaire en alloc_usd).
    target_degraded_entry = (
        target_price + PRICE_IMPACT_MIN_RR * invalidation_price
    ) / (1.0 + PRICE_IMPACT_MIN_RR)
    if target_degraded_entry <= entry_price:
        return 0.0  # même une taille infinitésimale ne tiendrait pas ce plancher ici

    k = PRICE_IMPACT_RATIO / pool_liquidity_usd
    capped_alloc = (target_degraded_entry / entry_price - 1.0) / k
    return max(0.0, min(alloc_usd, capped_alloc))


# ── 2. Coupe-circuit de portefeuille (état persisté, fichier dédié) ────────

SOFT_DRAWDOWN_PCT = 0.10       # -10 % depuis le plus haut d'équité -> alloc réduite de moitié
HARD_DRAWDOWN_PCT = 0.20       # -20 % depuis le plus haut -> bloque toute nouvelle entrée
HARD_CONSECUTIVE_LOSSES = 5    # 5 pertes consécutives -> bloque aussi toute nouvelle entrée
SOFT_ALLOC_MULTIPLIER = 0.5

_BAND_NONE = "none"
_BAND_SOFT = "soft"
_BAND_HARD = "hard"


def _state_path() -> Path:
    return data_dir() / "risk_guard_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Même sémantique à trois états que ``outgoing_pause._read_raw`` :
    ``{}`` (fichier absent -- jamais déclenché, pas un doute), ``dict``
    (lu correctement), ``None`` (corrompu -- état INCONNU)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("risk_guard_state illisible/corrompu (%s) — état INCONNU", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("risk_guard_state de forme inattendue (%r) — état INCONNU", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def new_entry_block_status() -> dict[str, Any]:
    """État courant du coupe-circuit DÉDIÉ (pas ``outgoing_pause``) :
    ``{blocked, since, reason, by, last_alert_band, readable}``.
    ``readable=False`` signale un fichier corrompu -- fail-closed côté
    appelant (``blocks_new_entries``), même doctrine « argent » que
    ``outgoing_pause.money_block_reason``."""
    raw = _read_raw()
    readable = raw is not None
    data = raw or {}
    since: datetime | None = None
    since_raw = data.get("since")
    if isinstance(since_raw, str):
        try:
            since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    return {
        "blocked": bool(data.get("blocked")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "last_alert_band": data.get("last_alert_band") or _BAND_NONE,
        "readable": readable,
    }


def block_new_entries(reason: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Arme le palier dur : plus aucune NOUVELLE position paper tant que
    ``resume_new_entries`` n'a pas été appelé explicitement (jamais
    automatique -- cf. docstring du module)."""
    status = new_entry_block_status()
    _write(
        {
            "blocked": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
            "last_alert_band": _BAND_HARD,
        }
    )
    logger.warning("risk_guard: coupe-circuit ARMÉ (palier dur) — reason=%s", reason)
    return new_entry_block_status()


def resume_new_entries(*, by: int | str | None = None) -> dict[str, Any]:
    """Lève le coupe-circuit. JAMAIS appelé automatiquement par
    ``evaluate_portfolio_risk`` -- réservé à une action humaine explicite
    (ex. commande opérateur), même si le drawdown s'est entre-temps résorbé."""
    _write(
        {
            "blocked": False,
            "since": None,
            "by": by,
            "reason": "",
            "last_alert_band": _BAND_NONE,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("risk_guard: coupe-circuit LEVÉ (reprise manuelle) — by=%s", by)
    return new_entry_block_status()


def blocks_new_entries() -> tuple[bool, str | None]:
    """``(bloqué, raison)`` -- combine le coupe-circuit dédié ET
    ``outgoing_pause`` (une pause globale bloque aussi les nouvelles entrées
    paper) SANS jamais confondre les deux mécanismes dans la raison
    rapportée. Fail-closed sur état illisible (doctrine « argent »)."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return True, "ARIA en pause globale (kill-switch sortant) — aucune nouvelle position paper tant que /start n'est pas donné."

    status = new_entry_block_status()
    if not status["readable"]:
        return True, "état du coupe-circuit portefeuille illisible/corrompu — fail-closed par sécurité"
    if status["blocked"]:
        return True, status["reason"] or "coupe-circuit portefeuille armé — reprise manuelle requise"
    return False, None


@dataclass
class PortfolioRiskState:
    equity: float
    high_water_mark: float
    drawdown_pct: float             # 0..1 depuis le plus haut
    consecutive_losses: int
    alloc_multiplier: float         # 1.0 normal, SOFT_ALLOC_MULTIPLIER si palier souple
    blocked: bool
    blocked_reason: str | None = None
    newly_triggered_soft: bool = False
    newly_triggered_hard: bool = False


async def evaluate_portfolio_risk(*, price_lookup=None) -> PortfolioRiskState:
    """Photo du risque portefeuille -- à appeler UNE FOIS par cycle, avant
    toute tentative d'ouverture de nouvelle position (jamais avant la
    gestion des positions déjà ouvertes, qui doit continuer normalement même
    coupe-circuit armé). Met à jour le plus haut d'équité persisté et arme le
    coupe-circuit dédié si un palier dur est franchi pour la première fois."""
    from aria_core import paper_trader

    summary = await paper_trader.portfolio_summary(price_lookup=price_lookup)
    equity = float(summary["equity"])

    hwm = await paper_trader.get_equity_high_water_mark()
    if equity > hwm:
        hwm = equity
        await paper_trader.set_equity_high_water_mark(hwm)
    drawdown_pct = max(0.0, (hwm - equity) / hwm) if hwm > 0 else 0.0

    closed = await paper_trader.get_closed_positions(limit=HARD_CONSECUTIVE_LOSSES)
    consecutive_losses = 0
    for p in closed:
        if (p.get("pnl_usd") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break

    status = new_entry_block_status()
    already_blocked = status["blocked"]
    hard_breach = drawdown_pct >= HARD_DRAWDOWN_PCT or consecutive_losses >= HARD_CONSECUTIVE_LOSSES
    newly_triggered_hard = False
    if hard_breach and not already_blocked and status["readable"]:
        reason = (
            f"drawdown {drawdown_pct:.1%} depuis le plus haut d'équité ({hwm:,.0f} $)"
            if drawdown_pct >= HARD_DRAWDOWN_PCT
            else f"{consecutive_losses} pertes consécutives"
        )
        block_new_entries(reason)
        newly_triggered_hard = True
        already_blocked = True

    soft_breach = SOFT_DRAWDOWN_PCT <= drawdown_pct < HARD_DRAWDOWN_PCT
    newly_triggered_soft = False
    if not already_blocked:
        last_band = status["last_alert_band"]
        if soft_breach and last_band != _BAND_SOFT:
            _write(
                {
                    "blocked": False,
                    "since": None,
                    "by": None,
                    "reason": "",
                    "last_alert_band": _BAND_SOFT,
                }
            )
            newly_triggered_soft = True
        elif not soft_breach and last_band == _BAND_SOFT:
            _write({"blocked": False, "since": None, "by": None, "reason": "", "last_alert_band": _BAND_NONE})

    blocked, blocked_reason = blocks_new_entries()
    alloc_multiplier = SOFT_ALLOC_MULTIPLIER if (soft_breach and not blocked) else 1.0

    return PortfolioRiskState(
        equity=equity,
        high_water_mark=hwm,
        drawdown_pct=drawdown_pct,
        consecutive_losses=consecutive_losses,
        alloc_multiplier=alloc_multiplier,
        blocked=blocked,
        blocked_reason=blocked_reason,
        newly_triggered_soft=newly_triggered_soft,
        newly_triggered_hard=newly_triggered_hard,
    )


def format_soft_drawdown_alert(state: PortfolioRiskState) -> str:
    return "\n".join([
        "🧪 SIMULATION — coupe-circuit portefeuille (palier SOUPLE)",
        f"Drawdown {state.drawdown_pct:.1%} depuis le plus haut d'équité ({state.high_water_mark:,.0f} $).",
        f"Allocation des NOUVELLES entrées réduite de moitié (×{SOFT_ALLOC_MULTIPLIER}) jusqu'à résorption.",
        "Positions déjà ouvertes : gérées normalement (stop suiveur/prise de profit).",
        "Aucun argent réel.",
    ])


def format_hard_circuit_breaker_alert(state: PortfolioRiskState) -> str:
    return "\n".join([
        "🧪 SIMULATION — coupe-circuit portefeuille (palier DUR)",
        f"{state.blocked_reason or 'seuil de risque franchi'}.",
        "Toute NOUVELLE position paper est bloquée jusqu'à reprise manuelle explicite.",
        "Positions déjà ouvertes : gérées normalement (stop suiveur/prise de profit) — aucune n'est fermée de force.",
        "Reprise : action humaine explicite requise, jamais automatique.",
        "Aucun argent réel.",
    ])
