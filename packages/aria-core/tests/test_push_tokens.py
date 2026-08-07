import pytest

from aria_core.push_tokens import (
    list_push_tokens,
    register_push_token,
    unregister_push_token,
)


@pytest.mark.asyncio
async def test_register_and_list():
    await register_push_token("ExponentPushToken[aaa]", installation_id="dev-1")
    tokens = await list_push_tokens()
    assert "ExponentPushToken[aaa]" in tokens


@pytest.mark.asyncio
async def test_register_is_idempotent_upsert():
    await register_push_token("ExponentPushToken[bbb]", installation_id="dev-1")
    await register_push_token("ExponentPushToken[bbb]", installation_id="dev-1")
    tokens = await list_push_tokens()
    assert tokens.count("ExponentPushToken[bbb]") == 1


@pytest.mark.asyncio
async def test_unregister_removes_token():
    await register_push_token("ExponentPushToken[ccc]")
    await unregister_push_token("ExponentPushToken[ccc]")
    tokens = await list_push_tokens()
    assert "ExponentPushToken[ccc]" not in tokens


@pytest.mark.asyncio
async def test_register_blank_token_is_noop():
    await register_push_token("   ")
    tokens = await list_push_tokens()
    assert tokens == []
