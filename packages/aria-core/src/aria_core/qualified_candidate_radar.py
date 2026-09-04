"""ARIA RADAR V1 -- observation-only Telegram alert on discovery qualification.

03/09, operator go (Chantier C, distinct from the trading pipeline itself):
fires the moment a candidate passes on-chain discovery qualification
(``onchain_pool_discovery.check_candidates()``), BEFORE the regime gate --
see ``shadow_persistent.py``'s ``robinhood_discovery_loop`` for the call
site. Deliberately upstream of any trading decision: the regime gate is a
TRADING gate, not a discovery sensor, so gating the radar on it would
throw away exactly the timing data this alert exists to capture (how many
candidates appear, which ones later matter, how long from detection to
regime opening).

Format frozen by the operator verbatim -- do not restyle without a new
explicit go. The one hard rule behind every field below: never render a
number the pipeline didn't actually measure. Day-zero discovery
(``onchain_pool_discovery.py``) never produces a real ``price_change_pct``
(it's always ``{}`` at this stage -- momentum/acceleration have no source
here), and no CHARTISTE/SOCIAL score exists at all until the Fusion Engine
(Chantier B) is built. Both render an explicit ``N/A``, never a blank or a
fabricated 0. Buy/Sell and Volume ARE real here (``onchain_activity_
observation.py``'s WS snapshot, bricks 1-3 of the on-chain sensors roadmap,
already wired into ``TrendingPool`` by ``onchain_pool_discovery.py`` at
qualification time) -- rendered when present, ``N/A`` when the snapshot
was never available for that candidate.

Pure text formatting, no I/O, no network call -- the caller owns sending
(``telegram_notify.send``, same bot/chat as every other shadow notification,
never a parallel system).
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.services.geckoterminal import TrendingPool
from aria_core.services.goplus import TokenSecurity

_NA = "N/A — non calculé"
_SEP = "━━━━━━━━━━━━━━━━"

# 03/09, operator go -- PROVISIONAL DISPLAY THRESHOLD, not a scientifically
# calibrated one (same "recalibrate on real closures" doctrine as every
# other threshold in this pipeline, e.g. MIN_LIQUIDITY_USD_DAY_ZERO).
# Purely factual: renders the RAW buy/sell counts and volumes, never a
# causal/security conclusion ("wash trading"/"scam"/"rug" are explicitly
# banned from this text -- that verdict needs a real security brain, not
# yet built). Found live 03/09 on $R404 (41 buys/0 sells), independently
# flagged "Potential scam" by fomo.io the same minute.
_FLOW_IMBALANCE_MIN_BUYS = 10

# 03/09, operator go -- Telegram ELIGIBILITY threshold, deliberately
# DISTINCT from and never touching onchain_pool_discovery's own
# MIN_LIQUIDITY_USD_DAY_ZERO ($200, discovery/logging qualification --
# unchanged). A candidate below this line is still discovered, logged, and
# fed into record_signals/regime tracking exactly as before; it simply
# never reaches this alert. Same "provisional, recalibrate on real
# closures" doctrine as every other threshold in this pipeline -- operator
# rationale: "$20k n'est pas zero risque, ca reduit un risque precis
# (illiquidite/impact de sortie)", not a security guarantee.
RADAR_ELIGIBLE_LIQUIDITY_USD = 20_000.0


def is_radar_eligible(pool: TrendingPool, *, threshold_usd: float = RADAR_ELIGIBLE_LIQUIDITY_USD) -> bool:
    """Fail-closed, same doctrine as the rest of this pipeline: an unknown
    reserve is never treated as notify-worthy."""
    return pool.reserve_usd is not None and pool.reserve_usd >= threshold_usd


def is_security_blocked(security: TokenSecurity) -> bool:
    """03/09, operator go -- minimal GoPlus honeypot gate, confirmed live to
    cover Robinhood Chain (chain_id 4663, real call on the exact $R404 pool
    fomo.io independently flagged "Potential scam" the same minute).

    Deliberately narrow scope: only the 3 "can I resell at all" flags
    (is_honeypot/cannot_sell_all/cannot_buy) -- the single least-ambiguous
    signal GoPlus exposes, per its own module docstring ("the most
    important: is resale possible at all?"). FAIL-OPEN on unknown, same
    doctrine as goplus.py itself: a None flag (network outage, no data,
    GoPlus never responded) never blocks -- only a POSITIVELY confirmed
    signal does. This is NOT the operator's full "Security Gate" vision
    (liquidity stability, contract anomalies, etc.) -- that remains a
    future, separately-scoped chantier."""
    if not security.available:
        return False
    return bool(security.is_honeypot) or bool(security.cannot_sell_all) or bool(security.cannot_buy)


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}" if value >= 1 else f"${value:.6f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:.6f}" if value < 1 else f"${value:,.4f}"


def _fmt_supply(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.0f}"


def _fmt_age(pool_created_at: datetime | None, now: datetime) -> str:
    if pool_created_at is None:
        return "N/A"
    seconds = (now - pool_created_at).total_seconds()
    if seconds < 60:
        return f"{seconds:.0f} sec"
    return f"{seconds / 60.0:.1f} min"


def _fmt_pair_usd(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "N/A"
    return f"${a:,.2f} / ${b:,.2f}"


# 04/09, operator go -- see format_candidate_alert's own docstring.
_CHAIN_ABBREV = {"robinhood": "RH", "base": "BASE", "solana": "SOL"}


def _fmt_usd_compact(value: float | None) -> str:
    if value is None:
        return "N/A"
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if magnitude >= 1_000:
        return f"${value / 1_000:.1f}k"
    return _fmt_usd(value)


def _fmt_age_compact(pool_created_at: datetime | None, now: datetime) -> str:
    if pool_created_at is None:
        return "N/A"
    seconds = (now - pool_created_at).total_seconds()
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60.0:.1f}min"


def format_candidate_alert(
    pool: TrendingPool, *, chain: str, regime_open: bool, security_status: str,
    now: datetime | None = None,
) -> str:
    """A short (5-8 line) human summary for the "Candidate" state --
    04/09, operator go, twice-revised the SAME day as
    ``format_qualified_candidate`` (RADAR V1's original 40-line dump,
    which stays available for a future, richer alert). Operator's own
    words on why this exists: "Telegram ne doit afficher que:
    Qu'est-ce qu'ARIA vient de faire / pense / observe ?" -- Telegram is a
    HUMAN SUMMARY SCREEN, not a data dump. The full data this function
    compresses is never lost: it stays in ``early_life_tracking``/
    ``onchain_activity_observation_log`` for ARIA's own reasoning, which
    always sees the complete, unformatted history.

    Same "never fabricate a signal the pipeline doesn't measure" doctrine
    as ``format_qualified_candidate``: every field renders an honest
    ``N/A`` when unknown, never a blank/zero/invented number.
    ``acceleration`` is hardcoded ``N/A`` because nothing in the pipeline
    computes it yet (same as that function's own Acceleration field) --
    never silently dropped, so a reader always sees explicitly that this
    dimension is unmeasured, not merely absent.

    ``security_status`` is passed in rather than re-derived here (the
    caller already ran the retry/block state machine in
    ``early_life_observation.py`` -- this function only renders "PASS" for
    ``"safe"``, never re-decides safety) -- deliberately never called with
    anything else in production (a candidate only reaches this alert once
    ``security_status == "safe"``), but rendered honestly (uppercased
    as-is) rather than assumed if it somehow were."""
    at = now or datetime.now(timezone.utc)
    symbol = pool.symbol or "?"
    chain_label = _CHAIN_ABBREV.get(chain, chain.upper())
    age = _fmt_age_compact(pool.pool_created_at, at)
    liquidity = _fmt_usd_compact(pool.reserve_usd)
    buy_sell = (
        f"{pool.buy_count}/{pool.sell_count}"
        if pool.buy_count is not None and pool.sell_count is not None
        else "N/A"
    )
    volume = _fmt_usd(pool.volume_usd)
    regime_label = "OPEN" if regime_open else "CLOSED"
    security_label = "PASS" if security_status == "safe" else security_status.upper()

    dexscreener_link = f"https://dexscreener.com/{chain}/{pool.pool_address}"
    fomo_link = f"https://fomo.family/tokens/{chain}/{pool.token_address or ''}"

    return (
        f"🚨 ARIA — ${symbol}\n"
        f"⛓️ {chain_label} · age {age} · liq {liquidity} · B/S {buy_sell}\n"
        f"📈 Volume {volume} · acceleration N/A\n"
        f"🛡️ Security {security_label}\n"
        f"⚠️ Day-zero · regime {regime_label}\n"
        f"👁️ Observation only\n"
        f"🔗 {dexscreener_link} · {fomo_link}"
    )


def format_qualified_candidate(
    pool: TrendingPool, *, chain: str, regime_open: bool, liquidity_floor_usd: float,
    now: datetime | None = None,
) -> str:
    """Render the ARIA RADAR V1 text for one just-qualified candidate.

    ``regime_open`` reflects the chain-level regime gate's CURRENT state --
    informational only, never gates whether this function is called (the
    caller decides that; see this module's own docstring).

    03/09, operator go -- Market Cap/Supply added (real ``totalSupply()``
    eth_call, same pattern/budget as ``symbol()``, computed in
    ``onchain_pool_discovery.py`` and never re-derived here). Display only,
    operator-explicit: "Ne surtout pas utiliser le mcap pour filtrer" --
    this alert must never claim mcap is a qualification criterion. Volume
    now renders the doppler-converted USD figure (``pool.volume_usd``),
    never the raw quote-unit number, which SafePons' first real radar
    showed as an unreadable "3e-06 (quote units)"."""
    at = now or datetime.now(timezone.utc)
    symbol = pool.symbol or "?"
    candidate_id = f"{chain}:{pool.pool_address}"

    buy_sell = (
        f"{pool.buy_count}/{pool.sell_count}"
        if pool.buy_count is not None and pool.sell_count is not None
        else "N/A"
    )
    volume = _fmt_usd(pool.volume_usd)
    pool_age = _fmt_age(pool.pool_created_at, at)
    buy_sell_usd = _fmt_pair_usd(pool.buy_volume_usd, pool.sell_volume_usd)
    swaps = str(pool.swap_count) if pool.swap_count is not None else "N/A"
    regime_label = "OPEN" if regime_open else "CLOSED"

    dexscreener_link = f"https://dexscreener.com/{chain}/{pool.pool_address}"
    fomo_link = f"https://fomo.family/tokens/{chain}/{pool.token_address or ''}"

    why_now = ["• Candidat vient de passer la qualification (on-chain, day-zero)"]
    if pool.reserve_usd is not None:
        why_now.append(
            f"• Liquidité initiale {_fmt_usd(pool.reserve_usd)} "
            f"(plancher {_fmt_usd(liquidity_floor_usd)})"
        )
    if pool.buy_count is not None and pool.sell_count is not None:
        why_now.append(f"• Buy/Sell observé: {buy_sell}")

    risks = []
    if (
        pool.buy_count is not None and pool.sell_count is not None
        and pool.sell_count == 0 and pool.buy_count >= _FLOW_IMBALANCE_MIN_BUYS
    ):
        risks.append(
            f"⚠️ FLOW IMBALANCE\n"
            f"{pool.buy_count} buys / {pool.sell_count} sells\n"
            f"Buy volume: {_fmt_usd(pool.buy_volume_usd)}\n"
            f"Sell volume: {_fmt_usd(pool.sell_volume_usd)}"
        )
    risks += [
        "• Poche day-zero — pas de garantie de profondeur au-delà de la liquidité observée",
        "• Aucun score CHARTISTE/SOCIAL à ce stade (Fusion Engine non construit)",
        f"• Régime: {regime_label}",
    ]

    return f"""🚨 ARIA RADAR V1 — ${symbol}

STATUS: QUALIFIED
Chain: {chain}
Candidate: {candidate_id}
Observed: {at.strftime("%H:%M:%S")}

{_SEP}
⛓️ ON-CHAIN
{_SEP}

Prix:           {_fmt_price(pool.price_usd)}
Pool age:       {pool_age}
Momentum:       {_NA} (day-zero, pas de fenêtre de variation mesurée)
Liquidity:      {_fmt_usd(pool.reserve_usd)}
Market Cap:     {_fmt_usd(pool.market_cap_usd)}
Supply:         {_fmt_supply(pool.total_supply)}
Swaps:          {swaps}
Buy/Sell:       {buy_sell}
Buy/Sell $:     {buy_sell_usd}
Volume:         {volume}
Acceleration:   {_NA} (dérive du Momentum, non disponible)

{_SEP}
🧠 OTHER SIGNALS
{_SEP}

CHARTISTE: {_NA}
SOCIAL:    {_NA}

{_SEP}
🎯 POURQUOI MAINTENANT ?
{_SEP}

{chr(10).join(why_now)}

{_SEP}
⚠️ RISQUES
{_SEP}

{chr(10).join(risks)}

{_SEP}
🔗 LIENS
{_SEP}

DexScreener: {dexscreener_link}
fomo.family: {fomo_link}

{_SEP}
📊 DÉCISION
{_SEP}

QUALIFIED — OBSERVATION ONLY"""
