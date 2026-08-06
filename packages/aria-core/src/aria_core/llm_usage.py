"""LLM token counter — monthly JSONL journal (aggregated by day/provider)."""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

_FREE_PROVIDERS = frozenset({"", "none", "ollama", "local", "unknown"})
_GROK_BUILD_PROVIDERS = frozenset({"grok", "xai", "grok-build"})

_chat_usage_ctx: ContextVar[dict[str, int] | None] = ContextVar("chat_usage_ctx", default=None)
_chat_fallback_ctx: ContextVar[dict[str, Any] | None] = ContextVar("chat_fallback_ctx", default=None)

# 06/08 -- operator request: a short cost line on every paid Telegram reply
# ("XXX$ dépensé"), Haiku/Sonnet only (the only two providers meant to be
# used long-term, see CLAUDE.md). USD per MILLION tokens (input, output).
# SOURCED, never guessed (doctrine: a number cited without a source is a
# liability, not a convenience) -- anthropic.com/claude/haiku +
# anthropic.com/news/claude-sonnet-5, checked live 06/08. Sonnet 5's
# introductory price ($2/$10) steps up to standard ($3/$15) on 2026-09-01 --
# handled by date below so this table never silently drifts stale.
_HAIKU_PRICE_PER_MILLION = (1.0, 5.0)
_SONNET_INTRO_PRICE_PER_MILLION = (2.0, 10.0)
_SONNET_STANDARD_PRICE_PER_MILLION = (3.0, 15.0)
_SONNET_PRICE_STEP_UP_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Devil's Advocate report bae40fb9: _price_per_million_usd runs on EVERY
# cost_usd_for call (i.e. every paid LLM call, Telegram cost line included)
# -- an unconditional logger.warning() in the 3-day step-up window would
# have produced one near-identical line per call, drowning the exact signal
# it exists to surface. Logged once per calendar day instead (a real
# process-lifetime flag would miss a restart mid-window; a set keyed by ISO
# date self-resets daily and needs no cleanup -- 3 entries max, ever).
_step_up_warning_logged_dates: set[str] = set()


def _price_per_million_usd(provider: str, model: str, *, at: datetime | None = None) -> tuple[float, float] | None:
    """(input, output) USD/million tokens for a KNOWN model, else None --
    never a guessed price. Matched by substring since the exact model ID
    varies by call site (e.g. "claude-haiku-4-5-20251001" vs a short alias)."""
    if (provider or "").strip().lower() != "anthropic":
        return None
    m = (model or "").lower()
    if "haiku" in m:
        return _HAIKU_PRICE_PER_MILLION
    if "sonnet" in m:
        now = at or datetime.now(timezone.utc)
        if now >= _SONNET_PRICE_STEP_UP_AT:
            return _SONNET_STANDARD_PRICE_PER_MILLION
        days_left = (_SONNET_PRICE_STEP_UP_AT - now).days
        today_key = now.date().isoformat()
        if days_left <= 3 and today_key not in _step_up_warning_logged_dates:
            # Devil's Advocate report dfb1ce3d: a hardcoded step-up date is
            # fine short-term, but must never drift silently -- a loud
            # signal in the days right before it fires beats discovering
            # weeks later that every cost figure has been quietly wrong.
            _step_up_warning_logged_dates.add(today_key)
            logger.warning(
                "Sonnet 5 pricing steps up to $%.0f/$%.0f per million tokens "
                "in %d day(s) (%s) -- verify _SONNET_STANDARD_PRICE_PER_MILLION "
                "is still correct before/at that date.",
                _SONNET_STANDARD_PRICE_PER_MILLION[0], _SONNET_STANDARD_PRICE_PER_MILLION[1],
                days_left, _SONNET_PRICE_STEP_UP_AT.date().isoformat(),
            )
        return _SONNET_INTRO_PRICE_PER_MILLION
    return None


def cost_usd_for(
    *, provider: str, model: str, input_tokens: int, output_tokens: int, at: datetime | None = None,
) -> float | None:
    """None when the model's price isn't in the table above -- honest
    degradation (no invented figure), same doctrine as every other unknown
    in this codebase. ``at`` lets a test pin the Sonnet price-step-up date
    deterministically -- real callers never pass it (real current time)."""
    prices = _price_per_million_usd(provider, model, at=at)
    if prices is None:
        return None
    price_in, price_out = prices
    return (int(input_tokens) / 1_000_000) * price_in + (int(output_tokens) / 1_000_000) * price_out


