"""Compteur tokens LLM — journal JSONL mensuel (agrégation par jour/provider)."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

_chat_usage_ctx: ContextVar[dict[str, int] | None] = ContextVar("chat_usage_ctx", default=None)


def begin_chat_usage_tracking() -> None:
    _chat_usage_ctx.set({
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
    })


def clear_chat_usage_tracking() -> None:
    _chat_usage_ctx.set(None)


def get_chat_usage_totals() -> dict[str, int]:
    state = _chat_usage_ctx.get()
    if not state:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }
    return dict(state)


def _accumulate_chat_usage(*, input_tokens: int, output_tokens: int) -> None:
    state = _chat_usage_ctx.get()
    if state is None:
        return
    inp = int(input_tokens)
    out = int(output_tokens)
    state["input_tokens"] += inp
    state["output_tokens"] += out
    state["total_tokens"] += inp + out
    state["calls"] += 1


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
    """Fallback grossier (~4 chars/token) quand l'API ne renvoie pas usage."""
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
    at: datetime | None = None,
) -> None:
    """Append une ligne dans data/llm-usage/YYYY-MM.jsonl."""
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
        path = _month_path(day)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if ok and kind == "chat":
            _accumulate_chat_usage(
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
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


def summarize_usage(*, month: str | None = None) -> dict[str, Any]:
    """
    Agrège tokens par mois (défaut: mois courant UTC).
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