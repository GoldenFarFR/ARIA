"""Blockscout read-only client — ARIA's "on-chain eyes".

No writes, no signing, no calls other than GET. Error policy defined in
AGENTS.md:
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- Missing data is never replaced by a guess — the `error` field (and
  `available=False`) carries the absence of data.
- Repeated consecutive failures (>3): logged, never blocking, never Telegram spam.

Multi-chain EVM (14/07, wallet-scoring #157 only -- the rest of ARIA stays
Base): Blockscout migrated its legacy "keyless" access to the Pro API (a
free account, a key, ``https://api.blockscout.com/{chain_id}/
api/v2/...``) starting July 1st, 2026. ``base.blockscout.com`` (legacy,
keyless) stays the default as long as no key is configured -- graceful
degradation, zero regression on Base. As soon as ``BLOCKSCOUT_PRO_API_KEY``
is present in the environment, ALL chains (Base included) go through the
Pro API (much higher throughput: 5 req/s vs ~3 req/s in legacy).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

from aria_core.services import blockscout_credit_budget

logger = logging.getLogger(__name__)

BASE_URL = "https://base.blockscout.com/api/v2"
PRO_API_URL = "https://api.blockscout.com"

# EVM chains covered by wallet-scoring (#157, 14/07). Solana is NOT EVM
# (different addresses, no Blockscout) -- separate project, out of scope.
# "bnb" removed (14/07, verified by VPS Research from 3 independent angles:
# absent from the official chains.blockscout.com directory, explicit
# "Network not supported" error on a real call, every community subdomain
# tested returned 404) -- Blockscout doesn't serve BNB Smart Chain, real
# data could never have come back. Avalanche never added for the same
# reason. Real alternative if BNB is wanted one day: BSCTrace via MegaNode
# (separate client, not a CHAIN_IDS entry) -- see
# docs/aria-learning-inbox/2026-07-14-verification-blockscout-bnb-avalanche-non-supportes.md
# Extended (14/07) to the 13 chains confirmed queryable on both sides
# (Blockscout Pro API × GeckoTerminal) -- established that evening for the
# dynamic TVL ranking (#157, services/defillama.py). chain_id = DefiLlama
# `chainId` (verified live, GET https://api.llama.fi/v2/chains, none of the
# 13 missing from the response).
CHAIN_IDS: dict[str, int] = {
    "base": 8453,
    "ethereum": 1,
    "arbitrum": 42161,
    "optimism": 10,
    "polygon": 137,
    "celo": 42220,
    "gnosis": 100,
    "scroll": 534352,
    "zksync": 324,
    "rootstock": 30,
    "unichain": 130,
    "soneium": 1868,
    "mode": 34443,
}

UNAVAILABLE = "donnée on-chain indisponible"


def _pro_api_key() -> str:
    """Blockscout Pro key -- ONLY from the environment (never hardcoded),
    same policy as ``TAVILY_API_KEY`` (see services/tavily.py)."""
    return os.environ.get("BLOCKSCOUT_PRO_API_KEY", "").strip()

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
    creator_address: str | None = None  # deployer (used to recognize a launchpad)
    holders_count: int | None = None  # present if ``address`` is a token (``token.holders_count`` field)
    ens_domain_name: str | None = None  # ENS/Basename name (cosmetic, never a scoring factor)
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
    error: str | None = None


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
    # 22/07 -- deployer reputation (services/deployer_history.py): present as-is
    # in the endpoint's real response (`item.created_contract.hash`, confirmed
    # by a direct call) on a contract-creation tx, ``None`` otherwise -- no
    # extra network cost, the field was just never mapped before this fix.
    created_contract: str | None = None


@dataclass
class TokenHolder:
    address: str
    balance: float | None
    percentage: float | None
    # 19/07 -- Gemini cross review: already present in the REAL response of
    # ``/tokens/{address}/holders`` (verified by a direct call, no extra
    # network cost) -- each holder's ``address`` object carries its own
    # contract/verification status, exactly like ``/addresses/{address}``.
    is_contract: bool | None = None
    is_verified: bool | None = None


@dataclass
class TokenTransfersResult:
    transfers: list[TokenTransfer] = field(default_factory=list)
    available: bool = True
    error: str | None = None
    # 15/07, external review -- wallet-scoring: ``True`` if the Blockscout
    # API STILL had data (``next_page_params`` present) when pagination
    # stopped because of the ``max_pages``/``limit`` cap, never when it
    # stops because the history is genuinely exhausted (``next_page_params``
    # absent). Default ``False`` -- backward compatible with every existing
    # caller (``get_transaction_token_transfers`` is single-page, never
    # affected).
    truncated: bool = False


@dataclass
class TransactionsResult:
    transactions: list[Transaction] = field(default_factory=list)
    available: bool = True
    error: str | None = None


@dataclass
class BoundedTransactionsResult:
    """Result of ``get_transactions_bounded`` -- ``truncated=True`` means the
    page cap was reached WITHOUT exhausting the wallet's real history: any
    conclusion drawn from ``transactions`` (age, funding source) is then a
    BOUND (the wallet is AT LEAST as old as the oldest transaction found
    here), never a guaranteed exact value."""

    transactions: list[Transaction] = field(default_factory=list)
    available: bool = True
    error: str | None = None
    truncated: bool = False


@dataclass
class TokenHoldersResult:
    holders: list[TokenHolder] = field(default_factory=list)
    total_supply: float | None = None
    available: bool = True
    error: str | None = None


@dataclass
class TokenMetadataResult:
    """21/07 -- extracted from ``get_token_holders`` (decimals + total_supply
    only, without the holders list) to let a caller combine this cheap
    metadata (``/tokens/{address}`` endpoint, low volume, holds up well on
    the free fallback) with a DIFFERENT holders source -- e.g.
    ``blockscout_x402.get_token_holders_x402`` (paid, never out of credits),
    see ``momentum_entry._check_holder_concentration``."""

    decimals: int | None = None
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
    """Async HTTP client, read-only, moderate throttle.

    ``chain`` selects the EVM network (``"base"`` by default, unchanged
    historical behavior). If ``BLOCKSCOUT_PRO_API_KEY`` is configured, goes
    through the Pro API (multi-chain, 5 req/s); otherwise, graceful
    degradation to the old free endpoint ``base.blockscout.com`` -- available
    for ``"base"`` ONLY (other chains require the Pro key)."""

    def __init__(self, *, chain: str = "base", min_interval: float | None = None) -> None:
        self.chain = chain
        api_key = _pro_api_key()
        chain_id = CHAIN_IDS.get(chain)

        if api_key and chain_id is not None:
            self.base_url = f"{PRO_API_URL}/{chain_id}/api/v2"
            self._api_key: str | None = api_key
            # 21/07 -- calibrated to 90% of the confirmed 5 req/s (official doc
            # + x-ratelimit-limit:5 header verified live), CLAUDE.md doctrine
            # "Rate calibrated to 90%": 4.5 req/s = 0.222s. Replaces 0.2s
            # (100%, zero margin).
            default_interval = 0.222
        elif chain == "base":
            self.base_url = BASE_URL
            self._api_key = None
            default_interval = 0.35
        else:
            # Non-Base chain with no Pro key: no known free endpoint --
            # every request fails cleanly via _get_json (never a randomly
            # guessed URL).
            self.base_url = ""
            self._api_key = None
            default_interval = 0.35

        self._min_interval = min_interval if min_interval is not None else default_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        # 20/07 -- found under real conditions (Pro credits exhausted
        # mid-session, "Out of credits" as HTTP 402): a Pro key that's
        # CONFIGURED but DRAINED is not equivalent to a key that's ABSENT in
        # this __init__ -- once built on the Pro branch, this client stayed
        # committed to it forever, never falling back to the free
        # base.blockscout.com endpoint even though it works and was already
        # implemented for exactly this case (missing key). This flag is ONLY
        # used to log the fallback warning once (not on every call) -- the
        # real fallback state lives in base_url/_api_key, mutated in place by
        # _get_json on the first 402 encountered on the "base" chain.
        self._pro_credits_exhausted = False

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
                "blockscout: %s consecutive failures (last: %s) — no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info("blockscout: call failed (%s/%s) — %s", self._consecutive_failures, _FAIL_STREAK_WARN_THRESHOLD, detail)

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET with the AGENTS.md error policy. Returns (data, error)."""
        if not self.base_url:
            return None, f"{UNAVAILABLE} (clé Blockscout Pro requise pour la chaîne '{self.chain}')"

        attempt_429 = 0
        timeout_retried = False
        pro_fallback_attempted = False

        # 22/07 -- PROACTIVE check of the Pro credit budget, even before
        # attempting the call (the 402 fallback below stays the reactive
        # safety net if this budget turns out to be miscalibrated, never
        # removed). Only relevant on "base" (the only chain with a known
        # free fallback) -- on other chains, proactively depleting the
        # budget wouldn't help since there's nowhere to fall back to. Real
        # cost of THIS specific endpoint (cost_for_endpoint, 22/07 --
        # token-transfers costs 30, not 20 like the rest, verified on the
        # real dashboard).
        _pro_call_cost = blockscout_credit_budget.cost_for_endpoint(path)
        if (
            self._api_key
            and self.chain == "base"
            and not await blockscout_credit_budget.can_spend(_pro_call_cost)
        ):
            if not self._pro_credits_exhausted:
                self._pro_credits_exhausted = True
                logger.warning(
                    "blockscout: Pro credit budget nearly exhausted (90000/day) "
                    "-- PROACTIVE fallback (this process) to the free endpoint "
                    "base.blockscout.com, even before a real 402",
                )
            self.base_url = BASE_URL
            self._api_key = None
            self._min_interval = 0.35

        while True:
            # url/call_params recomputed on EVERY iteration (not once before
            # the loop) -- necessary since 20/07: the 402 fallback below
            # mutates self.base_url/self._api_key IN PLACE, a value frozen
            # before the loop would retry the old Pro URL indefinitely. Same
            # behavior for every other retry path (429/5xx/timeout), which
            # never change base_url/_api_key.
            url = f"{self.base_url}{path}"
            call_params = dict(params or {})
            if self._api_key:
                call_params["apikey"] = self._api_key
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=call_params)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Blockscout)"

            # 20/07 -- found under real conditions (Pro credits exhausted
            # mid-session, "Out of credits" as a 402): automatic fallback to
            # the FREE base.blockscout.com endpoint, already implemented for
            # the "no key configured" case but never exercised for the "key
            # configured but drained" case -- yet both situations leave the
            # same real choice (Base only, no key). PERMANENT fallback for
            # this process's lifetime (a drained key doesn't refill itself)
            # -- applies ONLY on "base" (the only chain with a known free
            # endpoint) and only if the Pro key was still in use (never a
            # loop if the free endpoint itself failed).
            if (
                response.status_code == 402
                and self._api_key
                and self.chain == "base"
                and not pro_fallback_attempted
            ):
                pro_fallback_attempted = True
                if not self._pro_credits_exhausted:
                    self._pro_credits_exhausted = True
                    logger.warning(
                        "blockscout: Pro API returned 402 (%s) on %s -- PERMANENT "
                        "fallback (this process) to the free endpoint "
                        "base.blockscout.com -- flag to the operator to top up "
                        "Pro credits",
                        response.text[:200],
                        url,
                    )
                self.base_url = BASE_URL
                self._api_key = None
                self._min_interval = 0.35
                continue

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
                await self._record_pro_credit_spend(path)
                return None, "adresse ou contrat introuvable"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            await self._record_pro_credit_spend(path)
            return response.json(), None

    async def _record_pro_credit_spend(self, path: str) -> None:
        """Only records if THIS call actually went through the Pro API (a
        key still present at the moment of success -- a 402/proactive
        fallback already triggered ALONG THE WAY would have set ``_api_key``
        to ``None`` before reaching this point). Best-effort, never
        blocking: a hiccup writing the budget counter must never fail an
        otherwise successful Blockscout call."""
        if not self._api_key:
            return
        try:
            cost = blockscout_credit_budget.cost_for_endpoint(path)
            await blockscout_credit_budget.record_spend(endpoint=path, credits=cost)
        except Exception:
            logger.warning("blockscout: failed to record credit budget spend (non-blocking)", exc_info=True)

    # ------------------------------------------------------------------
    # 1. Address info (balance, contract, verification, name)
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

        creator = data.get("creator_address_hash") or data.get("creator_address")
        token = data.get("token") if isinstance(data.get("token"), dict) else {}
        holders_raw = token.get("holders_count")
        holders_count = None
        if holders_raw is not None:
            try:
                holders_count = int(holders_raw)
            except (TypeError, ValueError):
                holders_count = None

        ens_domain_name = data.get("ens_domain_name")

        return AddressInfo(
            address=address,
            is_contract=bool(data.get("is_contract")),
            is_verified=data.get("is_verified"),
            contract_name=data.get("name"),
            balance_wei=str(balance_wei) if balance_wei is not None else None,
            balance_native=balance_native,
            creator_address=str(creator).lower() if creator else None,
            holders_count=holders_count,
            ens_domain_name=str(ens_domain_name) if ens_domain_name else None,
            available=True,
            error=None,
        )

    # ------------------------------------------------------------------
    # 2. Token transfers (who pays whom, which tokens, amounts)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_token_transfer(item: dict) -> TokenTransfer:
        token = item.get("token") or {}
        total = item.get("total") or {}
        amount = None
        transfer_error = None
        raw_value = total.get("value")
        decimals_raw = total.get("decimals")
        if raw_value is not None:
            if decimals_raw is None:
                transfer_error = "décimales du token indisponible"
            else:
                try:
                    amount = int(raw_value) / (10 ** int(decimals_raw))
                except (TypeError, ValueError):
                    transfer_error = "décimales du token indisponible"

        return TokenTransfer(
            tx_hash=str(item.get("tx_hash") or item.get("transaction_hash") or ""),
            from_address=str((item.get("from") or {}).get("hash") or ""),
            to_address=str((item.get("to") or {}).get("hash") or ""),
            token_address=token.get("address_hash") or token.get("address"),
            token_symbol=token.get("symbol"),
            token_name=token.get("name"),
            amount=amount,
            timestamp=item.get("timestamp"),
            method=item.get("method"),
            error=transfer_error,
        )

    async def get_token_transfers(
        self,
        address: str,
        limit: int = 50,
        *,
        max_pages: int = 1,
        token_type: str | None = None,
    ) -> TokenTransfersResult:
        """``max_pages`` > 1 follows Blockscout's ``next_page_params`` cursor
        to approach a wallet's full history (#157, wallet-centric
        multi-token) -- default behavior (``max_pages=1``) unchanged for
        existing callers (``analyze_smart_money``, token-centric, one page
        is enough). ``token_type`` (e.g. ``"ERC-20"``) filters NFT/ERC-1155
        noise on the API side."""
        params: dict = {"type": token_type} if token_type else {}
        transfers: list[TokenTransfer] = []
        pages_fetched = 0
        # 15/07, external review -- distinguishes "history genuinely
        # exhausted" (API has no more `next_page_params`) from "we stopped
        # before the end" (network error, malformed response, or
        # max_pages/limit cap reached while `next_page_params` still
        # existed) -- only this 2nd case should surface `truncated=True`,
        # never a signal silently lost for a very active wallet whose
        # history exceeds the cap (2000 transfers / 10 pages on the
        # wallet-scoring side).
        truncated = False

        while True:
            data, error = await self._get_json(f"/addresses/{address}/token-transfers", params=params)
            if error is not None:
                if pages_fetched == 0:
                    return TokenTransfersResult(available=False, error=error)
                truncated = True
                break
            if not isinstance(data, dict):
                if pages_fetched == 0:
                    return TokenTransfersResult(available=False, error=UNAVAILABLE)
                truncated = True
                break

            items = data.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                transfers.append(self._parse_token_transfer(item))
                if len(transfers) >= limit:
                    break

            pages_fetched += 1
            next_page = data.get("next_page_params")
            if not next_page:
                break
            if pages_fetched >= max_pages or len(transfers) >= limit:
                truncated = True
                break
            params = {**({"type": token_type} if token_type else {}), **next_page}

        return TokenTransfersResult(transfers=transfers[:limit], available=True, error=None, truncated=truncated)

    async def get_transaction_token_transfers(self, tx_hash: str) -> TokenTransfersResult:
        """All ERC-20 transfers of ONE specific transaction (14/07,
        wallet-scoring #157 -- exact price by tx_hash, complementing
        ``get_ohlcv``/``price_at``). A single page: a swap transaction, even
        multi-hop, counts at most a few dozen transfers, never needs
        pagination. Reuses ``_parse_token_transfer`` (same decimals/address
        parsing as ``get_token_transfers``, no duplication)."""
        data, error = await self._get_json(f"/transactions/{tx_hash}/token-transfers")
        if error is not None:
            return TokenTransfersResult(available=False, error=error)
        if not isinstance(data, dict):
            return TokenTransfersResult(available=False, error=UNAVAILABLE)

        items = data.get("items") or []
        transfers = [self._parse_token_transfer(item) for item in items if isinstance(item, dict)]
        return TokenTransfersResult(transfers=transfers, available=True, error=None)

    # ------------------------------------------------------------------
    # 3. Transaction history
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
            created_field = item.get("created_contract")
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
                    created_contract=(created_field or {}).get("hash") if isinstance(created_field, dict) else None,
                )
            )
        return TransactionsResult(transactions=transactions, available=True, error=None)

    # ------------------------------------------------------------------
    # 3b. Bounded history (approximates the wallet's age / funding source --
    # #157, never guaranteed exhaustive: Blockscout offers no cheap "oldest
    # first" sort on this endpoint, verified live (``sort=asc`` returns an
    # empty list). Pagination capped at ``max_pages``; ``truncated=True`` if
    # the cap is reached without exhausting the history -- the result is
    # then a BOUND, never the actual first transaction.
    # ------------------------------------------------------------------
    async def get_transactions_bounded(self, address: str, *, max_pages: int = 5) -> "BoundedTransactionsResult":
        params: dict = {}
        transactions: list[Transaction] = []
        pages_fetched = 0

        while True:
            data, error = await self._get_json(f"/addresses/{address}/transactions", params=params)
            if error is not None:
                if pages_fetched == 0:
                    return BoundedTransactionsResult(available=False, error=error)
                break
            if not isinstance(data, dict):
                if pages_fetched == 0:
                    return BoundedTransactionsResult(available=False, error=UNAVAILABLE)
                break

            items = data.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                to_field = item.get("to")
                created_field = item.get("created_contract")
                value_native = None
                raw_value = item.get("value")
                if raw_value is not None:
                    try:
                        value_native = int(raw_value) / 1e18
                    except (TypeError, ValueError):
                        value_native = None
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
                        created_contract=(created_field or {}).get("hash") if isinstance(created_field, dict) else None,
                    )
                )

            pages_fetched += 1
            next_page = data.get("next_page_params")
            if not next_page:
                return BoundedTransactionsResult(transactions=transactions, available=True, error=None, truncated=False)
            if pages_fetched >= max_pages:
                break
            params = next_page

        return BoundedTransactionsResult(transactions=transactions, available=True, error=None, truncated=True)

    # ------------------------------------------------------------------
    # 4. Holder distribution (top holders, %)
    # ------------------------------------------------------------------
    async def get_token_metadata(self, token_address: str) -> TokenMetadataResult:
        """21/07 -- extracted from ``get_token_holders`` (decimals +
        total_supply only, ``/tokens/{address}`` endpoint) to let a caller
        combine this metadata with a separate holders source (e.g. x402,
        see ``momentum_entry._check_holder_concentration``)."""
        token_data, token_error = await self._get_json(f"/tokens/{token_address}")
        if token_error is not None:
            return TokenMetadataResult(available=False, error=token_error)
        if not isinstance(token_data, dict):
            return TokenMetadataResult(available=False, error=UNAVAILABLE)

        decimals_raw = token_data.get("decimals")
        decimals: int | None
        try:
            decimals = int(decimals_raw) if decimals_raw is not None else None
        except (TypeError, ValueError):
            decimals = None
        decimals_error = None if decimals is not None else "décimales du token indisponible"

        total_supply_raw = token_data.get("total_supply")
        total_supply = None
        if decimals is not None and total_supply_raw is not None:
            try:
                total_supply = int(total_supply_raw) / (10**decimals)
            except (TypeError, ValueError):
                total_supply = None

        return TokenMetadataResult(
            decimals=decimals, total_supply=total_supply, available=True, error=decimals_error,
        )

    async def get_token_holders(self, token_address: str) -> TokenHoldersResult:
        metadata = await self.get_token_metadata(token_address)
        if not metadata.available:
            return TokenHoldersResult(available=False, error=metadata.error)
        decimals = metadata.decimals
        decimals_error = metadata.error
        total_supply = metadata.total_supply

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
            if decimals is not None and raw_balance is not None:
                try:
                    balance = int(raw_balance) / (10**decimals)
                except (TypeError, ValueError):
                    balance = None

            percentage = None
            if balance is not None and total_supply:
                percentage = (balance / total_supply) * 100

            holder_address = item.get("address")
            is_addr_dict = isinstance(holder_address, dict)
            holders.append(
                TokenHolder(
                    address=str((holder_address or {}).get("hash") if is_addr_dict else holder_address or ""),
                    balance=balance,
                    percentage=percentage,
                    is_contract=bool(holder_address.get("is_contract")) if is_addr_dict else None,
                    is_verified=bool(holder_address.get("is_verified")) if is_addr_dict else None,
                )
            )

        return TokenHoldersResult(holders=holders, total_supply=total_supply, available=True, error=decimals_error)

    # ------------------------------------------------------------------
    # 5. is_verified + scan for sensitive functions (mint, disable_transfers, blacklist)
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

        # We ONLY consider ABI functions that MODIFY state (nonpayable/payable),
        # i.e. a power actually callable after deployment. We IGNORE:
        #   - `view`/`pure` functions (getters: `isBlacklisted`, `mintingFinished`)
        #     which aren't the power itself;
        #   - raw source code: `_mint` (an INTERNAL OpenZeppelin function) is
        #     present in EVERY ERC20, even fixed-supply ones -> scanning the
        #     source produced an almost-systematic false-positive "mint"
        #     (wrongly rejected).
        # Internal functions don't appear in the ABI: a `_mint` used only in
        # the constructor will therefore NOT be flagged. Only an external
        # `mint(...)` (that the dev can call to dilute) is.
        mutating_names: set[str] = set()
        for entry in data.get("abi") or []:
            if not isinstance(entry, dict) or entry.get("type") != "function":
                continue
            if not entry.get("name"):
                continue
            mutability = str(entry.get("stateMutability") or "").lower()
            if mutability in ("view", "pure"):
                continue
            mutating_names.add(str(entry["name"]).lower().replace("_", ""))

        def _has_flag(aliases: tuple[str, ...]) -> bool:
            # Substring match on mutating function names: catches variants
            # (`mintTo`, `addToBlacklist`, `stopTrading`) without the source
            # code noise.
            return any(alias in name for name in mutating_names for alias in aliases)

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

    # ------------------------------------------------------------------
    # 6. Best-effort owner read — renouncement detection
    # ------------------------------------------------------------------
    async def read_owner(self, token_address: str) -> tuple[str | None, str | None]:
        """Returns (owner_address, error). ``owner`` in lowercase, or None if unreadable.

        Queries the contract's READ methods (Blockscout ``methods-read``) and
        fetches the current value of a no-argument getter named ``owner`` /
        ``getOwner`` / ``_owner``. Best-effort and defensive: any unexpected
        shape returns (None, reason) — never an exception (graceful
        degradation).
        """
        data, error = await self._get_json(f"/smart-contracts/{token_address}/methods-read")
        if error is not None:
            return None, error
        methods = data if isinstance(data, list) else (data.get("items") if isinstance(data, dict) else None)
        if not isinstance(methods, list):
            return None, UNAVAILABLE
        for m in methods:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").lower().replace("_", "")
            if name not in ("owner", "getowner"):
                continue
            if m.get("inputs"):
                continue  # a real owner getter takes no argument
            for out in m.get("outputs") or []:
                if not isinstance(out, dict):
                    continue
                val = out.get("value")
                if isinstance(val, str) and val.startswith("0x") and len(val) == 42:
                    return val.lower(), None
        return None, "owner introuvable"


blockscout_client = BlockscoutClient()

_chain_clients: dict[str, BlockscoutClient] = {"base": blockscout_client}


def get_blockscout_client(chain: str) -> BlockscoutClient:
    """Blockscout client for ``chain`` (#157, multi-chain wallet-scoring,
    14/07) -- one client per chain (independent throttle/failure state),
    cached. A ``chain`` outside ``CHAIN_IDS`` still returns a client
    (graceful degradation handled by ``_get_json``), never an exception."""
    client = _chain_clients.get(chain)
    if client is None:
        client = BlockscoutClient(chain=chain)
        _chain_clients[chain] = client
    return client