def begin_chat_usage_tracking() -> None:
    _chat_usage_ctx.set({
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "cost_unknown": False,
    })
    _chat_fallback_ctx.set({"used": False, "provider": ""})


def clear_chat_usage_tracking() -> None:
    _chat_usage_ctx.set(None)
    _chat_fallback_ctx.set(None)


def get_chat_usage_totals() -> dict[str, Any]:
    state = _chat_usage_ctx.get()
    if not state:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "cost_usd": 0.0,
            "cost_unknown": False,
        }
    return dict(state)


def mark_fallback_used(provider: str) -> None:
    """Notes that a fallback route (not the primary route) answered for this
    chat turn (#135). No-op outside a turn tracked by begin_chat_usage_tracking
    — same pattern as _accumulate_chat_usage below."""
    state = _chat_fallback_ctx.get()
    if state is None:
        return
    state["used"] = True
    state["provider"] = provider


def get_chat_fallback_state() -> dict[str, Any]:
    state = _chat_fallback_ctx.get()
    if not state:
        return {"used": False, "provider": ""}
    return dict(state)


def _accumulate_chat_usage(
    *, input_tokens: int, output_tokens: int, provider: str = "", model: str = "",
) -> None:
    state = _chat_usage_ctx.get()
    if state is None:
        return
    inp = int(input_tokens)
    out = int(output_tokens)
    state["input_tokens"] += inp
    state["output_tokens"] += out
    state["total_tokens"] += inp + out
    state["calls"] += 1
    cost = cost_usd_for(provider=provider, model=model, input_tokens=inp, output_tokens=out)
    if cost is None:
        state["cost_unknown"] = True
    else:
        state["cost_usd"] += cost


def llm_usage_dir() -> Path:
    path = data_dir() / "llm-usage"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _month_path(day: str) -> Path:
    month = day[:7]
    return llm_usage_dir() / f"{month}.jsonl"


def parse_usage_from_response(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (inp + out))
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total}


def estimate_tokens_from_text(*parts: str) -> int:
    """Rough fallback (~4 chars/token) when the API doesn't return usage."""
    text = " ".join(p for p in parts if p)
    return max(1, len(text) // 4)


def record_llm_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    ok: bool = True,
    status_code: int | None = None,
    kind: str = "chat",
    estimated: bool = False,
    depth: str | None = None,
    truncated: bool = False,
    latency_ms: float | None = None,
    at: datetime | None = None,
) -> None:
    """Appends a line to data/llm-usage/YYYY-MM.jsonl.

    ``latency_ms`` (17/07, operator request -- arbitrate Grok vs. Gemini on
    REAL data rather than a guess): response time measured on the caller side
    (sending the request until the HTTP response is received), never
    estimated. Absent -> no field written (honest degradation, never a made-up
    value)."""
    try:
        now = at or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        total = int(input_tokens) + int(output_tokens)
        row = {
            "ts": now.isoformat(),
            "day": day,
            "provider": (provider or "unknown").lower(),
            "model": model or "",
            "kind": kind,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": total,
            "ok": bool(ok),
            "estimated": bool(estimated),
        }
        if status_code is not None:
            row["status_code"] = int(status_code)
        if depth:
            row["depth"] = str(depth)
        if truncated:
            row["truncated"] = True
        if latency_ms is not None:
            row["latency_ms"] = round(float(latency_ms), 1)
        cost = cost_usd_for(
            provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens, at=now,
        )
        if cost is not None:
            row["cost_usd"] = round(cost, 5)
        path = _month_path(day)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if ok and kind == "chat":
            _accumulate_chat_usage(
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                provider=provider or "",
                model=model or "",
            )
    except Exception as exc:
        logger.debug("llm usage log skip: %s", exc)


def _iter_rows(month: str | None = None) -> list[dict[str, Any]]:
    base = llm_usage_dir()
    if not base.is_dir():
        return []
    files = sorted(base.glob("*.jsonl"))
    if month:
        files = [p for p in files if p.stem == month]
    rows: list[dict[str, Any]] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    return rows


