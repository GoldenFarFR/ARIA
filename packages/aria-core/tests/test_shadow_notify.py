"""shadow_notify.notify_pocket -- shared open/close Telegram notifier
extracted 23/08 from Robinhood/Base's near-duplicate dedicated functions.

A background adversarial review of the first extraction draft flagged a real
risk: because Robinhood and Base currently share every numeric threshold,
a shared body could pass every test written against TODAY's identical
numbers while secretly hardcoding one pocket's value instead of reading
``cfg.module.X``. Every test below therefore uses two FAKE pockets with
DELIBERATELY DIFFERENT thresholds, so a hardcoded shortcut fails loudly."""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import shadow_notify

_SCHEMA = """
CREATE TABLE pocket_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address TEXT, symbol TEXT, entry_price REAL,
    detected_at TEXT, pool_created_at TEXT, reserve_usd REAL,
    m5_pct REAL, m15_pct REAL, buyers_m15 INTEGER, sellers_m15 INTEGER,
    exit_reason TEXT, final_multiplier REAL, realistic_final_multiplier REAL,
    realized_proceeds REAL, realistic_realized_proceeds REAL,
    next_scale_level REAL, last_checked_at TEXT, closed_at TEXT
)
"""


def _fake_module(tmp_path, name, **overrides):
    m = types.SimpleNamespace(
        DB_PATH=str(tmp_path / f"{name}.db"),
        TABLE="pocket_log",
        MAX_POOL_AGE_MINUTES=10.0,
        SCALE_OUT_STEP_PCT=25.0,
        SCALE_OUT_SELL_FRACTION=0.25,
        TRAILING_STOP_PCT=20.0,
    )
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


async def _init_db(db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_SCHEMA)
        await db.commit()


async def _insert_row(db_path, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(f"INSERT INTO pocket_log ({keys}) VALUES ({placeholders})", tuple(cols.values()))
        await db.commit()
        return cur.lastrowid


class _Recorder:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)


@pytest.mark.asyncio
async def test_first_open_pass_only_anchors_never_replays_history(tmp_path):
    m = _fake_module(tmp_path, "a")
    await _init_db(m.DB_PATH)
    await _insert_row(m.DB_PATH, pool_address="p1", symbol="AAA", entry_price=1.0,
                       detected_at=datetime.now(timezone.utc).isoformat())
    cfg = shadow_notify.PocketNotifyConfig(key="a", label="A", module=m, dexscreener_chain_slug="a")
    rec = _Recorder()
    await shadow_notify.notify_pocket(cfg, "open", send=rec.send)
    assert rec.messages == []


@pytest.mark.asyncio
async def test_open_after_anchor_notifies_new_row_only(tmp_path):
    m = _fake_module(tmp_path, "b")
    await _init_db(m.DB_PATH)
    cfg = shadow_notify.PocketNotifyConfig(key="b", label="ROBINHOOD", module=m, dexscreener_chain_slug="robinhood")
    rec = _Recorder()
    await shadow_notify.notify_pocket(cfg, "open", send=rec.send)  # anchor, table empty
    await _insert_row(m.DB_PATH, pool_address="p1", symbol="AAA", entry_price=1.0,
                       detected_at=datetime.now(timezone.utc).isoformat(), reserve_usd=9000.0)
    await shadow_notify.notify_pocket(cfg, "open", send=rec.send)
    assert len(rec.messages) == 1
    assert "ROBINHOOD (AAA)" in rec.messages[0]
    assert "OUVERTURE" in rec.messages[0]


@pytest.mark.asyncio
async def test_exit_text_reads_the_pockets_own_thresholds_not_a_shared_default(tmp_path):
    """The regression this test exists to catch: a shared body that hardcodes
    one pocket's working numbers instead of reading cfg.module.X would pass
    every other test here (today Robinhood/Base share every value) but fail
    this one, because these two fakes deliberately diverge."""
    m_a = _fake_module(tmp_path, "a", SCALE_OUT_STEP_PCT=25.0, SCALE_OUT_SELL_FRACTION=0.25, TRAILING_STOP_PCT=20.0)
    m_b = _fake_module(tmp_path, "b", SCALE_OUT_STEP_PCT=40.0, SCALE_OUT_SELL_FRACTION=0.5, TRAILING_STOP_PCT=8.0)
    cfg_a = shadow_notify.PocketNotifyConfig(key="a", label="A", module=m_a, dexscreener_chain_slug="a")
    cfg_b = shadow_notify.PocketNotifyConfig(key="b", label="B", module=m_b, dexscreener_chain_slug="b")
    text_a = shadow_notify.exit_text(cfg_a)
    text_b = shadow_notify.exit_text(cfg_b)
    assert text_a != text_b
    assert "+25%" in text_a and "-20%" in text_a
    assert "+40%" in text_b and "-8%" in text_b


