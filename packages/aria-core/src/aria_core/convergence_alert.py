"""Telegram text for a brain_correlation 2/3 or 3/3 crossing.

03/09, operator go -- companion to ``brain_correlation.py``. Pure text
formatting, no I/O: the caller owns sending (``telegram_notify.send``, same
bot/chat as every other shadow notification -- never a parallel system,
same doctrine as ``qualified_candidate_radar.py``).

Deliberately not a trade verdict. This alert exists to prove the three
brains can meet temporally -- deriving an actual entry decision from a
converged state is the Fusion Engine's job, explicitly not built yet."""
from __future__ import annotations

from aria_core.brain_correlation import BRAINS

_LABELS = {"on_chain": "ON-CHAIN", "social": "SOCIAL", "chart": "CHART"}


def _local_hms(iso_ts: str) -> str:
    return iso_ts[11:19] if len(iso_ts) >= 19 else iso_ts


def format_convergence_alert(symbol: str | None, *, chain: str, state: dict) -> str:
    """``state`` is a ``brain_correlation.correlation_state()`` result.
    Renders every brain in ``BRAINS`` order: positive+valid ones get their
    real timestamp, everything else renders "en attente" -- never a
    fabricated red/negative for a brain that simply hasn't fired yet."""
    count = state["count"]
    header = "🔥 CONVERGENCE 3/3" if count >= 3 else "🚨 CORRELATION 2/3" if count == 2 else f"⚪ {state['level']}"

    lines = []
    for brain in BRAINS:
        label = _LABELS[brain]
        if brain in state["brains_positive"]:
            ts = _local_hms(state["observed_at"][brain])
            lines.append(f"{label} 🟢  ({ts})")
        else:
            lines.append(f"{label} ⚪ en attente")

    return f"""{header} — ${symbol or "?"}
Chain: {chain}

{chr(10).join(lines)}

Statut: correlation temporelle uniquement -- aucune decision de trade."""