def is_paid_provider(provider: str) -> bool:
    """Billed cloud provider (as opposed to local Ollama)."""
    return (provider or "").strip().lower() not in _FREE_PROVIDERS


def _format_token_count(value: int) -> str:
    n = int(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}".replace(",", " ")


def summarize_paid_usage(*, month: str | None = None, lifetime: bool = False) -> dict[str, Any]:
    """
    Aggregates only paid cloud calls (grok, groq, xai, …).
    lifetime=True → all months in data/llm-usage/.
    """
    if lifetime:
        month_key = "lifetime"
        rows = [r for r in _iter_rows() if _is_paid_row(r)]
    else:
        month_key = month or datetime.now(timezone.utc).strftime("%Y-%m")
        rows = [r for r in _iter_rows(month_key) if _is_paid_row(r)]

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls_ok": 0,
        "calls_failed": 0,
    }
    by_provider: dict[str, dict[str, int]] = {}
    by_day: dict[str, dict[str, int]] = {}

    def _bump(bucket: dict[str, dict[str, int]], key: str, row: dict[str, Any]) -> None:
        slot = bucket.setdefault(
            key,
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0},
        )
        slot["input_tokens"] += int(row.get("input_tokens") or 0)
        slot["output_tokens"] += int(row.get("output_tokens") or 0)
        slot["total_tokens"] += int(row.get("total_tokens") or 0)
        slot["calls"] += 1

    for row in rows:
        if bool(row.get("ok")):
            totals["calls_ok"] += 1
        else:
            totals["calls_failed"] += 1
        if not row.get("ok"):
            continue
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        tot = int(row.get("total_tokens") or (inp + out))
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_tokens"] += tot
        _bump(by_provider, str(row.get("provider") or "unknown"), row)
        _bump(by_day, str(row.get("day") or "?"), row)

    return {
        "month": month_key,
        "totals": totals,
        "by_provider": dict(sorted(by_provider.items())),
        "by_day": dict(sorted(by_day.items())),
        "rows": len(rows),
    }


def _is_paid_row(row: dict[str, Any]) -> bool:
    return is_paid_provider(str(row.get("provider") or ""))


def _is_grok_build_row(row: dict[str, Any]) -> bool:
    return (str(row.get("provider") or "").strip().lower()) in _GROK_BUILD_PROVIDERS


def summarize_grok_build_usage(*, month: str | None = None, lifetime: bool = False) -> dict[str, Any]:
    """Grok Build / xAI tokens only (not Groq, not Cursor)."""
    if lifetime:
        month_key = "lifetime"
        rows = [r for r in _iter_rows() if _is_grok_build_row(r)]
    else:
        month_key = month or datetime.now(timezone.utc).strftime("%Y-%m")
        rows = [r for r in _iter_rows(month_key) if _is_grok_build_row(r)]

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls_ok": 0,
        "calls_failed": 0,
    }
    for row in rows:
        if bool(row.get("ok")):
            totals["calls_ok"] += 1
        else:
            totals["calls_failed"] += 1
        if not row.get("ok"):
            continue
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_tokens"] += int(row.get("total_tokens") or (inp + out))

    return {"month": month_key, "totals": totals, "rows": len(rows)}


def format_grok_build_dashboard(*, month: str | None = None, lang: str = "fr") -> str:
    month_key = month or datetime.now(timezone.utc).strftime("%Y-%m")
    month_sum = summarize_grok_build_usage(month=month_key)
    life_sum = summarize_grok_build_usage(lifetime=True)
    m_tok = _format_token_count(int(month_sum["totals"]["total_tokens"]))
    l_tok = _format_token_count(int(life_sum["totals"]["total_tokens"]))
    if lang == "fr":
        return f"grok {month_key}: {m_tok} tok | total: {l_tok} tok"
    return f"grok {month_key}: {m_tok} tok | total: {l_tok} tok"


def paid_usage_snapshot(*, month: str | None = None) -> dict[str, Any]:
    """Current month + lifetime total — for the KART dashboard (0 API calls)."""
    month_key = month or datetime.now(timezone.utc).strftime("%Y-%m")
    month_sum = summarize_paid_usage(month=month_key)
    life_sum = summarize_paid_usage(lifetime=True)
    return {
        "month": month_key,
        "month_total_tokens": int(month_sum["totals"]["total_tokens"]),
        "month_calls": int(month_sum["totals"]["calls_ok"]),
        "lifetime_total_tokens": int(life_sum["totals"]["total_tokens"]),
        "lifetime_calls": int(life_sum["totals"]["calls_ok"]),
        "by_provider_month": month_sum["by_provider"],
    }


