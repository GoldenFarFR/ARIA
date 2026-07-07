"""Tests du client Blockscout (lecture seule) — aucun appel réseau réel, tout est mocké."""

import pytest

from aria_core.services.blockscout import BlockscoutClient, UNAVAILABLE


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    """Simule httpx.AsyncClient: renvoie la prochaine réponse d'une file par URL."""

    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.blockscout.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.blockscout.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_get_address_info_success(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xabc"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "is_contract": True,
                    "is_verified": True,
                    "name": "MyToken",
                    "coin_balance": str(2 * 10**18),
                },
            )
        },
    )

    info = await client.get_address_info("0xabc")

    assert info.available is True
    assert info.error is None
    assert info.is_contract is True
    assert info.is_verified is True
    assert info.contract_name == "MyToken"
    assert info.balance_native == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_get_address_info_not_found(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xdead"
    _patch_client(monkeypatch, {url: FakeResponse(404, None)})

    info = await client.get_address_info("0xdead")

    assert info.available is False
    assert "introuvable" in info.error


@pytest.mark.asyncio
async def test_get_token_transfers_parses_amounts(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xabc/token-transfers"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "items": [
                        {
                            "tx_hash": "0x1",
                            "from": {"hash": "0xfrom"},
                            "to": {"hash": "0xto"},
                            "token": {"address": "0xtok", "symbol": "AAA", "name": "TokenA"},
                            "total": {"value": str(5 * 10**18), "decimals": "18"},
                            "timestamp": "2026-07-06T00:00:00Z",
                            "method": "transfer",
                        }
                    ]
                },
            )
        },
    )

    result = await client.get_token_transfers("0xabc", limit=50)

    assert result.available is True
    assert len(result.transfers) == 1
    transfer = result.transfers[0]
    assert transfer.from_address == "0xfrom"
    assert transfer.to_address == "0xto"
    assert transfer.token_symbol == "AAA"
    assert transfer.amount == pytest.approx(5.0)
    assert transfer.error is None


@pytest.mark.asyncio
async def test_get_token_transfers_missing_decimals_no_zero_assumption(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xabc/token-transfers"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "items": [
                        {
                            "tx_hash": "0x1",
                            "from": {"hash": "0xfrom"},
                            "to": {"hash": "0xto"},
                            "token": {"address": "0xtok", "symbol": "AAA", "name": "TokenA"},
                            "total": {"value": str(5 * 10**18)},
                            "timestamp": "2026-07-06T00:00:00Z",
                            "method": "transfer",
                        }
                    ]
                },
            )
        },
    )

    result = await client.get_token_transfers("0xabc", limit=50)

    assert result.available is True
    assert len(result.transfers) == 1
    transfer = result.transfers[0]
    assert transfer.amount is None
    assert transfer.error == "décimales du token indisponible"


@pytest.mark.asyncio
async def test_get_transactions_parses_items(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xabc/transactions"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "items": [
                        {
                            "hash": "0xtx1",
                            "from": {"hash": "0xfrom"},
                            "to": {"hash": "0xto"},
                            "value": str(1 * 10**18),
                            "status": "ok",
                            "method": "transfer",
                            "timestamp": "2026-07-06T00:00:00Z",
                            "block_number": 123,
                        }
                    ]
                },
            )
        },
    )

    result = await client.get_transactions("0xabc")

    assert result.available is True
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.tx_hash == "0xtx1"
    assert tx.value_native == pytest.approx(1.0)
    assert tx.block_number == 123


@pytest.mark.asyncio
async def test_get_token_holders_computes_percentage(monkeypatch):
    client = BlockscoutClient()
    token_url = f"{client.base_url}/tokens/0xtok"
    holders_url = f"{client.base_url}/tokens/0xtok/holders"
    _patch_client(
        monkeypatch,
        {
            token_url: FakeResponse(200, {"decimals": "18", "total_supply": str(1000 * 10**18)}),
            holders_url: FakeResponse(
                200,
                {
                    "items": [
                        {"address": {"hash": "0xwhale"}, "value": str(100 * 10**18)},
                        {"address": {"hash": "0xsmall"}, "value": str(1 * 10**18)},
                    ]
                },
            ),
        },
    )

    result = await client.get_token_holders("0xtok")

    assert result.available is True
    assert result.error is None
    assert result.total_supply == pytest.approx(1000.0)
    assert len(result.holders) == 2
    assert result.holders[0].address == "0xwhale"
    assert result.holders[0].percentage == pytest.approx(10.0)
    assert result.holders[1].percentage == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_get_token_holders_missing_decimals_no_zero_assumption(monkeypatch):
    client = BlockscoutClient()
    token_url = f"{client.base_url}/tokens/0xtok"
    holders_url = f"{client.base_url}/tokens/0xtok/holders"
    _patch_client(
        monkeypatch,
        {
            token_url: FakeResponse(200, {"total_supply": str(1000 * 10**18)}),
            holders_url: FakeResponse(
                200,
                {
                    "items": [
                        {"address": {"hash": "0xwhale"}, "value": str(100 * 10**18)},
                    ]
                },
            ),
        },
    )

    result = await client.get_token_holders("0xtok")

    assert result.available is True
    assert result.total_supply is None
    assert result.error == "décimales du token indisponible"
    assert len(result.holders) == 1
    assert result.holders[0].balance is None
    assert result.holders[0].percentage is None


