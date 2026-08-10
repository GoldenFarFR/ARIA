"""x402 payment attempt journal -- append-only, plain file, INDEPENDENT of
SQLite (10/08, real incident: a real 0.01 USDC x402 payment to a known,
legitimate provider -- twit.sh, matched 77+ prior identical payments --
settled on-chain with ZERO trace anywhere in `x402_spend_log`, not even a
"pending" row from `try_reserve`. Root cause never pinned down with full
certainty (best working theory: SQLite contention from a concurrent process
on the same `aria.db`), but the structural fix doesn't depend on knowing the
exact cause: this journal writes BEFORE any payment is attempted, on a plain
file that can never be blocked by a SQLite lock/contention the way the
`x402_spend_log` table can.

This is a SAFETY NET, not a replacement for `x402_spend_log` (the real
budget/audit source of truth) -- `agent_wallet_monitor.py` consults BOTH: an
exact match in `x402_spend_log` still classifies "known_x402" as before; a
miss there but a match HERE (or against the known-provider registry in
`x402_budget.known_pay_to_addresses`) downgrades a false "unexpected_outflow"
to "probable_known_provider_unlogged" -- the pause/kill-switch behavior is
IDENTICAL in both cases, only the Telegram message's diagnostic improves.
Never a way to silently wave through a real unauthorized outflow."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

_JOURNAL_FILENAME = "x402_payment_attempts.jsonl"
# Same tolerance window as agent_wallet_monitor's own x402 match window --
# a real payment settles on-chain within seconds of the attempt being logged.
_LOOKUP_WINDOW_MINUTES = 30


def _journal_path():
    return data_dir() / _JOURNAL_FILENAME


@dataclass(frozen=True)
class AttemptRecord:
    ts: str
    resource: str
    provider: str
    amount_usd: float
    pay_to: str
    contract: str = ""
    token_symbol: str = ""


def record_attempt(
    *, resource: str, provider: str, amount_usd: float, pay_to: str,
    contract: str = "", token_symbol: str = "",
) -> None:
    """Best-effort, synchronous, called BEFORE `x402_budget.try_reserve` --
    deliberately never awaited/async: a plain blocking file append is simpler
    and cannot be starved by the same SQLite contention this journal exists
    to survive. Never raises -- a failure here must NEVER block a real
    payment attempt, it would defeat the whole purpose."""
    record = AttemptRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        resource=resource, provider=provider, amount_usd=amount_usd,
        pay_to=pay_to, contract=contract, token_symbol=token_symbol,
    )
    try:
        path = _journal_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as exc:  # noqa: BLE001 -- safety net must never itself block a payment
        logger.error("x402_attempt_journal: failed to record attempt (%s) -- %s", resource, exc)


def recent_attempts(*, since: datetime | None = None) -> list[dict]:
    """Reads the journal, newest-relevant window only (default: last
    `_LOOKUP_WINDOW_MINUTES`). Never raises -- an unreadable/missing journal
    returns an empty list (the caller's own `x402_spend_log` match stays the
    primary source; this is a safety net, not a hard dependency)."""
    cutoff = since or (datetime.now(timezone.utc) - timedelta(minutes=_LOOKUP_WINDOW_MINUTES))
    path = _journal_path()
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = row.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts >= cutoff:
                    out.append(row)
    except Exception as exc:  # noqa: BLE001 -- safety net, never blocking
        logger.warning("x402_attempt_journal: failed to read journal (%s)", exc)
        return []
    return out
