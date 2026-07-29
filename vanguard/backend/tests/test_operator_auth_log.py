import pytest

from app.auth import operator_auth_log as oal


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "auth_operator_log_test.db"
    monkeypatch.setattr(oal, "DB_PATH", str(db))
    yield


@pytest.mark.asyncio
async def test_record_and_list_one_event():
    await oal.record_event(
        event_type=oal.EVENT_LOGIN_SUCCESS, username="op", ip="1.2.3.4", installation_id="dev-1",
    )
    events = await oal.list_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "login_success"
    assert events[0]["username"] == "op"


@pytest.mark.asyncio
async def test_events_most_recent_first():
    await oal.record_event(event_type=oal.EVENT_LOGIN_FAILURE, username="op")
    await oal.record_event(event_type=oal.EVENT_LOGIN_SUCCESS, username="op")
    events = await oal.list_events()
    assert [e["event_type"] for e in events] == ["login_success", "login_failure"]


@pytest.mark.asyncio
async def test_list_events_respects_limit():
    for _ in range(5):
        await oal.record_event(event_type=oal.EVENT_LOGOUT, username="op")
    events = await oal.list_events(limit=2)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_record_event_never_raises_on_db_failure(monkeypatch):
    async def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(oal, "_ensure_table", _boom)
    await oal.record_event(event_type=oal.EVENT_LOGIN_SUCCESS, username="op")  # must not raise