def format_paid_usage_dashboard(*, month: str | None = None, lang: str = "fr") -> str:
    """KART backward compat — alias for Grok Build (xAI)."""
    return format_grok_build_dashboard(month=month, lang=lang)


def summarize_usage(*, month: str | None = None) -> dict[str, Any]:
    """
    Aggregates tokens by month (default: current UTC month).
    month format: YYYY-MM
    """
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    rows = _iter_rows(month)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls_ok": 0,
        "calls_failed": 0,
    }
    by_provider: dict[str, dict[str, int]] = {}
    by_day: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}

    def _bump(bucket: dict[str, dict[str, int]], key: str, row: dict[str, Any]) -> None:
        slot = bucket.setdefault(
            key,
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0},
        )
        slot["input_tokens"] += int(row.get("input_tokens") or 0)
        slot["output_tokens"] += int(row.get("output_tokens") or 0)
        slot["total_tokens"] += int(row.get("total_tokens") or 0)
        slot["calls"] += 1

    for row in rows:
        if bool(row.get("ok")):
            totals["calls_ok"] += 1
        else:
            totals["calls_failed"] += 1
        if not row.get("ok"):
            continue
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        tot = int(row.get("total_tokens") or (inp + out))
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_tokens"] += tot
        _bump(by_provider, str(row.get("provider") or "unknown"), row)
        _bump(by_day, str(row.get("day") or "?"), row)
        model_key = f"{row.get('provider')}/{row.get('model')}"
        _bump(by_model, model_key, row)

    return {
        "month": month,
        "totals": totals,
        "by_provider": dict(sorted(by_provider.items())),
        "by_day": dict(sorted(by_day.items())),
        "by_model": dict(sorted(by_model.items())),
        "rows": len(rows),
    }


# Devil's Advocate report dfb1ce3d: a full JSONL re-scan on every paid
# Telegram reply is synchronous I/O inside an async handler, growing daily
# as the month's file grows -- a short TTL cache turns "one scan per
# message" into "at most one scan per _MONTHLY_COST_CACHE_TTL_SECONDS",
# which is all a cost DISPLAY (never a financial decision) needs.
_MONTHLY_COST_CACHE_TTL_SECONDS = 60.0
_monthly_cost_cache: dict[str, tuple[float, float]] = {}  # month -> (value, computed_at_monotonic)


def _compute_monthly_cost_usd(month: str) -> float:
    total = 0.0
    for row in _iter_rows(month):
        if not row.get("ok"):
            continue
        if "cost_usd" in row:
            total += float(row["cost_usd"])
            continue
        cost = cost_usd_for(
            provider=str(row.get("provider") or ""),
            model=str(row.get("model") or ""),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
        )
        if cost is not None:
            total += cost
    return total


def clear_monthly_cost_cache() -> None:
    """Test-only escape hatch -- the cache is a module-level dict, shared
    across every test in the same process; without a way to clear it, two
    tests using the same month literal (e.g. "2026-07") would leak a stale
    value from one into the other whenever they run within the TTL window."""
    _monthly_cost_cache.clear()


def monthly_cost_usd(*, month: str | None = None) -> float:
    """Sum of known-price cost across this month's rows (Haiku/Sonnet today
    -- any provider _price_per_million_usd() doesn't know stays excluded,
    never guessed). Recomputes from tokens for rows logged before this cost
    tracking existed (no "cost_usd" field persisted yet) so the month total
    isn't silently short right after this feature ships.

    Cached for _MONTHLY_COST_CACHE_TTL_SECONDS -- see the module comment
    above. A caller that genuinely needs the exact live value (tests,
    reconciliation) should call _compute_monthly_cost_usd directly."""
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    now = time.monotonic()
    cached = _monthly_cost_cache.get(month)
    if cached is not None and (now - cached[1]) < _MONTHLY_COST_CACHE_TTL_SECONDS:
        return cached[0]
    value = _compute_monthly_cost_usd(month)
    _monthly_cost_cache[month] = (value, now)
    return value