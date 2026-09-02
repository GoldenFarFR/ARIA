"""Telegram cards for the prospective shadow pipeline -- three fixed moments.

Why this module (02/09, operator-directed): *"il me faudrait une notif propre
et jolie agreable visuellement avec toutes les infos comme coral"*. The point
is not decoration -- it is that a card read three days later must let you
reconstruct exactly what ARIA knew at T0 and why it would have acted, without
opening a database.

**The immutability rule, and it is the whole point.** The detection card is
built ONCE, from the T0 snapshot, and is never rewritten with what happened
afterwards. Progress and outcome arrive as SEPARATE messages. A card silently
updated with hindsight would turn the journal into a story told backwards,
which is the exact failure this pipeline exists to avoid.

**Width is a hard constraint, not a style choice.** Telegram on mobile shows
about 30-32 monospace characters before wrapping, and a wrapped table is worse
than no table. Every line here is built to fit ``CARD_WIDTH``; the box-drawing
frames that look good in a terminal (45+ chars) break on a phone, so the
hierarchy comes from emoji section heads and aligned columns instead.

**Nothing on a card is typed by hand.** Every value is read from the snapshot
dict, and a missing value renders as ``n/a`` rather than being omitted or
invented -- an absent social signal must LOOK absent, not look like a zero.
That is the same rule as the observation layer's four unavailability reasons.

**No aggregate score.** The bars show four dimensions side by side precisely so
they stay separate: a single number would merge on-chain, social and structure
into something that hides which one carried the decision -- and the operator's
two sketches (slow base vs fast stampede) showed the same metric can mean
opposite things across regimes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Telegram mobile wraps monospace past ~32 chars. Measured constraint, not a
# preference: a wrapped column table is unreadable, and this card's entire
# value is that numbers line up.
CARD_WIDTH = 32
LABEL_WIDTH = 16

KIND_DETECTION = "detection"
KIND_SHADOW_BUY = "shadow_buy"
KIND_CLOSED = "closed"

_HEADERS = {
    KIND_DETECTION: ("🟡", "DETECTION"),
    KIND_SHADOW_BUY: ("🟢", "SHADOW BUY"),
    KIND_CLOSED: ("🔴", "CLOSED"),
}


def _esc(text: object) -> str:
    """Telegram HTML escaping. Token symbols are attacker-controlled strings --
    a token literally named ``<b>`` must not be able to style the card, let
    alone break its markup."""
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _row(label: str, value: object, *, width: int = LABEL_WIDTH) -> str:
    """One aligned ``label ........ value`` line, right-justified value.

    The TOTAL line is capped at ``CARD_WIDTH``: the label is truncated before
    the value, because a clipped label stays guessable while a clipped number
    is a lie. Measured why this matters: a first version padded from a fixed
    label width and produced 47-char lines, which wrap on a phone and destroy
    the column alignment the card exists for.

    ``None`` renders as ``n/a`` on purpose: a value we do not have must look
    missing, never like a zero or an empty string that reads as "fine".
    """
    shown = "n/a" if value is None else str(value)
    room = CARD_WIDTH - len(shown) - 1
    if room < 3:                      # value alone fills the line
        return shown[:CARD_WIDTH]
    lab = label if len(label) <= room else label[: room - 1] + "…"
    return f"{lab}{' ' * (CARD_WIDTH - len(lab) - len(shown))}{shown}"


def _bar(value: float | None, *, slots: int = 10) -> str:
    """A 0-1 value as a filled bar. Unknown renders as dashes, never as empty
    (an empty bar reads as "measured, and it is zero")."""
    if value is None:
        return "─" * slots + "   n/a"
    v = max(0.0, min(1.0, float(value)))
    filled = round(v * slots)
    return "█" * filled + "░" * (slots - filled) + f"  {v * 100:3.0f}"


def _price(value: float | None) -> str:
    """Prices here span 12 orders of magnitude between tokens, so a fixed
    number of decimals is useless -- significant digits are what matters."""
    if value is None:
        return "n/a"
    if value >= 1:
        return f"${value:,.4f}"
    # Sub-dollar prices span many orders of magnitude between tokens, so a
    # fixed decimal count is useless. Compact scientific form keeps the line
    # inside CARD_WIDTH where "$0.00000012900" would not.
    return f"${value:.2e}".replace("e-0", "e-")


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"


def _usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    for unit, div in (("M", 1e6), ("K", 1e3)):
        if abs(value) >= div:
            return f"${value / div:,.1f}{unit}"
    return f"${value:,.0f}"


def _hms(ts: int | None) -> str:
    if ts is None:
        return "n/a"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def _age(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m}min"


@dataclass
class CardSections:
    """Rendered blocks, exposed separately so a caller can drop one it has no
    data for rather than print a section full of ``n/a``."""

    header: str
    entry: str | None = None
    structure: str | None = None
    onchain: str | None = None
    social: str | None = None
    execution: str | None = None
    bars: str | None = None
    why: str | None = None
    footer: str = ""


def _header(kind: str, snap: dict) -> str:
    icon, title = _HEADERS.get(kind, ("⚪", "CASE"))
    symbol = _esc(snap.get("symbol") or "?")
    chain = _esc(snap.get("chain") or "?")
    dex = _esc(snap.get("dex_family") or "")
    line2 = f"${symbol} · {chain}" + (f" · {dex}" if dex else "")
    detected = _hms(snap.get("t_detect"))
    line3 = f"détecté {detected} · âge {_age(snap.get('token_age_s'))}"
    case_id = snap.get("case_id")
    tag = f"  #{case_id}" if case_id else ""
    return f"<b>{icon} {title}{tag}</b>\n<code>{_esc(line2)}\n{_esc(line3)}</code>"


def _entry_block(snap: dict) -> str | None:
    entry = snap.get("entry_price")
    if entry is None:
        return None
    lines = ["🎯 <b>ENTRÉE</b>", "<code>" + _row("prix", _price(entry))]
    lo, hi = snap.get("entry_zone_low"), snap.get("entry_zone_high")
    if lo is not None and hi is not None:
        lines.append(_row("zone", f"{_price(lo)}–{_price(hi)}"))
    for i, tp in enumerate(snap.get("targets") or [], start=1):
        pct = (tp / entry - 1.0) if entry else None
        lines.append(_row(f"TP{i}  {_pct(pct)}", _price(tp)))
    inval = snap.get("invalidation_price")
    if inval is not None:
        pct = (inval / entry - 1.0) if entry else None
        lines.append(_row(f"inval {_pct(pct)}", _price(inval)))
    rr = snap.get("risk_reward")
    if rr is not None:
        lines.append(_row("R:R", f"{rr:.2f}"))
    return "\n".join(lines) + "</code>"


def _structure_block(snap: dict) -> str | None:
    st = snap.get("structure") or {}
    if not st:
        return None
    lines = ["📐 <b>STRUCTURE</b>", "<code>"]
    tmpl, match = st.get("template"), st.get("match")
    if tmpl:
        lines.append(_row("template " + _esc(tmpl), _pct(match, signed=False)))
    lines.append(_row("phase", _esc(st.get("phase")) if st.get("phase") else None))
    lines.append(_row("drawdown", _pct(st.get("drawdown_from_peak"))))
    ar = st.get("activity_relative")
    lines.append(_row("activité rel.", f"{ar:.2f}" if ar is not None else None))
    return "\n".join(lines) + "</code>"


def _onchain_block(snap: dict) -> str | None:
    oc = snap.get("onchain") or {}
    if not oc:
        return None
    bs = oc.get("buy_sell_ratio")
    spm = oc.get("swaps_per_min")
    lines = [
        "⛓ <b>ON-CHAIN</b>", "<code>",
        _row("buy / sell", f"{bs:.2f}" if bs is not None else None),
        _row("swaps/min", f"{spm:.0f}" if spm is not None else None),
        _row("wallets actifs", oc.get("active_wallets")),
        _row("nouveaux", oc.get("new_wallets")),
        _row("renouvellement", f"{oc['renewal_ratio']:.2f}" if oc.get("renewal_ratio") is not None else None),
        _row("liquidité", _usd(oc.get("liquidity_usd"))),
    ]
    return "\n".join(lines) + "</code>"


def _social_block(snap: dict) -> str | None:
    so = snap.get("social") or {}
    # Rendered even when empty: an absent social signal is information, and
    # hiding the section would make "not measured" indistinguishable from
    # "measured and neutral".
    status = so.get("status")
    lines = ["🌐 <b>SOCIAL</b>", "<code>", _row("signal", _esc(status) if status else None)]
    if so.get("sources") is not None:
        lines.append(_row("sources", so.get("sources")))
    if so.get("last_activity"):
        lines.append(_row("dernière activité", _esc(so["last_activity"])))
    return "\n".join(lines) + "</code>"


def _execution_block(snap: dict) -> str | None:
    ex = snap.get("execution") or {}
    if not ex:
        return None
    lines = [
        "⚡ <b>EXÉCUTION</b>", "<code>",
        _row("slippage est.", _pct(ex.get("slippage_est"), signed=False)),
        _row("risque", _esc(ex.get("risk")) if ex.get("risk") else None),
    ]
    if ex.get("max_size_usd") is not None:
        lines.append(_row("taille max", _usd(ex["max_size_usd"])))
    return "\n".join(lines) + "</code>"


def _bars_block(snap: dict) -> str | None:
    bars = snap.get("bars") or {}
    if not bars:
        return None
    lines = ["<code>"]
    for label, key in (("onchain", "onchain"), ("social", "social"),
                       ("structure", "structure"), ("execution", "execution")):
        lines.append(f"{label:<10}{_bar(bars.get(key))}")
    return "\n".join(lines) + "</code>"


def _why_block(snap: dict) -> str | None:
    """The section the operator said he would read most. Capped at four:
    beyond that it stops being a reason and becomes a dump."""
    reasons = [r for r in (snap.get("why_now") or []) if r][:4]
    if not reasons:
        return None
    # Truncated to CARD_WIDTH like every other line: a reason that wraps costs
    # more legibility than the two words it would have added.
    body = "\n".join(f"✓ {_esc(str(r)[: CARD_WIDTH - 2])}" for r in reasons)
    return f"<b>WHY NOW</b>\n<code>{body}</code>"


def render_card(kind: str, snap: dict) -> str:
    """The full card for one moment. Pure function of the snapshot.

    Deliberately takes a plain dict rather than a dataclass: the snapshot is
    persisted as JSON, and rendering must read exactly what was STORED at T0 --
    not a live object that could have been mutated since.
    """
    sections = [_header(kind, snap)]
    for block in (
        _entry_block(snap), _structure_block(snap), _onchain_block(snap),
        _social_block(snap), _execution_block(snap), _bars_block(snap),
        _why_block(snap),
    ):
        if block:
            sections.append(block)

    # Footer on its own lines rather than joined: the two bits together ran to
    # 47 characters, which wraps on mobile and breaks the card's last line.
    footer_bits = []
    if kind != KIND_CLOSED:
        footer_bits.append("SHADOW · aucune exécution")
    stamp = snap.get("t_card") or snap.get("t_detect")
    if stamp:
        footer_bits.append(f"{_hms(stamp)} UTC")
    if footer_bits:
        sections.append("<code>" + _esc("\n".join(footer_bits)) + "</code>")
    return "\n\n".join(sections)


def render_progress(case_id: str | int, points: list[tuple[int, float]],
                    *, mfe: float | None = None, mae: float | None = None) -> str:
    """Follow-up message -- a SEPARATE message, never an edit of the card.

    Editing the original with later prices is exactly how a journal starts
    telling the story backwards; the operator's rule is that T0 is written once.
    """
    lines = [f"<b>📈 SUIVI #{_esc(case_id)}</b>", "<code>"]
    for ts, pct in points:
        lines.append(f"{_hms(ts):<10}{_pct(pct):>10}")
    if mfe is not None or mae is not None:
        lines.append("")
        lines.append(_row("MFE", _pct(mfe)))
        lines.append(_row("MAE", _pct(mae)))
    return "\n".join(lines) + "</code>"


def render_rejection(snap: dict) -> str:
    """A rejected candidate, with the reason -- the operator's point 2.

    Without these, we could never tell whether a filter removes noise or
    removes winners. The reason code is what makes that measurable later.
    """
    symbol = _esc(snap.get("symbol") or "?")
    reason = _esc(snap.get("reject_reason") or "unspecified")
    lines = [
        f"<b>⚪ REJETÉ</b>  ${symbol}",
        "<code>" + _row("raison", reason),
    ]
    layer = snap.get("reject_layer")
    if layer:
        lines.append(_row("couche", _esc(layer)))
    st = snap.get("structure") or {}
    if st.get("drawdown_from_peak") is not None:
        lines.append(_row("drawdown", _pct(st["drawdown_from_peak"])))
    if st.get("activity_relative") is not None:
        lines.append(_row("activité rel.", f"{st['activity_relative']:.2f}"))
    lines.append(_row("détecté", _hms(snap.get("t_detect"))))
    return "\n".join(lines) + "</code>"
