"""Bitcoin cycle analysis (accumulation / markup / distribution / markdown) --
the long-term macro foundation ARIA reads before judging a Base token in
context.

Facts-only: cycle boundaries (halving dates) are a public, verifiable fact
(Bitcoin protocol, immutable), never an opinion. Per-phase statistics (low/
high price, %, duration) are computed from the REAL fetched price series
(CoinGecko), never fabricated — `segment_cycles` is a pure function, no
network, testable.

Only the phase LABEL (accumulation/markup/distribution/markdown) is an
analytical LENS: a widely-used reading framework (the 4-year cycle theory
tied to halvings), not a proven market law. The accumulation-exit threshold
(+30% from the low) and the distribution band (10% below the high) are
SIMPLE, DECLARED heuristics here, not an official boundary (none exists).
The qualitative narrative per cycle comes from an LLM, but always anchored
on the real numbers computed here — never a fabricated truth (same doctrine
as `exam.py` for contested frameworks: say so explicitly rather than
presenting it as proven).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

BTC_COIN_ID = "bitcoin"

# Bitcoin halving dates — public fact, immutable protocol (not an estimate).
HALVING_DATES: tuple[tuple[str, str], ...] = (
    ("cycle halving 2016->2020", "2016-07-09"),
    ("cycle halving 2020->2024", "2020-05-11"),
    ("cycle halving 2024->en cours", "2024-04-20"),
)

# Margin before the first halving kept, to capture the low that precedes it.
HISTORY_START = "2015-06-01"

# SIMPLE, DECLARED heuristics (no official boundary exists):
ACCUMULATION_EXIT_GAIN = 0.30  # accumulation exit: first price is +30% off the cycle low
DISTRIBUTION_BAND = 0.10       # distribution: price is within 10% of the cycle high


def _to_ts(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _iso(ms_ts: int) -> str:
    return datetime.fromtimestamp(ms_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class CyclePhase:
    label: str
    start_date: str
    end_date: str
    start_price: float
    end_price: float
    change_pct: float


@dataclass
class CycleStats:
    name: str
    window_start: str
    window_end: str
    low_price: float
    low_date: str
    high_price: float
    high_date: str
    gain_low_to_high_pct: float
    drawdown_high_to_end_pct: float
    phases: list[CyclePhase] = field(default_factory=list)


def segment_cycles(prices: list[tuple[int, float]]) -> list[CycleStats]:
    """Splits the real series into cycles anchored on halvings, computes the
    real statistics (low/high/%, durations). PURE function: no network call,
    no fabricated value — only min/max/percentages on the supplied prices."""
    if len(prices) < 2:
        return []
    prices = sorted(prices, key=lambda x: x[0])
    last_ts_ms = prices[-1][0]

    boundaries = [(_to_ts(d), name) for name, d in HALVING_DATES]
    windows: list[tuple[str, int, int]] = []
    for i, (start_ts, name) in enumerate(boundaries):
        end_ts = boundaries[i + 1][0] if i + 1 < len(boundaries) else int(last_ts_ms / 1000.0) + 1
        windows.append((name, start_ts, end_ts))

    stats: list[CycleStats] = []
    for name, start_ts, end_ts in windows:
        window = [(t, p) for t, p in prices if start_ts * 1000 <= t <= end_ts * 1000]
        if len(window) < 2:
            continue
        low_t, low_p = min(window, key=lambda x: x[1])
        high_t, high_p = max(window, key=lambda x: x[1])
        end_p = window[-1][1]
        gain = (high_p / low_p - 1.0) * 100.0 if low_p else 0.0
        drawdown = (end_p / high_p - 1.0) * 100.0 if high_p else 0.0

        phases: list[CyclePhase] = []

        # Accumulation: from the low up to the first price >= +ACCUMULATION_EXIT_GAIN.
        after_low = [(t, p) for t, p in window if t >= low_t]
        acc_end = next(
            ((t, p) for t, p in after_low if p >= low_p * (1 + ACCUMULATION_EXIT_GAIN)), None,
        )
        markup_start = (low_t, low_p)
        if acc_end and acc_end[0] > low_t:
            phases.append(CyclePhase(
                label="accumulation", start_date=_iso(low_t), end_date=_iso(acc_end[0]),
                start_price=low_p, end_price=acc_end[1],
                change_pct=(acc_end[1] / low_p - 1.0) * 100.0 if low_p else 0.0,
            ))
            markup_start = acc_end

        # Markup: from the end of accumulation up to the cycle high.
        if high_t > markup_start[0]:
            phases.append(CyclePhase(
                label="hausse (markup)", start_date=_iso(markup_start[0]), end_date=_iso(high_t),
                start_price=markup_start[1], end_price=high_p,
                change_pct=(high_p / markup_start[1] - 1.0) * 100.0 if markup_start[1] else 0.0,
            ))

        # Distribution: band around the high (price >= high * (1 - DISTRIBUTION_BAND)).
        dist_window = [(t, p) for t, p in window if t >= high_t and p >= high_p * (1 - DISTRIBUTION_BAND)]
        markdown_start = (high_t, high_p)
        if len(dist_window) >= 2 and dist_window[-1][0] > dist_window[0][0]:
            phases.append(CyclePhase(
                label="distribution", start_date=_iso(dist_window[0][0]), end_date=_iso(dist_window[-1][0]),
                start_price=dist_window[0][1], end_price=dist_window[-1][1],
                change_pct=(dist_window[-1][1] / dist_window[0][1] - 1.0) * 100.0 if dist_window[0][1] else 0.0,
            ))
            markdown_start = dist_window[-1]

        # Markdown: from the end of distribution to the end of the window.
        if window[-1][0] > markdown_start[0]:
            phases.append(CyclePhase(
                label="baisse (markdown)", start_date=_iso(markdown_start[0]), end_date=_iso(window[-1][0]),
                start_price=markdown_start[1], end_price=end_p,
                change_pct=(end_p / markdown_start[1] - 1.0) * 100.0 if markdown_start[1] else 0.0,
            ))

        stats.append(CycleStats(
            name=name, window_start=_iso(window[0][0]), window_end=_iso(window[-1][0]),
            low_price=low_p, low_date=_iso(low_t), high_price=high_p, high_date=_iso(high_t),
            gain_low_to_high_pct=gain, drawdown_high_to_end_pct=drawdown, phases=phases,
        ))
    return stats


async def fetch_btc_history(*, client=None) -> list[tuple[int, float]] | None:
    """Real BTC/USD price series since HISTORY_START (Blockchain.com). None if
    unavailable — never a fabricated series.

    CoinGecko changed its policy (confirmed live on 09/07, `error_code
    10012`): its free tier now refuses any request on data older than 365
    days -- structurally incompatible with the 10+ years required here.
    Switched to `services/blockchain_info.py` (established company,
    documented public endpoint, ~1600 daily points 2009->today, no key).
    Recent RSI (`arena_signal.py`) stays on CoinGecko, which works very well
    for a short window (<=365 days).
    """
    if client is None:
        from aria_core.services.blockchain_info import blockchain_info_client as client

    result = await client.fetch_btc_market_price_history()
    if not result.available:
        return None

    start_ms = _to_ts(HISTORY_START) * 1000
    filtered = [(t, p) for t, p in result.prices if t >= start_ms]
    return filtered or None


def current_phase_summary(stats: list[CycleStats]) -> dict | None:
    """Current phase (last segment of the ongoing cycle) -- PURE, deterministic
    function, no network/LLM call. Serves as compact market context for every
    VC report (macro overlay, task #14); the full 3-cycle narrative remains
    reserved for /cycles."""
    if not stats:
        return None
    current_cycle = stats[-1]
    if not current_cycle.phases:
        return None
    phase = current_cycle.phases[-1]
    return {
        "label": phase.label,
        "since": phase.start_date,
        "change_pct": phase.change_pct,
        "cycle_name": current_cycle.name,
    }


_PHASE_CACHE_TTL_SECONDS = 3600  # the phase doesn't flip from one cycle to another in 1h --
# avoids redoing a CoinGecko round-trip on EVERY VC report (sobriety).
_phase_cache: dict = {"at": 0.0, "value": None}


async def fetch_current_macro_phase(*, client=None, force_refresh: bool = False) -> dict | None:
    """Compact entry point for the VC reports' macro overlay (task #14).
    Fail-closed AND graceful degradation: history unavailable -> returns the
    last known cached value if there is one, otherwise None (never a
    fabricated phase; the section is then simply omitted from the report)."""
    import time

    now = time.monotonic()
    if not force_refresh and _phase_cache["value"] is not None:
        if (now - _phase_cache["at"]) < _PHASE_CACHE_TTL_SECONDS:
            return _phase_cache["value"]

    prices = await fetch_btc_history(client=client)
    if not prices:
        return _phase_cache["value"]

    stats = segment_cycles(prices)
    result = current_phase_summary(stats)
    if result is not None:
        _phase_cache["at"] = now
        _phase_cache["value"] = result
    return result


_NARRATIVE_SYSTEM = (
    "Tu es ARIA, analyste macro. La théorie des cycles Bitcoin (accumulation/hausse/"
    "distribution/baisse, ancrés sur le halving) est un CADRE DE LECTURE répandu, pas "
    "une loi de marché prouvée — dis-le explicitement dans ta réponse. Explique chaque "
    "cycle SEULEMENT à partir des chiffres fournis, sans en inventer d'autres. Facture, "
    "nuancée, 3 à 5 phrases par cycle."
)


def _format_stats_for_llm(stats: list[CycleStats]) -> str:
    lines = []
    for c in stats:
        lines.append(
            f"{c.name} ({c.window_start} -> {c.window_end}) : bas {c.low_price:,.0f}$ le "
            f"{c.low_date}, haut {c.high_price:,.0f}$ le {c.high_date} "
            f"({c.gain_low_to_high_pct:+.0f}% bas->haut), variation haut->fin de fenêtre "
            f"{c.drawdown_high_to_end_pct:+.0f}%."
        )
        for p in c.phases:
            lines.append(
                f"  - {p.label} : {p.start_date} -> {p.end_date}, {p.start_price:,.0f}$ -> "
                f"{p.end_price:,.0f}$ ({p.change_pct:+.0f}%)"
            )
    return "\n".join(lines)


async def analyze_btc_cycles(*, client=None, llm=None) -> dict:
    """Analysis of the last 3 Bitcoin cycles: real computed numbers + LLM
    narrative anchored on them. Fail-closed: history unavailable -> no
    fabricated analysis."""
    prices = await fetch_btc_history(client=client)
    if not prices:
        return {"available": False, "error": "historique BTC indisponible (CoinGecko)"}

    stats = segment_cycles(prices)
    if not stats:
        return {"available": False, "error": "segmentation impossible (série insuffisante)"}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    prompt = (
        "Voici les statistiques réelles des 3 derniers cycles Bitcoin (bornés par les "
        "halvings) :\n\n" + _format_stats_for_llm(stats) +
        "\n\nExplique chaque cycle (accumulation, hausse, distribution, baisse) en te "
        "basant UNIQUEMENT sur ces chiffres."
    )
    narrative = await llm(prompt, _NARRATIVE_SYSTEM, max_tokens=900)

    return {
        "available": True,
        "cycles": stats,
        "narrative": narrative or "Récit qualitatif indisponible (LLM) — chiffres bruts ci-dessus uniquement.",
    }


def format_cycles_report(result: dict) -> str:
    if not result.get("available"):
        return f"Analyse des cycles Bitcoin indisponible : {result.get('error', '?')}"

    lines = ["📉📈 ARIA — 3 derniers cycles Bitcoin (halving à halving)", ""]
    for c in result["cycles"]:
        lines.append(f"• {c.name} : {c.window_start} → {c.window_end}")
        lines.append(
            f"  Bas {c.low_price:,.0f}$ ({c.low_date}) → Haut {c.high_price:,.0f}$ "
            f"({c.high_date}), {c.gain_low_to_high_pct:+.0f}%"
        )
    lines.append("")
    lines.append(result["narrative"])
    lines.append("")
    lines.append(
        "Cadre de lecture (accumulation/hausse/distribution/baisse) : un modèle répandu, "
        "pas une loi de marché prouvée."
    )
    return "\n".join(lines)