@pytest.mark.asyncio
async def test_liquidity_warning_wording_depends_on_calibration_flag(tmp_path):
    m = _fake_module(tmp_path, "c")
    await _init_db(m.DB_PATH)
    cfg_calibrated = shadow_notify.PocketNotifyConfig(
        key="c", label="ROBINHOOD", module=m, dexscreener_chain_slug="robinhood",
        liquidity_floor_usd=4000.0, liquidity_floor_calibrated=True,
    )
    cfg_borrowed = shadow_notify.PocketNotifyConfig(
        key="c", label="BASE", module=m, dexscreener_chain_slug="base",
        liquidity_floor_usd=4000.0, liquidity_floor_calibrated=False,
    )
    rec = _Recorder()
    await shadow_notify.notify_pocket(cfg_calibrated, "open", send=rec.send)  # anchor
    await _insert_row(m.DB_PATH, pool_address="p1", symbol="AAA", entry_price=1.0,
                       detected_at=datetime.now(timezone.utc).isoformat(), reserve_usd=100.0)
    rec2 = _Recorder()
    await shadow_notify.notify_pocket(cfg_calibrated, "open", send=rec2.send)
    assert "PnL non executable" in rec2.messages[0]

    cfg_borrowed_state_key = "c2"
    cfg_borrowed = shadow_notify.PocketNotifyConfig(
        key=cfg_borrowed_state_key, label="BASE", module=m, dexscreener_chain_slug="base",
        liquidity_floor_usd=4000.0, liquidity_floor_calibrated=False,
    )
    rec3 = _Recorder()
    await shadow_notify.notify_pocket(cfg_borrowed, "open", send=rec3.send)  # anchor
    await _insert_row(m.DB_PATH, pool_address="p2", symbol="BBB", entry_price=1.0,
                       detected_at=datetime.now(timezone.utc).isoformat(), reserve_usd=100.0)
    rec4 = _Recorder()
    await shadow_notify.notify_pocket(cfg_borrowed, "open", send=rec4.send)
    assert "non recalibre" in rec4.messages[0]


@pytest.mark.asyncio
async def test_close_dedups_by_id_never_double_notifies(tmp_path):
    m = _fake_module(tmp_path, "d")
    await _init_db(m.DB_PATH)
    cfg = shadow_notify.PocketNotifyConfig(key="d", label="D", module=m, dexscreener_chain_slug="d")
    now = datetime.now(timezone.utc)
    row_id = await _insert_row(
        m.DB_PATH, pool_address="p1", symbol="AAA", entry_price=1.0,
        detected_at=(now - timedelta(minutes=5)).isoformat(),
        exit_reason="trailing_stop", final_multiplier=1.4,
        realized_proceeds=140.0, last_checked_at=now.isoformat(),
    )
    rec = _Recorder()
    await shadow_notify.notify_pocket(cfg, "close", send=rec.send)
    await shadow_notify.notify_pocket(cfg, "close", send=rec.send)
    assert len(rec.messages) == 1
    assert "CLOTURE" in rec.messages[0]
    assert "PnL: +40.0%" in rec.messages[0]


@pytest.mark.asyncio
async def test_notify_failure_never_raises(tmp_path):
    m = _fake_module(tmp_path, "e")
    # DB never initialized -- every query fails; notify_pocket must swallow it.
    cfg = shadow_notify.PocketNotifyConfig(key="e", label="E", module=m, dexscreener_chain_slug="e")
    rec = _Recorder()
    await shadow_notify.notify_pocket(cfg, "open", send=rec.send)
    assert rec.messages == []
