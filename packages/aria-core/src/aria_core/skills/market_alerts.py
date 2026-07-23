"""Market alerts — paid crypto-Twitter digest (Otto AI, x402,
`services/ottoai.py`), complementary to `market_sentiment.py` (QUANTITATIVE
RSI/Bollinger regimes per pair) with a QUALITATIVE general-market signal
(recent alerts/chatter, not measurable by indicators alone).

Same architecture as `market_sentiment.py`: dataclass + gated heartbeat cycle
+ "no expiration" persistence (always overwrites the last reading, never a
TTL) + soft degradation. Deliberately separate TWIN module (not a
modification of `market_sentiment.py`): "one service = one new module"
doctrine, and the underlying data (untrusted free-form third-party text)
requires dedicated sanitization (mandate #192) that `market_sentiment.py`
(pure numbers) never needed to apply.

Gate `ARIA_MARKET_ALERTS_ENABLED` (OFF by default). Cost: $0.001/call (Otto
AI), covered by the existing `x402_budget.py` cap ($5/week, shared with
`conviction_research.py`/`cybercentry_insight.py`) -- same hourly cycle as
`market_sentiment_cycle` (60min) = ~$0.024/day, wide margin."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_MAX_DIGEST_CHARS = 1500  # full digest observed ~700-900 chars, wide margin


def market_alerts_enabled() -> bool:
    """Seam gated OFF by default. The heartbeat cycle only runs once this flag
    is enabled by the operator -- same pattern as market_sentiment_enabled()."""
    return os.environ.get("ARIA_MARKET_ALERTS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class MarketAlertsReading:
    digest_text: str
    source_timestamp: str | None
    computed_at: str


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_alerts (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                digest_text TEXT NOT NULL,
                source_timestamp TEXT,
                computed_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def upsert_reading(digest_text: str, *, source_timestamp: str | None = None) -> None:
    """ALWAYS overwrites the previous row (a single row, id=1) -- same
    "no expiration" doctrine as market_sentiment.py. The text is sanitized
    HERE (single choke point) -- never stored raw, never reinjected raw
    elsewhere."""
    from aria_core.sanitize import sanitize_untrusted_text

    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    safe_text = sanitize_untrusted_text(digest_text, _MAX_DIGEST_CHARS)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO market_alerts (id, digest_text, source_timestamp, computed_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                digest_text=excluded.digest_text, source_timestamp=excluded.source_timestamp,
                computed_at=excluded.computed_at
            """,
            (safe_text, source_timestamp, now),
        )
        await db.commit()


async def latest_reading() -> MarketAlertsReading | None:
    """Last persisted reading (already sanitized at write time) -- never a
    recomputation, the heartbeat handles refreshing. ``None`` if nothing has
    been written yet (gate OFF, or the first cycle hasn't run yet)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT digest_text, source_timestamp, computed_at FROM market_alerts WHERE id = 1"
            )
        ).fetchone()
    if row is None:
        return None
    return MarketAlertsReading(digest_text=row[0], source_timestamp=row[1], computed_at=row[2])


async def run_market_alerts_cycle() -> dict:
    """Refreshes the reading -- soft degradation, never an exception
    propagating (same doctrine as run_market_sentiment_cycle). A
    payment/network failure simply leaves the last known reading in place (no
    overwrite with emptiness)."""
    from aria_core.services.ottoai import fetch_twitter_digest

    try:
        digest = await fetch_twitter_digest()
    except Exception:
        logger.exception("market_alerts_cycle failed")
        return {"updated": False, "reason": "exception"}
    if not digest.available:
        return {"updated": False, "reason": digest.error or "indisponible"}
    await upsert_reading(digest.digest_text, source_timestamp=digest.timestamp)
    return {"updated": True}


def format_alerts_report(reading: MarketAlertsReading | None) -> str:
    if reading is None:
        return (
            "Alertes de marché : aucune lecture encore disponible (le cycle "
            "heartbeat n'a pas encore tourné, ou le gate est désactivé)."
        )
    lines = [
        "📰 ARIA — alertes de marché (digest crypto-Twitter, Otto AI)",
        "",
        reading.digest_text,
        "",
    ]
    if reading.source_timestamp:
        lines.append(f"source datée {reading.source_timestamp}, récupérée {reading.computed_at}")
    else:
        lines.append(f"calculé {reading.computed_at}")
    lines.append("")
    lines.append(
        "Digest de tiers, texte libre -- signal de contexte/chatter récent, jamais "
        "un fait vérifié sur un projet précis."
    )
    return "\n".join(lines)
