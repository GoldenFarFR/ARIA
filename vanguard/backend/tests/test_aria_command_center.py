"""Centre de commandement (10/07) : calibration/ventilation ajoutées à /track-record,
+ /market-cycle et /sentiment PUBLICS. Jamais un contrat candidat exposé -- agrégats
seulement (même doctrine que /track-record existant)."""
import pytest


@pytest.mark.asyncio
async def test_track_record_includes_calibration_and_by_strategy(tmp_path, monkeypatch):
    from aria_core import screened_pool, vc_predictions
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(screened_pool, "DB_PATH", str(tmp_path / "pool.db"))
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "wallet.db"))

    pid = await vc_predictions.record_prediction(
        contract="0xabc", recommandation="BUY", potentiel=8, risque="MODÉRÉ",
        taille_pct=5.0, security_score=70, llm_used=True, strategy="vc",
    )
    await vc_predictions.close_prediction(pid, outcome_pct=25.0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/track-record")

    assert res.status_code == 200
    data = res.json()
    assert "calibration" in data
    assert "by_strategy" in data
    assert data["by_strategy"]["vc"]["buy_count"] == 1


@pytest.mark.asyncio
async def test_market_cycle_endpoint_degrades_softly_without_data(monkeypatch):
    from aria_core.skills import btc_cycles
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async def fake_phase(*, client=None, force_refresh=False):
        return None

    monkeypatch.setattr(btc_cycles, "fetch_current_macro_phase", fake_phase)
    # Le route importe la fonction localement à chaque appel -- patcher le module suffit
    # car `from aria_core.skills.btc_cycles import fetch_current_macro_phase` est fait
    # DANS le handler (import différé), donc résout le nom au moment de l'appel.

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/market-cycle")

    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert data["phase"] is None


@pytest.mark.asyncio
async def test_sentiment_endpoint_reads_persisted_readings_only(tmp_path, monkeypatch):
    from aria_core.skills import market_sentiment
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(market_sentiment, "DB_PATH", str(tmp_path / "sentiment.db"))
    reading = market_sentiment.SentimentReading(
        pair="BTC", regime="euphorie", detail="test", rsi=80.0, bollinger_position=1.1,
        momentum_pct=10.0, drawdown_from_high_pct=0.0, rally_from_low_pct=40.0, trend_up=True,
    )
    await market_sentiment.upsert_reading(reading)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/sentiment")

    assert res.status_code == 200
    data = res.json()
    assert data["readings"][0]["pair"] == "BTC"
    assert data["readings"][0]["regime"] == "euphorie"
    assert "regime_labels" in data


@pytest.mark.asyncio
async def test_sentiment_endpoint_empty_without_heartbeat_run(tmp_path, monkeypatch):
    from aria_core.skills import market_sentiment
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(market_sentiment, "DB_PATH", str(tmp_path / "sentiment_empty.db"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/sentiment")

    assert res.status_code == 200
    assert res.json()["readings"] == []
