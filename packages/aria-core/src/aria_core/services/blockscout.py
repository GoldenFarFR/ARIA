"""Client de lecture seule Blockscout (Base) — « yeux on-chain » d'ARIA.

Aucune écriture, aucune signature, aucun appel autre que GET. Politique
d'erreurs définie dans AGENTS.md :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- Aucune donnée manquante n'est jamais remplacée par une supposition — le
  champ `error` (et `available=False`) porte l'absence de donnée.
- Échecs consécutifs répétés (>3) : logué, jamais bloquant, jamais de spam Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://base.blockscout.com/api/v2"

UNAVAILABLE = "donnée on-chain indisponible"

_SENSITIVE_FUNCTION_NAMES = {
    "mint": ("mint",),
    "disable_transfers": ("disabletransfers", "disabletransfer", "transfersdisabled", "stoptrading"),
    "blacklist": ("blacklist", "blocklist", "isblacklisted", "addblacklist"),
}

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class AddressInfo:
    address: str
    is_contract: bool | None = None
    is_verified: bool | None = None
    contract_name: str | None = None
    balance_wei: str | None = None
    balance_native: float | None = None
    available: bool = False
    error: str | None = None


@dataclass
class TokenTransfer:
    tx_hash: str
    from_address: str
    to_address: str
    token_address: str | None
    token_symbol: str | None
    token_name: str | None
    amount: float | None
    timestamp: str | None
    method: str | None = None


@dataclass
class Transaction:
    tx_hash: str
    from_address: str
    to_address: str | None
    value_native: float | None
    status: str | None
    method: str | None
    timestamp: str | None
    block_number: int | None = None


@dataclass
class TokenHolder:
    address: str
    balance: float | None
    percentage: float | None


@dataclass
class TokenTransfersResult:
    transfers: list[TokenTransfer] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class TransactionsResult:
    transactions: list[Transaction] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class TokenHoldersResult:
    holders: list[TokenHolder] = field(default_factory=list)
    total_supply: float | None = None
    available: bool = True
    error: str | None = None


@dataclass
class ContractFlags:
    address: str
    is_verified: bool | None = None
    contract_name: str | None = None
    has_mint: bool | None = None
    has_disable_transfers: bool | None = None
    has_blacklist: bool | None = None
    available: bool = False
    error: str | None = None


class BlockscoutClient:
    """Client HTTP async, lecture seule, throttle modéré (API publique sans clé)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 0.35) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "blockscout: %s echecs consecutifs (dernier: %s) — pas de blocage, pas d'escalade",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info("blockscout: echec appel (%s/%s) — %s", self._consecutive_failures, _FAIL_STREAK_WARN_THRESHOLD, detail)

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET avec la politique d'erreurs AGENTS.md. Retourne (data, error)."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Blockscout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Blockscout)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Blockscout)"

            if response.status_code == 404:
                self._record_success()
                return None, "adresse ou contrat introuvable"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    # ------------------------------------------------------------------
    # 1. Info adresse (balance, contrat, verification, nom)
    # ------------------------------------------------------------------
    async def get_address_info(self, address: str) -> AddressInfo:
        data, error = await self._get_json(f"/addresses/{address}")
        if error is not None:
            return AddressInfo(address=address, available=False, error=error)
        if not isinstance(data, dict):
            return AddressInfo(address=address, available=False, error=UNAVAILABLE)

        balance_wei = data.get("coin_balance")
        balance_native = None
        if balance_wei is not None:
            try:
                balance_native = int(balance_wei) / 1e18
            except (TypeError, ValueError):
                balance_native = None

        return AddressInfo(
            address=address,
            is_contract=bool(data.get("is_contract")),
            is_verified=data.get("is_verified"),
            contract_name=data.get("name"),
            balance_wei=str(balance_wei) if balance_wei is not None else None,
            balance_native=balance_native,
            available=True,
            error=None,
        )

    # ------------------------------------------------------------------
    # 2. Transferts de tokens (qui paie qui, quels tokens, montants)
    # ------------------------------------------------------------------
    async def get_token_transfers(self, address: str, limit: int = 50) -> TokenTransfersResult:
        data, error = await self._get_json(f"/addresses/{address}/token-transfers")
        if error is not None:
            return TokenTransfersResult(available=False, error=error)
        if not isinstance(data, dict):
            return TokenTransfersResult(available=False, error=UNAVAILABLE)

        items = data.get("items") or []
        transfers: list[TokenTransfer] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or {}
            total = item.get("total") or {}
            amount = None
            raw_value = total.get("value")
            decimals = total.get("decimals")
            if raw_value is not None:
                try:
                    decimals_int = int(decimals) if decimals is not None else 0
                    amount = int(raw_value) / (10**decimals_int)
                except (TypeError, ValueError):
                    amount = None

            transfers.append(
                TokenTransfer(
                    tx_hash=str(item.get("tx_hash") or item.get("transaction_hash") or ""),
                    from_address=str((item.get("from") or {}).get("hash") or ""),
                    to_address=str((item.get("to") or {}).get("hash") or ""),
                    token_address=token.get("address"),
                    token_symbol=token.get("symbol"),
                    token_name=token.get("name"),
                    amount=amount,
                    timestamp=item.get("timestamp"),
                    method=item.get("method"),
                )
            )
        return TokenTransfersResult(transfers=transfers, available=True, error=None)

    # ------------------------------------------------------------------
    # 3. Historique des transactions
    # ------------------------------------------------------------------
    async def get_transactions(self, address: str, limit: int = 50) -> TransactionsResult:
        data, error = await self._get_json(f"/addresses/{address}/transactions")
        if error is not None:
            return TransactionsResult(available=False, error=error)
        if not isinstance(data, dict):
            return TransactionsResult(available=False, error=UNAVAILABLE)

        items = data.get("items") or []
        transactions: list[Transaction] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            value_native = None
            raw_value = item.get("value")
            if raw_value is not None:
                try:
                    value_native = int(raw_value) / 1e18
                except (TypeError, ValueError):
                    value_native = None

            to_field = item.get("to")
            transactions.append(
                Transaction(
                    tx_hash=str(item.get("hash") or ""),
                    from_address=str((item.get("from") or {}).get("hash") or ""),
                    to_address=(to_field or {}).get("hash") if isinstance(to_field, dict) else None,
                    value_native=value_native,
                    status=item.get("status"),
                    method=item.get("method"),
                    timestamp=item.get("timestamp"),
                    block_number=item.get("block_number"),
                )
            )
        return TransactionsResult(transactions=transactions, available=True, error=None)

    # ------------------------------------------------------------------
    # 4. Distribution des holders (top holders, %)
    # ------------------------------------------------------------------
    async def get_token_holders(self, token_address: str) -> TokenHoldersResult:
        token_data, token_error = await self._get_json(f"/tokens/{token_address}")
        if token_error is not None:
            return TokenHoldersResult(available=False, error=token_error)
        if not isinstance(token_data, dict):
            return TokenHoldersResult(available=False, error=UNAVAILABLE)

        decimals_raw = token_data.get("decimals")
        try:
            decimals = int(decimals_raw) if decimals_raw is not None else 0
        except (TypeError, ValueError):
            decimals = 0

        total_supply_raw = token_data.get("total_supply")
        total_supply = None
        if total_supply_raw is not None:
            try:
                total_supply = int(total_supply_raw) / (10**decimals)
            except (TypeError, ValueError):
                total_supply = None

        holders_data, holders_error = await self._get_json(f"/tokens/{token_address}/holders")
        if holders_error is not None:
            return TokenHoldersResult(total_supply=total_supply, available=False, error=holders_error)
        if not isinstance(holders_data, dict):
            return TokenHoldersResult(total_supply=total_supply, available=False, error=UNAVAILABLE)

        holders: list[TokenHolder] = []
        for item in holders_data.get("items") or []:
            if not isinstance(item, dict):
                continue
            raw_balance = item.get("value")
            balance = None
            if raw_balance is not None:
                try:
                    balance = int(raw_balance) / (10**decimals)
                except (TypeError, ValueError):
                    balance = None

            percentage = None
            if balance is not None and total_supply:
                percentage = (balance / total_supply) * 100

            holder_address = item.get("address")
            holders.append(
                TokenHolder(
                    address=str((holder_address or {}).get("hash") if isinstance(holder_address, dict) else holder_address or ""),
                    balance=balance,
                    percentage=percentage,
                )
            )

        return TokenHoldersResult(holders=holders, total_supply=total_supply, available=True, error=None)

    # ------------------------------------------------------------------
    # 5. is_verified + scan fonctions sensibles (mint, disable_transfers, blacklist)
    # ------------------------------------------------------------------
    async def check_contract_flags(self, token_address: str) -> ContractFlags:
        data, error = await self._get_json(f"/smart-contracts/{token_address}")
        if error is not None:
            return ContractFlags(address=token_address, available=False, error=error)
        if not isinstance(data, dict):
            return ContractFlags(address=token_address, available=False, error=UNAVAILABLE)

        is_verified = bool(data.get("is_verified"))
        contract_name = data.get("name")

        if not is_verified:
            return ContractFlags(
                address=token_address,
                is_verified=False,
                contract_name=contract_name,
                has_mint=None,
                has_disable_transfers=None,
                has_blacklist=None,
                available=True,
                error="contrat non vérifié — scan des fonctions sensibles impossible",
            )

        function_names: set[str] = set()
        for entry in data.get("abi") or []:
            if isinstance(entry, dict) and entry.get("type") == "function" and entry.get("name"):
                function_names.add(str(entry["name"]).lower())

        source_code = str(data.get("source_code") or "").lower()

        def _has_flag(aliases: tuple[str, ...]) -> bool:
            normalized_names = {name.replace("_", "") for name in function_names}
            if any(alias in normalized_names for alias in aliases):
                return True
            return any(alias in source_code for alias in aliases)

        return ContractFlags(
            address=token_address,
            is_verified=True,
            contract_name=contract_name,
            has_mint=_has_flag(_SENSITIVE_FUNCTION_NAMES["mint"]),
            has_disable_transfers=_has_flag(_SENSITIVE_FUNCTION_NAMES["disable_transfers"]),
            has_blacklist=_has_flag(_SENSITIVE_FUNCTION_NAMES["blacklist"]),
            available=True,
            error=None,
        )


blockscout_client = BlockscoutClient()
