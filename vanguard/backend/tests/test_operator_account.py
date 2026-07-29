"""Operator mobile account (Item #201) -- dedicated login for the Android fallback
channel. No hard lockout by design: a fallback channel must never be lockable by
an attacker who merely guesses the single username, right when the operator needs
it (see the module's own docstring)."""
import pytest

from app.auth import operator_account as oa


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "auth_operator_test.db"
    monkeypatch.setattr(oa, "DB_PATH", str(db))
    yield


@pytest.mark.asyncio
async def test_create_account_hashes_password_never_stores_plaintext():
    account_id = await oa.create_or_replace_account(
        username="op", password="s3cret-phrase", totp_secret="JBSWY3DPEHPK3PXP",
    )
    assert account_id > 0
    account = await oa.get_account("op")
    assert account is not None
    assert account["password_hash"] != "s3cret-phrase"
    assert "s3cret-phrase" not in account["password_hash"]
    assert account["role"] == "owner"
    assert account["failed_attempts"] == 0


@pytest.mark.asyncio
async def test_verify_password_correct_and_incorrect():
    await oa.create_or_replace_account(username="op", password="right-pw", totp_secret="X")
    account = await oa.get_account("op")
    assert oa.verify_password(account, "right-pw") is True
    assert oa.verify_password(account, "wrong-pw") is False


@pytest.mark.asyncio
async def test_get_account_by_id_matches_get_account():
    account_id = await oa.create_or_replace_account(username="op", password="pw", totp_secret="X")
    by_username = await oa.get_account("op")
    by_id = await oa.get_account_by_id(account_id)
    assert by_id == by_username


@pytest.mark.asyncio
async def test_get_account_unknown_returns_none():
    assert await oa.get_account("nobody") is None
    assert await oa.get_account_by_id(999) is None


@pytest.mark.asyncio
async def test_re_enrollment_replaces_account_and_resets_failures():
    account_id_1 = await oa.create_or_replace_account(username="op", password="pw1", totp_secret="X")
    await oa.record_login_failure(account_id_1)
    await oa.record_login_failure(account_id_1)
    account = await oa.get_account("op")
    assert account["failed_attempts"] == 2

    account_id_2 = await oa.create_or_replace_account(username="op", password="pw2", totp_secret="Y")
    assert account_id_2 == account_id_1  # same row, re-enrolled -- not a duplicate
    account = await oa.get_account("op")
    assert account["failed_attempts"] == 0
    assert oa.verify_password(account, "pw1") is False
    assert oa.verify_password(account, "pw2") is True


@pytest.mark.asyncio
async def test_record_login_failure_increments_never_locks():
    account_id = await oa.create_or_replace_account(username="op", password="pw", totp_secret="X")
    for _ in range(20):
        await oa.record_login_failure(account_id)
    account = await oa.get_account_by_id(account_id)
    assert account["failed_attempts"] == 20
    # No lockout field exists at all -- verify_password must still work regardless
    # of how many failures were recorded (never a hard lock on this account).
    assert oa.verify_password(account, "pw") is True


@pytest.mark.asyncio
async def test_record_login_success_resets_failures_and_sets_last_login():
    account_id = await oa.create_or_replace_account(username="op", password="pw", totp_secret="X")
    await oa.record_login_failure(account_id)
    await oa.record_login_failure(account_id)
    await oa.record_login_success(account_id)
    account = await oa.get_account_by_id(account_id)
    assert account["failed_attempts"] == 0
    assert account["last_login_at"] is not None


@pytest.mark.asyncio
async def test_reset_failed_attempts_via_username():
    account_id = await oa.create_or_replace_account(username="op", password="pw", totp_secret="X")
    await oa.record_login_failure(account_id)
    ok = await oa.reset_failed_attempts("op")
    assert ok is True
    account = await oa.get_account_by_id(account_id)
    assert account["failed_attempts"] == 0


@pytest.mark.asyncio
async def test_reset_failed_attempts_unknown_username_returns_false():
    assert await oa.reset_failed_attempts("ghost") is False


@pytest.mark.asyncio
async def test_replace_password_resets_failures():
    account_id = await oa.create_or_replace_account(username="op", password="old-pw", totp_secret="X")
    await oa.record_login_failure(account_id)
    ok = await oa.replace_password("op", "new-pw")
    assert ok is True
    account = await oa.get_account("op")
    assert account["failed_attempts"] == 0
    assert oa.verify_password(account, "old-pw") is False
    assert oa.verify_password(account, "new-pw") is True


@pytest.mark.asyncio
async def test_replace_password_unknown_username_returns_false():
    assert await oa.replace_password("ghost", "pw") is False


@pytest.mark.asyncio
async def test_replace_totp_secret():
    await oa.create_or_replace_account(username="op", password="pw", totp_secret="OLD")
    ok = await oa.replace_totp_secret("op", "NEW")
    assert ok is True
    account = await oa.get_account("op")
    assert account["totp_secret"] == "NEW"


@pytest.mark.asyncio
async def test_replace_totp_secret_unknown_username_returns_false():
    assert await oa.replace_totp_secret("ghost", "X") is False


def test_login_delay_seconds_capped_progressive():
    assert oa.login_delay_seconds(0) == 0.0
    assert oa.login_delay_seconds(1) == 2.0
    assert oa.login_delay_seconds(2) == 4.0
    assert oa.login_delay_seconds(3) == 8.0
    # Never an unbounded doubling -- must stay capped even after many failures.
    assert oa.login_delay_seconds(10) == 8.0
    assert oa.login_delay_seconds(1000) == 8.0
