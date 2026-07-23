"""Professional Telegram formatting — investor-grade, plain text (no markdown)."""

from __future__ import annotations

import re

AXIS_META_FR: dict[str, tuple[str, str]] = {
    "volume": ("📈", "Volume — frais & flux"),
    "builders": ("🛠", "Builders — SDK & API"),
    "community": ("👥", "Communauté — détention & rétention"),
    "exposure": ("📣", "Exposition — visibilité LT"),
    "holding_fit": ("🎯", "Fit holding — utility CAO"),
}

AXIS_META_EN: dict[str, tuple[str, str]] = {
    "volume": ("📈", "Volume — fees & flow"),
    "builders": ("🛠", "Builders — SDK & API"),
    "community": ("👥", "Community — holders & retention"),
    "exposure": ("📣", "Exposure — long-term visibility"),
    "holding_fit": ("🎯", "Holding fit — CAO utility"),
}

AXIS_SHORT_FR: dict[str, str] = {
    "volume": "Vol",
    "builders": "Dev",
    "community": "Com",
    "exposure": "Exp",
    "holding_fit": "Fit",
}

AXIS_SHORT_EN: dict[str, str] = {
    "volume": "Vol",
    "builders": "Bld",
    "community": "Com",
    "exposure": "Exp",
    "holding_fit": "Fit",
}

RANK_MEDALS = ("🥇", "🥈", "🥉", "4.", "5.")

# Fixed widths — readable on Telegram (proportional font)
_W_AXIS = 4
_W_SCORE = 3
_W_BAR = 8
_W_NAME = 15
_W_COMP = 5
_W_SUB = 3


def score_bar(score: int, width: int = _W_BAR) -> str:
    score = max(0, min(100, int(score)))
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _divider(width: int = 34) -> str:
    return "─" * width


def _fmt_score(value: float) -> str:
    """Prose display (FR comma if decimal)."""
    if abs(value - round(value)) <= 0.05:
        return str(int(round(value)))
    return f"{value:.1f}".replace(".", ",")


def _fmt_table_score(value: float) -> str:
    """Table: decimal point + fixed width for alignment."""
    return f"{value:5.1f}"


def _truncate_name(name: str, width: int = _W_NAME) -> str:
    if len(name) <= width:
        return name.ljust(width)
    return (name[: width - 1] + "…").ljust(width)


def format_axis_profile(
    lp,
    *,
    lang: str = "fr",
    holding_context: bool = True,
) -> list[str]:
    shorts = AXIS_SHORT_FR if lang == "fr" else AXIS_SHORT_EN
    keys = ["volume", "builders", "community", "exposure"]
    if holding_context:
        keys.append("holding_fit")

    header = "📊 Profil par axe (0–100)" if lang == "fr" else "📊 Axis profile (0–100)"
    col_hdr = (
        f"{'Axe':<{_W_AXIS}} {'Scr':>{_W_SCORE}}  {'Barre':<{_W_BAR}}"
        if lang == "fr"
        else f"{'Axe':<{_W_AXIS}} {'Scr':>{_W_SCORE}}  {'Bar':<{_W_BAR}}"
    )
    lines: list[str] = [header, _divider(28), col_hdr]
    for key in keys:
        val = getattr(lp, key)
        short = shorts[key]
        lines.append(
            f"{short:<{_W_AXIS}} {val:>{_W_SCORE}}  {score_bar(val)}"
        )
    meta = AXIS_META_FR if lang == "fr" else AXIS_META_EN
    legend_parts = [f"{shorts[k]}={meta[k][1].split('—')[0].strip()}" for k in keys]
    legend = " · ".join(legend_parts)
    lines.extend(["", legend])
    return lines


def _table_header(*, lang: str, holding_context: bool) -> str:
    cols = f"{'V':>{_W_SUB}} {'B':>{_W_SUB}} {'C':>{_W_SUB}} {'E':>{_W_SUB}}"
    if holding_context:
        cols += f" {'F':>{_W_SUB}}"
    score_lbl = "Score"
    return (
        f"    {'Launchpad':<{_W_NAME}} {score_lbl:>{_W_COMP}}  {cols}"
    )


