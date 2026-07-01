import pytest

from app.games.scores import get_score, leaderboard, upsert_score


@pytest.mark.asyncio
async def test_game_score_upsert_max(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr("app.games.scores.DB_PATH", str(db))

    s1 = await upsert_score(
        privy_did="did:privy:a",
        site_slug="kikou",
        game_id="snake",
        score=10,
        better="max",
    )
    assert s1 == 10

    s2 = await upsert_score(
        privy_did="did:privy:a",
        site_slug="kikou",
        game_id="snake",
        score=5,
        better="max",
    )
    assert s2 == 10

    s3 = await upsert_score(
        privy_did="did:privy:a",
        site_slug="kikou",
        game_id="snake",
        score=15,
        better="max",
    )
    assert s3 == 15

    got = await get_score(privy_did="did:privy:a", site_slug="kikou", game_id="snake")
    assert got == 15


@pytest.mark.asyncio
async def test_game_score_upsert_min(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr("app.games.scores.DB_PATH", str(db))

    await upsert_score(
        privy_did="did:privy:b",
        site_slug="kikou",
        game_id="memory",
        score=40,
        better="min",
    )
    kept = await upsert_score(
        privy_did="did:privy:b",
        site_slug="kikou",
        game_id="memory",
        score=50,
        better="min",
    )
    assert kept == 40

    best = await upsert_score(
        privy_did="did:privy:b",
        site_slug="kikou",
        game_id="memory",
        score=32,
        better="min",
    )
    assert best == 32


@pytest.mark.asyncio
async def test_leaderboard_order(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    monkeypatch.setattr("app.auth.access_code._DB_FILE", db)
    monkeypatch.setattr("app.auth.access_code.DB_PATH", str(db))
    monkeypatch.setattr("app.games.scores.DB_PATH", str(db))

    await upsert_score(privy_did="did:1", site_slug="kikou", game_id="snake", score=5, better="max")
    await upsert_score(privy_did="did:2", site_slug="kikou", game_id="snake", score=20, better="max")
    await upsert_score(privy_did="did:3", site_slug="kikou", game_id="snake", score=12, better="max")

    rows = await leaderboard(site_slug="kikou", game_id="snake", limit=5)
    assert [r["score"] for r in rows] == [20, 12, 5]