@pytest.mark.asyncio
async def test_check_contract_flags_verified_with_mint(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/smart-contracts/0xtok"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "is_verified": True,
                    "name": "SusToken",
                    # Détection basée sur l'ABI (fonctions réellement appelables),
                    # pas sur le code source : mint + blacklist externes = vrais pouvoirs.
                    "abi": [
                        {"type": "function", "name": "mint", "stateMutability": "nonpayable"},
                        {"type": "function", "name": "blacklist", "stateMutability": "nonpayable"},
                        {"type": "function", "name": "transfer", "stateMutability": "nonpayable"},
                    ],
                    "source_code": "contract SusToken { }",
                },
            )
        },
    )

    flags = await client.check_contract_flags("0xtok")

    assert flags.available is True
    assert flags.is_verified is True
    assert flags.has_mint is True
    assert flags.has_blacklist is True
    assert flags.has_disable_transfers is False


@pytest.mark.asyncio
async def test_fixed_supply_internal_mint_not_flagged(monkeypatch):
    """Anti-régression : un ERC20 à offre fixe (`_mint` INTERNE au constructeur) ne

    doit PAS être flaggé has_mint. Avant le fix, la sous-chaîne "mint" du code
    source déclenchait un faux positif qui rejetait de bons tokens.
    """
    client = BlockscoutClient()
    url = f"{client.base_url}/smart-contracts/0xfix"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "is_verified": True,
                    "name": "FixedToken",
                    # Aucune fonction mint EXTERNE dans l'ABI (offre fixe).
                    "abi": [
                        {"type": "function", "name": "transfer", "stateMutability": "nonpayable"},
                        {"type": "function", "name": "totalSupply", "stateMutability": "view"},
                    ],
                    # `_mint` interne + un getter mentionnant mint : ne doivent PAS compter.
                    "source_code": "contract FixedToken { constructor(){ _mint(msg.sender, 1e24); } function mintingFinished() external view returns(bool){return true;} }",
                },
            )
        },
    )

    flags = await client.check_contract_flags("0xfix")
    assert flags.available is True
    assert flags.has_mint is False  # plus de faux positif
    assert flags.has_blacklist is False


@pytest.mark.asyncio
async def test_check_contract_flags_unverified_no_guessing(monkeypatch):
    client = BlockscoutClient()
    url = f"{client.base_url}/smart-contracts/0xtok"
    _patch_client(monkeypatch, {url: FakeResponse(200, {"is_verified": False})})

    flags = await client.check_contract_flags("0xtok")

    assert flags.available is True
    assert flags.is_verified is False
    assert flags.has_mint is None
    assert flags.has_disable_transfers is None
    assert flags.has_blacklist is None
    assert "non vérifié" in flags.error


@pytest.mark.asyncio
async def test_rate_limit_gives_up_after_three_attempts(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = BlockscoutClient()
    url = f"{client.base_url}/addresses/0xabc"
    _patch_client(
        monkeypatch,
        {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )

    info = await client.get_address_info("0xabc")

    assert info.available is False
    assert UNAVAILABLE in info.error
    assert "rate limit" in info.error


@pytest.mark.asyncio
async def test_timeout_retries_once_then_fallback(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = BlockscoutClient()

    import httpx

    calls = {"count": 0}

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            calls["count"] += 1
            raise httpx.TimeoutException("boom")

    monkeypatch.setattr(
        "aria_core.services.blockscout.httpx.AsyncClient",
        lambda **kw: TimeoutClient(),
    )

    info = await client.get_address_info("0xabc")

    assert info.available is False
    assert UNAVAILABLE in info.error
    assert calls["count"] == 2