def _table_row(
    lp,
    composite: float,
    *,
    rank: str,
    holding_context: bool,
) -> str:
    cols = (
        f"{lp.volume:>{_W_SUB}} {lp.builders:>{_W_SUB}} "
        f"{lp.community:>{_W_SUB}} {lp.exposure:>{_W_SUB}}"
    )
    if holding_context:
        cols += f" {lp.holding_fit:>{_W_SUB}}"
    return (
        f"{rank:<2} {_truncate_name(lp.name)} {_fmt_table_score(composite)}  {cols}"
    )


def format_ranking_table(
    ranked: list[tuple],
    *,
    lang: str = "fr",
    holding_context: bool = True,
    limit: int = 5,
) -> list[str]:
    title = f"📋 CLASSEMENT — TOP {limit}" if lang == "fr" else f"📋 RANKING — TOP {limit}"
    legend = "V=Volume B=Builders C=Community E=Exposure"
    if lang == "fr":
        legend = "V=Volume B=Builders C=Communauté E=Exposition"
    if holding_context:
        legend += " F=Fit holding" if lang == "fr" else " F=Holding fit"

    lines = [
        _divider(),
        title,
        "",
        _table_header(lang=lang, holding_context=holding_context),
        _divider(36),
    ]
    for i, (lp, sc) in enumerate(ranked[:limit]):
        medal = RANK_MEDALS[i] if i < len(RANK_MEDALS) else f"{i + 1}."
        lines.append(_table_row(lp, sc, rank=medal, holding_context=holding_context))

    lines.extend(["", legend])
    return lines


def format_compare_table(
    launchpads: list,
    scores: list[float],
    *,
    lang: str = "fr",
    holding_context: bool = True,
) -> list[str]:
    """Aligned comparison table (compare mode)."""
    title = "⚖️ COMPARAISON" if lang == "fr" else "⚖️ COMPARISON"
    lines = [
        "══════════════════════════════════",
        title,
        "══════════════════════════════════",
        "",
        _table_header(lang=lang, holding_context=holding_context),
        _divider(36),
    ]
    for i, lp in enumerate(launchpads):
        sc = scores[i] if i < len(scores) else 0.0
        rank = str(i + 1) + "."
        lines.append(_table_row(lp, sc, rank=rank, holding_context=holding_context))
    legend = "V=Volume B=Builders C=Community E=Exposure"
    if lang == "fr":
        legend = "V=Volume B=Builders C=Communauté E=Exposition"
    if holding_context:
        legend += " F=Fit holding" if lang == "fr" else " F=Holding fit"
    lines.extend(["", legend])
    return lines


def _complete_sentence(text: str, max_chars: int = 260) -> str:
    import re

    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    chunk = text[:max_chars]
    for sep in (". ", "? ", "! ", "; "):
        idx = chunk.rfind(sep)
        if idx > 50:
            return chunk[: idx + 1].strip()
    idx = chunk.rfind(" ")
    if idx > 50:
        return chunk[:idx].strip() + "…"
    return chunk.strip() + "…"


def _dedupe_sources(sources: list) -> list:
    out: list = []
    seen: set[str] = set()
    for src in sources:
        text = getattr(src, "text", str(src))
        key = re.sub(r"\s+", " ", text.lower())[:90]
        if not key or key in seen:
            continue
        if any(key in s or s in key for s in seen):
            continue
        seen.add(key)
        out.append(src)
    return out


def _extract_price(texts: list[str]) -> str:
    import re

    for t in texts:
        m = re.search(r"\$[\d,]+(?:\.\d+)?|\d[\d\s,]*\s*(?:€|EUR|USD)", t, re.I)
        if m:
            return m.group(0).replace("  ", " ").strip()
    return ""


def _extract_percent(texts: list[str]) -> str:
    import re

    for t in texts:
        m = re.search(r"[+-]?\d+[,.]?\d*\s*%", t)
        if m:
            return m.group(0).strip()
    return ""


def _pick_best_snippet(texts: list[str], query: str) -> str:
    import re

    if not texts:
        return ""
    q_tokens = set(re.findall(r"[a-zàâçéèêëîïôùûü0-9]{4,}", query.lower()))
    best, best_score = texts[0], -1
    for t in texts:
        score = sum(1 for tok in q_tokens if tok in t.lower())
        if re.search(r"\d{1,2}h\d{2}|\d{1,2}:\d{2}", t, re.I):
            score += 4
        if re.search(r"\$[\d,]+|\d+\s*%", t):
            score += 2
        if score > best_score:
            best, best_score = t, score
    return best


