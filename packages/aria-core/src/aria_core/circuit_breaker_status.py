"""Aggregates the LIVE (in-process, source of truth) circuit-breaker state
of the 5 service modules that have a real open/closed circuit, enriched with
the last known transition from ``circuit_breaker_log`` (04/08).

Deliberately a separate module from ``circuit_breaker_log`` (a pure logger
that never imports the service modules, to avoid a cycle): this module is
the one layer allowed to import ``services.blockscout`` /
``services.dexscreener`` / ``services.goplus`` / ``services.
wallet_transfers_fast`` / ``momentum_entry`` directly, since none of those
ever import it back.

9 tracked states across the 5 files (verified against the actual code,
04/08 mapping + cross-check): ``blockscout:<chain>`` (one per chain in
``_chain_clients``, at least "base"), ``dexscreener``, ``goplus``,
``goplus_auth``, ``wallet_transfers_alchemy``, ``wallet_transfers_moralis``,
``ohlcv_<provider>`` for each of the 6 OHLCV cascade providers. The other 12
service files (8 counter-only, 2 no-threshold, 2 delegating) are NOT
covered -- reported honestly by the diagnostics endpoint as absent from this
status rather than faked as "closed"."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from aria_core import circuit_breaker_log

SUSTAINED_OUTAGE_WINDOW_HOURS = 1.0
SUSTAINED_OUTAGE_MIN_REOPENS = 2

OHLCV_PROVIDERS = ("geckoterminal", "mobula", "dexpaprika", "coinmarketcap", "codex", "dune")
WALLET_TRANSFER_PROVIDERS = ("alchemy", "moralis")


def _entry(
    *, is_open: bool, consecutive_failures: int, threshold: int,
    cooldown_seconds: float, seconds_remaining: float | None,
) -> dict:
    return {
        "circuit_state": "tracked",
        "state": "open" if is_open else "closed",
        "consecutive_failures": consecutive_failures,
        "threshold": threshold,
        "cooldown_seconds": cooldown_seconds,
        "seconds_remaining": max(0.0, seconds_remaining) if seconds_remaining is not None else None,
    }


async def get_circuit_status() -> dict:
    """One dict per tracked service, keyed by service name (e.g.
    ``"blockscout:base"``, ``"ohlcv_geckoterminal"``) -- live in-memory state
    plus the last logged transition, if any."""
    from aria_core.services import blockscout, dexscreener, goplus, wallet_transfers_fast
    from aria_core import momentum_entry

    result: dict[str, dict] = {}

    for chain, client in blockscout._chain_clients.items():
        now = time.monotonic()
        result[f"blockscout:{chain}"] = _entry(
            is_open=now < client._circuit_open_until,
            consecutive_failures=client._consecutive_failures,
            threshold=blockscout._FAIL_STREAK_WARN_THRESHOLD,
            cooldown_seconds=blockscout._CIRCUIT_COOLDOWN_SECONDS,
            seconds_remaining=client._circuit_open_until - now,
        )

    now_mono = time.monotonic()
    result["dexscreener"] = _entry(
        is_open=now_mono < dexscreener._circuit_open_until,
        consecutive_failures=dexscreener._consecutive_failures,
        threshold=dexscreener._CIRCUIT_FAIL_THRESHOLD,
        cooldown_seconds=dexscreener._CIRCUIT_COOLDOWN_SECONDS,
        seconds_remaining=dexscreener._circuit_open_until - now_mono,
    )

    client = goplus.goplus_client
    now_time = time.time()
    result["goplus"] = _entry(
        is_open=client.circuit_open(),
        consecutive_failures=client._consecutive_failures,
        threshold=goplus._CIRCUIT_FAIL_THRESHOLD,
        cooldown_seconds=goplus._CIRCUIT_COOLDOWN_S,
        seconds_remaining=client._circuit_open_until - now_time,
    )
    result["goplus_auth"] = _entry(
        is_open=now_time < client._auth_broken_until,
        consecutive_failures=0,  # 04/08 -- no counter: triggers on the FIRST code 4012, not a streak
        threshold=1,
        cooldown_seconds=goplus._AUTH_BROKEN_COOLDOWN_S,
        seconds_remaining=client._auth_broken_until - now_time,
    )

    for provider in WALLET_TRANSFER_PROVIDERS:
        now_mono = time.monotonic()
        open_until = wallet_transfers_fast._circuit_open_until.get(provider, 0.0)
        result[f"wallet_transfers_{provider}"] = _entry(
            is_open=now_mono < open_until,
            consecutive_failures=wallet_transfers_fast._consecutive_failures.get(provider, 0),
            threshold=wallet_transfers_fast._CIRCUIT_FAIL_THRESHOLD,
            cooldown_seconds=wallet_transfers_fast._CIRCUIT_COOLDOWN_SECONDS,
            seconds_remaining=open_until - now_mono,
        )

    for provider in OHLCV_PROVIDERS:
        now_mono = time.monotonic()
        open_until = momentum_entry._provider_cooldown_until.get(provider, 0.0)
        result[f"ohlcv_{provider}"] = _entry(
            is_open=now_mono < open_until,
            consecutive_failures=momentum_entry._provider_fail_counts.get(provider, 0),
            threshold=momentum_entry._PROVIDER_FAIL_THRESHOLD,
            cooldown_seconds=momentum_entry._PROVIDER_COOLDOWN_SECONDS,
            seconds_remaining=open_until - now_mono,
        )
        # 04/08 -- process-local only (see momentum_entry.py's own comment on
        # _provider_cooldown_until): explicitly flagged so a diagnostic reader
        # never assumes "since" survives a redeploy the way the other 8 do.
        result[f"ohlcv_{provider}"]["persisted_state"] = False

    last_events = await circuit_breaker_log.last_event_per_service()
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=SUSTAINED_OUTAGE_WINDOW_HOURS)
    ).isoformat()
    for service, entry in result.items():
        entry["last_event"] = last_events.get(service)
        # 04/08 -- a single "opened" is a normal, expected cooldown (every
        # breaker here has one); what the Telegram alert cares about is a
        # provider that keeps re-opening, i.e. never actually recovers.
        opened_count = await circuit_breaker_log.count_opened_since(service, since_iso)
        entry["opened_count_last_hour"] = opened_count
        entry["sustained_outage"] = (
            entry["state"] == "open" and opened_count >= SUSTAINED_OUTAGE_MIN_REOPENS
        )

    return result
