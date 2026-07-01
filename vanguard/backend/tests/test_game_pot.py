import pytest

from app.games.pot import get_pot_status, register_deposit


@pytest.mark.asyncio
async def test_pot_round_and_deposit(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr("app.games.pot.DB_PATH", str(db))
    monkeypatch.setattr("app.games.scores.DB_PATH", str(db))

    status = await get_pot_status(site_slug="kikou")
    assert status["pot_usdc"] == "0.00"
    assert status["entries"] == 0

    updated = await register_deposit(
        site_slug="kikou",
        wallet="0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        tx_hash="0x1111111111111111111111111111111111111111111111111111111111111111",
        privy_did="did:privy:test",
    )
    assert updated["entries"] == 1
    assert updated["pot_usdc"] == "0.10"

    again = await register_deposit(
        site_slug="kikou",
        wallet="0xAbCdEf0123456789AbCdEf0123456789AbCdEf01",
        tx_hash="0x1111111111111111111111111111111111111111111111111111111111111111",
        privy_did="did:privy:test",
    )
    assert again["entries"] == 1