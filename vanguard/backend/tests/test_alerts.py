import pytest

from app.database import has_recent_alert, init_db, save_alert


@pytest.mark.asyncio
async def test_has_recent_alert_dedup(tmp_path, monkeypatch):
    db = tmp_path / "dexpulse.db"
    monkeypatch.setattr("app.database.DB_PATH", str(db))
    await init_db()

    await save_alert(
        chain_id="ethereum",
        pair_address="0xabc",
        symbol="TEST",
        signal_type="buy",
        score=75.0,
        timeframe="1h",
        message="test alert",
    )

    assert await has_recent_alert("ethereum", "0xabc", "buy", "1h", within_hours=4) is True
    assert await has_recent_alert("ethereum", "0xabc", "sell", "1h", within_hours=4) is False
    assert await has_recent_alert("ethereum", "0xdef", "buy", "1h", within_hours=4) is False