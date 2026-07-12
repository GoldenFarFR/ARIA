import pytest


@pytest.mark.asyncio
async def test_paper_wallet_redacts_contract_and_exposes_aggregates(tmp_path, monkeypatch):
    from aria_core import paper_trader
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))

    await paper_trader.open_position("0xWinner", "WINR", 1.0, alloc_usd=1_000.0)
    await paper_trader.close_position("0xWinner", 2.0, reason="cible atteinte")

    await paper_trader.open_position("0xLoser", "LOSR", 1.0, alloc_usd=1_000.0)
    await paper_trader.close_position("0xLoser", 0.5, reason="invalidation")

    await paper_trader.open_position("0xOpen", "OPEN", 1.0, alloc_usd=1_000.0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/paper-wallet")

    assert res.status_code == 200
    data = res.json()

    assert data["closed_trades"] == 2
    assert data["open_positions"] == 1
    assert data["win_rate"] == 50.0

    assert len(data["history"]) == 2
    symbols = {h["symbol"] for h in data["history"]}
    assert symbols == {"WINR", "LOSR"}
    outcomes = {h["symbol"]: h["outcome"] for h in data["history"]}
    assert outcomes["WINR"] == "win"
    assert outcomes["LOSR"] == "loss"

    body = res.text
    assert "0xWinner" not in body
    assert "0xLoser" not in body
    assert "0xOpen" not in body
    assert "cible atteinte" not in body
    assert "invalidation" not in body
    for h in data["history"]:
        assert "contract" not in h
        assert "entry_price" not in h
        assert "exit_price" not in h
        assert "close_reason" not in h
        assert "qty" not in h
        assert "cost_usd" not in h


@pytest.mark.asyncio
async def test_paper_wallet_zero_positions_is_facts_only(tmp_path, monkeypatch):
    from aria_core import paper_trader
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper_empty.db"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/paper-wallet")

    assert res.status_code == 200
    data = res.json()
    assert data["open_positions"] == 0
    assert data["closed_trades"] == 0
    assert data["return_pct"] == 0.0
    assert data["win_rate"] is None
    assert data["history"] == []
