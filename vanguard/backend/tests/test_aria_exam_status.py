import pytest


@pytest.mark.asyncio
async def test_exam_status_disabled_by_default(tmp_path, monkeypatch):
    from aria_core import exam
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    monkeypatch.setattr(exam, "DB_PATH", str(tmp_path / "exam.db"))
    monkeypatch.delenv("ARIA_EXAM_ENABLED", raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/aria/exam-status")

    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is False
    assert data["program_days"] == 20
    assert data["current_day"] == 1
    assert data["cumulative"]["answered"] == 0
