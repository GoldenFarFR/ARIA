import pytest

from aria_core import x402_product_health as health


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DB_PATH", str(tmp_path / "health.db"))


@pytest.mark.asyncio
async def test_success_rate_with_no_attempts_returns_none_rate():
    result = await health.success_rate("wallet_score")
    assert result == {"attempts": 0, "successes": 0, "rate_pct": None}


@pytest.mark.asyncio
async def test_record_attempt_and_success_rate_basic():
    await health.record_attempt("wallet_score", "success")
    await health.record_attempt("wallet_score", "success")
    await health.record_attempt("wallet_score", "no_result")
    await health.record_attempt("wallet_score", "error")

    result = await health.success_rate("wallet_score")
    assert result == {"attempts": 4, "successes": 2, "rate_pct": 50.0}


@pytest.mark.asyncio
async def test_success_rate_is_scoped_per_product():
    await health.record_attempt("wallet_score", "success")
    await health.record_attempt("b20_safety", "error")
    await health.record_attempt("b20_safety", "error")

    wallet_result = await health.success_rate("wallet_score")
    b20_result = await health.success_rate("b20_safety")

    assert wallet_result == {"attempts": 1, "successes": 1, "rate_pct": 100.0}
    assert b20_result == {"attempts": 2, "successes": 0, "rate_pct": 0.0}


@pytest.mark.asyncio
async def test_success_rate_respects_window_most_recent_first():
    for _ in range(3):
        await health.record_attempt("wallet_score", "error")
    for _ in range(2):
        await health.record_attempt("wallet_score", "success")

    result = await health.success_rate("wallet_score", window=2)
    assert result == {"attempts": 2, "successes": 2, "rate_pct": 100.0}


@pytest.mark.asyncio
async def test_record_attempt_rejects_invalid_outcome():
    with pytest.raises(ValueError):
        await health.record_attempt("wallet_score", "maybe")
