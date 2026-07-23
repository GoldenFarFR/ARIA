"""Read-only Clanker client (Base) — a "direct" launchpad (no bonding phase).

Unlike Virtuals (bonding curve then graduation), a Clanker token receives
**real DEX liquidity right from deployment** (fair-launch Uniswap v3/v4 on Base) —
there's no pre-graduation phase to distinguish. This client therefore only serves
**fast discovery** (the most recent tokens); absorption itself reuses
the standard pipeline (`token_absorber.absorb`, 85% VC pool), not a dedicated path like
the bonding niche.

Documented public endpoint (``github.com/clanker-devco/DOCS``): base
``https://www.clanker.world/api``, ``GET /api/tokens`` ("Search and list Clanker
tokens with filters, sorting, and cursor-based pagination"). Auth: none for
public reads (partner key ``x-api-key`` only for higher quotas,
not required here).

Query parameters (``chainId``/``sort``/``sortBy``/``limit``) and response
shape CONFIRMED LIVE from the VPS on 07/10 (the cloud sandbox, meanwhile, was
blocked with HTTP 403 — generic Cloudflare anti-bot behavior specific to that
environment, not the API). Two corrections made thanks to the API's own
validation messages (never the docs, silent on these details): ``sortBy`` accepts a
strict enum (cf. ``_VALID_SORT_BY`` — ``createdAt``, plausible, was wrong);
``limit`` has a REAL cap of 20 (cf. ``_MAX_LIMIT`` — 100, plausible, was wrong and
made the ENTIRE call fail with HTTP 400, not just suboptimal). Valid response:
``{"data": [...]}``, each item a flat ``snake_case`` dict (``id``,
``created_at``, ``admin`` — the deployer —, ``tx_hash``, ``contract_address``,
``name``, ``symbol``, ``description``, ``deployed_at``, ``starting_market_cap``,
``chain_id``, ``platform``...). The parsing below stays deliberately tolerant
(``_first`` over several field names, snake_case AND camelCase) to degrade
gracefully if the shape evolves, rather than locking in a fragile dependency.

Same policies as the other clients in this folder:
- No writes, no signing, GET only.
- 429: exponential backoff, 3 attempts max, then give up without blocking.
- Timeout / 5xx: 1 retry after 5s, then explicit fallback.
- ``fetch_*`` NEVER raises on a network error: returns ``[]`` / ``None``.
- Guardrail: any external string goes through ``_sanitize`` (control-character
  stripping + neutralizing angle brackets ``<``/``>`` — anti prompt-injection).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://www.clanker.world/api"
_TOKENS_ENDPOINT = f"{API_ROOT}/tokens"

UNAVAILABLE = "donnée Clanker indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FIELD_MAX = 600


@dataclass
class ClankerToken:
    """An indexed Clanker token. All strings are already sanitized."""

    name: str | None = None
    symbol: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    pool_address: str | None = None
    created_at: str | None = None
    mcap: float | None = None
    volume24h: float | None = None
    liquidity_usd: float | None = None
    holder_count: int | None = None
    deployer_address: str | None = None
    description: str | None = None
    warning_flags: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Sanitization (guardrail) — identical to services/virtuals.py
# ----------------------------------------------------------------------
def _sanitize(text: object, max_len: int = _FIELD_MAX) -> str | None:
    if text is None:
        return None
    s = _CONTROL_CHARS_RE.sub("", str(text))
    s = s.replace("<", "‹").replace(">", "›")
    return s[:max_len]


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None or isinstance(value, bool) or isinstance(value, (list, dict)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first(mapping: dict, *keys: str) -> object:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


# ----------------------------------------------------------------------
# URL construction
# ----------------------------------------------------------------------
#: Values accepted by ``sortBy``, CONFIRMED LIVE on 07/10 from the VPS (the
#: endpoint returns an explicit validation error listing the exact enum
#: when an invalid value is sent — ``createdAt``, plausible but wrong, was
#: fixed thanks to this error message). Only ``deployed-at`` matches "most
#: recent first", which is what we want for discovery.
_VALID_SORT_BY = frozenset(
    {"market-cap", "tx-h24", "volume-h24", "price-percent-h24", "price-percent-h1", "deployed-at"}
)


#: REAL cap on ``limit``, CONFIRMED LIVE on 07/10 from the VPS: a call
#: with ``limit=50`` failed with HTTP 400 and an explicit validation message
#: (``"maximum":20,"inclusive":true,"path":["limit"]``) — 100 (initial assumption)
#: was wrong. A ``limit`` out of bounds is no longer just "suboptimal", it makes
#: the ENTIRE call fail (fetch_recent returns ``[]``): this cap must stay exact.
_MAX_LIMIT = 20


def build_recent_tokens_url(chain_id: int = 8453, limit: int = 50) -> str:
    """URL for the most recent tokens on Base (``chain_id=8453``).

    ``chainId``/``sort``/``sortBy``/``limit`` (``<=20``) CONFIRMED LIVE on 07/10
    (VPS, real network access — the cloud sandbox was blocked with HTTP 403 anti-bot). The
    shape of a VALID response (``limit`` compliant) was also confirmed live:
    ``{"data": [...]}`` where each item is a flat dict (``id``, ``created_at``,
    ``admin``, ``tx_hash``, ``contract_address``, ``name``, ``symbol``,
    ``description``, ...) — see ``parse_clanker_token`` for the fields kept.
    """
    try:
        size = int(limit)
    except (TypeError, ValueError):
        size = _MAX_LIMIT
    size = max(1, min(size, _MAX_LIMIT))
    params = [
        ("chainId", str(chain_id)),
        ("sort", "desc"),
        ("sortBy", "deployed-at"),
        ("limit", str(size)),
    ]
    return f"{_TOKENS_ENDPOINT}?{urlencode(params)}"


def build_token_by_address_url(token_address: str, chain_id: int = 8453) -> str:
    """URL filtering by contract address.

    Address lowercased (same fix as ``virtuals.py``, 07/10) --
    defensive: EVM addresses are case-insensitive, no information
    loss, but this protects against an exact filter on the API side that wouldn't
    match a mixed-case (checksum) address."""
    params = [("chainId", str(chain_id)), ("address", str(token_address).lower())]
    return f"{_TOKENS_ENDPOINT}?{urlencode(params)}"


# ----------------------------------------------------------------------
# Parsing (graceful degradation)
# ----------------------------------------------------------------------
def parse_clanker_token(raw: dict) -> ClankerToken | None:
    """Parses a Clanker response object into a ``ClankerToken``. Never raises.

    Real fields CONFIRMED LIVE on 07/10 (``contract_address``, ``admin``,
    ``created_at``/``deployed_at``, ``starting_market_cap`` — snake_case) placed at
    the head of each fallback list; camelCase variants kept as a tolerant fallback
    in case the shape evolves. Non-dict raw → ``None``; missing field → ``None``
    (facts-only) — ``volume24h``/``liquidity_usd``/``holder_count`` generally stay
    ``None`` on this endpoint (deployment feed, no live market
    data), without any missing data ever being invented.
    """
    if not isinstance(raw, dict):
        return None

    return ClankerToken(
        name=_sanitize(_first(raw, "name", "tokenName"), 120),
        symbol=_sanitize(_first(raw, "symbol", "ticker"), 20),
        chain_id=_safe_int(_first(raw, "chain_id", "chainId")),
        contract_address=_sanitize(
            _first(raw, "contract_address", "contractAddress", "address", "tokenAddress"), 80
        ),
        pool_address=_sanitize(_first(raw, "pool_address", "poolAddress", "pair"), 80),
        created_at=_sanitize(_first(raw, "created_at", "createdAt", "deployed_at", "deployedAt"), 40),
        mcap=_safe_float(_first(raw, "starting_market_cap", "marketCap", "mcap", "market_cap")),
        volume24h=_safe_float(_first(raw, "volume24h", "volume_24h", "volume")),
        liquidity_usd=_safe_float(_first(raw, "liquidityUsd", "liquidity_usd", "liquidity")),
        holder_count=_safe_int(_first(raw, "holderCount", "holder_count", "holders")),
        deployer_address=_sanitize(
            _first(raw, "admin", "msg_sender", "deployer_address", "deployerAddress", "creator", "deployer"), 80
        ),
        description=_sanitize(_first(raw, "description"), _FIELD_MAX),
        warning_flags=[
            _sanitize(f, 200) for f in (raw.get("warnings") or []) if isinstance(f, (str, int, float))
        ][:12],
    )


# ----------------------------------------------------------------------
# HTTP client (read-only)
# ----------------------------------------------------------------------
class ClankerClient:
    """Async HTTP client, read-only, cautious throttle (public API)."""

    def __init__(self, endpoint: str = _TOKENS_ENDPOINT, *, min_interval: float = 0.5) -> None:
        self.endpoint = endpoint
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
                "clanker: %s consecutive failures (last: %s) -- no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "clanker: call failed (%s/%s) -- %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        """GET with the in-house error policy. Returns ``(data, error)``."""
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers={"accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Clanker)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 after {attempt_429} attempts"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Clanker)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Clanker)"

            if response.status_code in (403, 404):
                # 403: likely generic anti-bot blocking (observed in testing) -- not
                # an escalation, a documented degradation (cf. module-level warning).
                self._record_success()
                return None, f"{UNAVAILABLE} (HTTP {response.status_code})"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def fetch_recent(self, chain_id: int = 8453, limit: int = 50) -> list[ClankerToken]:
        """Most recent tokens on Base. Always a list (``[]`` on error)."""
        try:
            url = build_recent_tokens_url(chain_id=chain_id, limit=limit)
            data, error = await self._get_json(url)
            if error is not None:
                return []
            items = None
            if isinstance(data, dict):
                items = _first(data, "tokens", "data", "results")
            elif isinstance(data, list):
                items = data
            if not isinstance(items, list):
                return []
            tokens: list[ClankerToken] = []
            for item in items:
                token = parse_clanker_token(item)
                if token is not None:
                    tokens.append(token)
            return tokens
        except Exception as exc:  # ultimate degradation: never an outgoing exception
            logger.info("clanker: fetch_recent unexpected failure -- %s", exc)
            return []

    async def fetch_by_address(self, token_address: str, chain_id: int = 8453) -> ClankerToken | None:
        """Clanker token by contract address. ``None`` on error or absence."""
        try:
            url = build_token_by_address_url(token_address, chain_id=chain_id)
            data, error = await self._get_json(url)
            if error is not None:
                return None
            items = None
            if isinstance(data, dict):
                items = _first(data, "tokens", "data", "results")
            elif isinstance(data, list):
                items = data
            if not isinstance(items, list) or not items:
                return None
            return parse_clanker_token(items[0])
        except Exception as exc:
            logger.info("clanker: fetch_by_address unexpected failure -- %s", exc)
            return None


clanker_client = ClankerClient()