def _extract_direct_answer(query: str, sources: list, lang: str) -> str:
    import re

    texts = [getattr(s, "text", str(s)) for s in sources if getattr(s, "text", str(s))]
    combined = " ".join(texts).lower()
    q = query.lower()

    if re.search(r"baisse|monte|hausse|descend|grimpe|up or down|going up|going down", q):
        down_kw = ("baisse", "chute", "en baisse", "descend", "perd", "correction", "drop", "fall", "down", "chute")
        up_kw = ("hausse", "monte", "en hausse", "augmente", "gagne", "rally", "rise", "up", "rebond")
        down = sum(1 for k in down_kw if k in combined)
        up = sum(1 for k in up_kw if k in combined)
        price = _extract_price(texts)
        pct = _extract_percent(texts)
        if down > up:
            if lang == "fr":
                ans = "Le Bitcoin est en baisse" if re.search(r"bitcoin|btc", q) else "Tendance à la baisse"
            else:
                ans = "Bitcoin is down" if re.search(r"bitcoin|btc", q) else "Trend is down"
        elif up > down:
            if lang == "fr":
                ans = "Le Bitcoin est en hausse" if re.search(r"bitcoin|btc", q) else "Tendance à la hausse"
            else:
                ans = "Bitcoin is up" if re.search(r"bitcoin|btc", q) else "Trend is up"
        else:
            ans = "Tendance peu claire dans les sources." if lang == "fr" else "Trend unclear in sources."
            return ans
        if price:
            ans += f" — environ {price}"
        if pct:
            ans += f" ({pct})"
        return ans + "."

    if re.search(r"heure|horaire|quand|when|what time", q):
        for t in texts:
            m = re.search(r"\d{1,2}h\d{2}|\d{1,2}:\d{2}", t, re.I)
            if m:
                return (
                    f"Selon les sources : coup d'envoi à {m.group(0)}."
                    if lang == "fr"
                    else f"Per sources: kick-off at {m.group(0)}."
                )

    best = _pick_best_snippet(texts, query)
    return _complete_sentence(best, 240) or (
        "Information non consolidée dans les sources." if lang == "fr" else "Could not consolidate from sources."
    )


def _normalize_sources(snippets: list) -> list:
    from aria_core.knowledge.web_verify import WebSource

    out: list[WebSource] = []
    for item in snippets:
        if isinstance(item, WebSource):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append(WebSource(text=item.strip()))
    return out


def _source_lines(src, lang: str) -> list[str]:
    text = getattr(src, "text", str(src))
    url = getattr(src, "url", "") or ""
    excerpt = _complete_sentence(text, 110)
    lines: list[str] = []
    if url:
        lines.append(f"📎 {url}")
    else:
        lines.append("📎 Source web")
    if excerpt:
        lines.append(f"   {excerpt}")
    return lines


def format_live_info_response(
    answer: str | None,
    sources: list,
    *,
    lang: str = "fr",
    query: str = "",
    fallback: bool = False,
) -> str:
    """News / market — clean answer up top, sources with 📎 link at the bottom."""
    normalized = _dedupe_sources(_normalize_sources(sources))
    direct = (answer or "").strip() or _extract_direct_answer(query, normalized, lang)
    direct = _complete_sentence(direct, 280)

    if lang == "fr":
        header = [
            "══════════════════════════════════",
            "📅 ACTU — sources web vérifiées",
            "══════════════════════════════════",
            "",
            direct,
            "",
            "📎 Sources",
        ]
        footer = "ℹ️ Fallback web direct — recalibration LLM cloud indisponible." if fallback else ""
    else:
        header = [
            "══════════════════════════════════",
            "📅 LIVE INFO — verified web sources",
            "══════════════════════════════════",
            "",
            direct,
            "",
            "📎 Sources",
        ]
        footer = "ℹ️ Direct web fallback — cloud LLM recalibration unavailable." if fallback else ""

    lines = list(header)
    if not normalized:
        lines.append("   (aucune source détaillée)" if lang == "fr" else "   (no detailed source)")
    else:
        for src in normalized[:4]:
            lines.extend(_source_lines(src, lang))
            lines.append("")

    if footer:
        lines.extend(["", footer])
    return "\n".join(lines).rstrip()


def format_live_info_brief(
    snippets: list,
    *,
    lang: str = "fr",
    query: str = "",
) -> str:
    """Backward compat — delegates to the answer + sources format."""
    return format_live_info_response(
        None, snippets, lang=lang, query=query, fallback=True,
    )