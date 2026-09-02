"""Periodic market-regime peak digest across the three shadow pockets (24/08,
operator-directed: after discovering the shadow pockets were silently gated
by ``blocked_regime_closed`` for over an hour with zero visible signal, the
operator asked for a standing 30-minute readout of each chain's own
``regime_state()`` -- so a closed gate is a fact on a schedule, never
something that only surfaces after someone asks "why isn't it trading".

One row per chain per cycle in a dedicated history table, so the digest can
show a short trend (last 3 readings) rather than a single snapshot -- a
median climbing from 14% to 25% reads very differently from one sitting
flat at 14%, and only a trend line makes that visible without asking the
operator to remember the previous number themselves."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import shadow_db_path

TABLE = "regime_peak_history"
NOTIFICATION_TABLE = "regime_peak_digest_notification"
DB_PATH = str(shadow_db_path())

# How many past readings to show in the trend arrow (14.2->17.6->25.4).
# 3 -- the operator's own request, enough to see direction without a wall of
# numbers on a Telegram message.
TREND_POINTS = 3

# 02/09 -- operator: "sur le canal telegram c'est le bordel". Measured cause:
# this digest fired unconditionally every 30 min (48 msg/day) and had been
# sending a byte-identical zero-valued message for days ("0.0% -- tendance
# 0.0->0.0->0.0"), drowning every real alert and about to drown the new live
# signal (specs/017). The 24/08 intent -- "a closed gate is a fact on a
# schedule, never something that only surfaces after someone asks" -- is
# preserved by notifying on a real CHANGE of gate state plus a daily
# heartbeat, instead of on a fixed 30-min drumbeat. The READING itself still
# happens every cycle (record_peak keeps feeding the trend); only the SEND
# became conditional.
DIGEST_REMINDER_HOURS = 24

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                median_peak_pct REAL,
                threshold_pct REAL,
                is_open INTEGER NOT NULL,
                samples INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_chain ON {TABLE}(chain, id)"
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {NOTIFICATION_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                signature TEXT NOT NULL,
                notified_at TEXT NOT NULL
            )
            """
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_peak(chain: str, state: dict, *, db_path: str | None = None) -> None:
    """Appends one reading. Never raises into the caller -- a digest that
    fails to log its own history must never break the heartbeat cycle
    around it, same discipline as every other shadow-side archive."""
    path = db_path or _db_path()
    try:
        await _ensure_table(path)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (chain, median_peak_pct, threshold_pct, is_open, samples, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chain,
                    state.get("median_peak_pct"),
                    state.get("threshold_pct"),
                    1 if state.get("open") else 0,
                    state.get("samples", 0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- history logging never blocks the digest
        pass


async def recent_trend(chain: str, *, limit: int = TREND_POINTS, db_path: str | None = None) -> list[float]:
    """Oldest-to-newest median_peak_pct readings for one chain, most recent
    ``limit`` rows. Excludes NULL (below-REGIME_WINDOW) readings -- a trend
    arrow mixing real numbers with "not enough data yet" would mislead."""
    path = db_path or _db_path()
    await _ensure_table(path)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            f"""
            SELECT median_peak_pct FROM {TABLE}
            WHERE chain = ? AND median_peak_pct IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (chain, limit),
        )
        rows = [r[0] for r in await cur.fetchall()]
    return list(reversed(rows))


def _format_chain_line(label: str, state: dict, trend: list[float]) -> str:
    median = state.get("median_peak_pct")
    threshold = state.get("threshold_pct")
    is_open = state.get("open")
    samples = state.get("samples", 0)
    # 24/08 real incident: base_momentum_shadow's threshold_pct can be None
    # (disarmed) while median_peak_pct is still a real number -- the sensor
    # keeps accumulating even when the gate has no opinion. f"{None:.0f}"
    # crashed the whole heartbeat tick every cycle until this was caught.
    threshold_str = f"{threshold:.0f}%" if threshold is not None else "désarmé"

    if median is None:
        return f"{label} : pas assez de données ({samples} échantillons, seuil {threshold_str})"

    glyph = "✅" if is_open else "🔒"
    trend_str = "->".join(f"{v:.1f}" for v in trend) if trend else f"{median:.1f}"
    return f"{glyph} {label} : {median:.1f}% (seuil {threshold_str}) — tendance {trend_str}"


async def build_regime_peak_digest(*, db_path: str | None = None) -> str:
    """One message covering all three shadow pockets' own regime gate --
    reuses each module's own ``regime_state()`` (never a second copy of the
    median/threshold logic), records the reading, and shows a 3-point trend
    per chain so the operator can see direction, not just a snapshot."""
    from aria_core import base_momentum_shadow, robinhood_pump_shadow, solana_late_bonding_shadow

    chains = [
        ("Solana", solana_late_bonding_shadow.regime_state, "solana"),
        ("Robinhood", robinhood_pump_shadow.regime_state, "robinhood"),
        ("Base", base_momentum_shadow.regime_state, "base"),
    ]

    lines = ["📊 Pic de marché (régime d'entrée) — 3 poches\n"]
    for label, state_fn, chain_key in chains:
        state = await state_fn()
        await record_peak(chain_key, state, db_path=db_path)
        trend = await recent_trend(chain_key, db_path=db_path)
        lines.append(_format_chain_line(label, state, trend))

    return "\n".join(lines)


def _gate_signature(states: dict[str, dict]) -> str:
    """What a reader actually needs to be told about: whether each pocket's
    gate is OPEN or CLOSED, and whether it has enough data to have an
    opinion. Deliberately NOT the median value -- a median drifting
    14.2->14.3 is not news, and treating it as news is exactly what turned
    this digest into 48 identical messages a day."""
    parts = []
    for chain in sorted(states):
        state = states[chain]
        has_data = state.get("median_peak_pct") is not None
        parts.append(f"{chain}:{1 if state.get('open') else 0}:{1 if has_data else 0}")
    return "|".join(parts)


async def _last_notification(db_path: str | None = None) -> tuple[str, datetime] | None:
    path = db_path or _db_path()
    await _ensure_table(path)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(f"SELECT signature, notified_at FROM {NOTIFICATION_TABLE} WHERE id = 1")
        row = await cur.fetchone()
    if not row:
        return None
    try:
        return row[0], datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def _record_notification(signature: str, db_path: str | None = None) -> None:
    path = db_path or _db_path()
    await _ensure_table(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"INSERT INTO {NOTIFICATION_TABLE} (id, signature, notified_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET signature=excluded.signature, notified_at=excluded.notified_at",
            (signature, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def build_regime_peak_digest_if_noteworthy(*, db_path: str | None = None) -> str | None:
    """Same reading and same history as ``build_regime_peak_digest`` (the
    trend keeps accumulating every cycle), but returns the message ONLY when
    it carries news: a pocket's gate opened or closed, a pocket gained or
    lost enough data to have an opinion, or nothing has been sent for
    ``DIGEST_REMINDER_HOURS``. ``None`` means "nothing worth a Telegram
    message this cycle" -- the caller must not send.

    Never raises: on any bookkeeping failure it falls back to sending, since
    a duplicate digest is a far smaller harm than a silently missed gate
    change (fail-open on the INFORMATION, the opposite posture from the
    money-side guards)."""
    from aria_core import base_momentum_shadow, robinhood_pump_shadow, solana_late_bonding_shadow

    chains = [
        ("Solana", solana_late_bonding_shadow.regime_state, "solana"),
        ("Robinhood", robinhood_pump_shadow.regime_state, "robinhood"),
        ("Base", base_momentum_shadow.regime_state, "base"),
    ]

    states: dict[str, dict] = {}
    lines = ["📊 Pic de marché (régime d'entrée) — 3 poches\n"]
    for label, state_fn, chain_key in chains:
        state = await state_fn()
        states[chain_key] = state
        await record_peak(chain_key, state, db_path=db_path)
        trend = await recent_trend(chain_key, db_path=db_path)
        lines.append(_format_chain_line(label, state, trend))

    signature = _gate_signature(states)
    try:
        last = await _last_notification(db_path=db_path)
        if last is not None:
            last_signature, last_at = last
            unchanged = last_signature == signature
            recent = (datetime.now(timezone.utc) - last_at) < timedelta(hours=DIGEST_REMINDER_HOURS)
            if unchanged and recent:
                return None
        await _record_notification(signature, db_path=db_path)
    except Exception:  # noqa: BLE001 -- fail-open: never swallow a real gate change
        pass

    return "\n".join(lines)
