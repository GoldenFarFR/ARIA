"""Read-only GoPlus Security client (Token Security API) — honeypot detection.

Complements Blockscout's "static" ABI scan (which functions EXIST) with a
dynamic BEHAVIOR reading that the ABI alone doesn't reveal: is the token a
honeypot (resale blocked), what are the REAL buy/sell taxes, is the owner
hidden, can it take back ownership, are transfers pausable, etc.

GoPlus API, Base chain = 8453. Read-only, no call other than GET/POST-token.
OPTIONAL authentication (#207, 18/07): if `GOPLUS_APP_KEY`/`GOPLUS_APP_SECRET`
are present in the environment, an access_token (JWT, valid 2h, auto-renewed)
is attached as an `Authorization: Bearer <token>` header on every call (fixed
on 21/07 -- the old `access-token` header wasn't recognized, see the comment
in `_get_json`) -- separates ARIA's quota from the anonymous per-IP limit
(~30 req/min, the direct cause of the `code 4029` errors observed 17-18/07).
Without these credentials, historical behavior is unchanged (public API, no
key). Same error policy as blockscout.py (dome):
- 429: exponential backoff, 3 attempts max, then gives up without blocking
  the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit fallback.
- Missing data is NEVER replaced by a guess: `available=False` + `error`,
  and every flag is None (unknown) rather than False when GoPlus doesn't
  respond.
- Authentication failure (token, network): never blocking, silent fallback
  to the call without a header (same behavior as if no key were configured).
- Reactive circuit breaker (21/07, quota safety net): beyond
  `_CIRCUIT_FAIL_THRESHOLD` consecutive failures (429/code 4029/timeout/5xx),
  the client stops calling the network for `_CIRCUIT_COOLDOWN_S` -- protects
  against any hidden cap (monthly/daily, never confirmed by GoPlus, so never
  hardcoded here) without making up a number. Purely reactive, same pattern
  as the per-provider circuit breaker already built on the OHLCV cascade
  (`momentum_entry._fetch_candles`).

A None (unknown) flag never blocks on its own: only a POSITIVELY confirmed
signal (honeypot=1, cannot_sell=1, high tax…) penalizes. Consistent with the
doctrine: a network outage does not ban a good token.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.gopluslabs.io/api/v1"
BASE_CHAIN_ID = "8453"  # Base mainnet

# 22/07 -- cooldown after a rejected authentication (code 4012) despite a
# token issued successfully -- avoids hammering a broken token on every
# subsequent call as long as it isn't fixed on the credentials side (operator
# action, likely GOPLUS_APP_SECRET rotation needed in .env). 30 min: long
# enough not to spam, short enough to self-heal without a redeploy once the
# credentials are fixed.
_AUTH_BROKEN_COOLDOWN_S = 1800.0

# 22/07 -- TTL of the per-contract security cache (dedup of a scarce resource,
# see GoPlusClient.__init__ for the full reasoning). Confirmed renounced = 30
# days (beyond a process's normal lifetime, mostly serves as a defensive
# safeguard in case of a long-running process -- not a really meaningful TTL
# given the structural invariant). Otherwise = 120s, aligned with the WebSocket
# cadence (~30s, `momentum_websocket.py`) to deduplicate without delaying the
# detection of a real behavior change.
_RENOUNCED_CACHE_TTL_S = 30 * 24 * 3600.0
_SHORT_CACHE_TTL_S = 120.0

UNAVAILABLE = "donnée GoPlus indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3
_TOKEN_REFRESH_MARGIN_S = 300  # renouvelle 5 min avant l'expiration annoncée par GoPlus

# 21/07 -- reactive circuit breaker (quota safety net). No monthly/daily GoPlus
# cap has ever been confirmed (only the 150 CU/min rate is verified on the
# dashboard) -- no number invented here, only a defensive pause once a
# SUSTAINED failure is observed, whatever its real cause (rate limit, monthly
# quota exhausted, GoPlus-side outage). 5 consecutive failures (beyond the
# simple-log threshold of 3) before stopping network calls for 5 minutes,
# rather than hammering an exhausted account candidate after candidate.
_CIRCUIT_FAIL_THRESHOLD = 5
_CIRCUIT_COOLDOWN_S = 300.0


def goplus_authenticated() -> bool:
    """True if the app_key/app_secret credentials are configured in the environment."""
    return bool(os.environ.get("GOPLUS_APP_KEY", "").strip() and os.environ.get("GOPLUS_APP_SECRET", "").strip())


@dataclass
class TokenSecurity:
    """Dynamic security reading of a token (GoPlus). Each flag: True (confirmed),
    False (confirmed absent), or None (unknown / GoPlus has no data)."""

    address: str
    # The most important: is resale possible at all?
    is_honeypot: bool | None = None
    cannot_sell_all: bool | None = None
    cannot_buy: bool | None = None
    # Real taxes (fraction: 0.05 = 5%). None if unknown.
    buy_tax: float | None = None
    sell_tax: float | None = None
    # Hidden dev powers.
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    owner_change_balance: bool | None = None
    transfer_pausable: bool | None = None
    trading_cooldown: bool | None = None
    slippage_modifiable: bool | None = None
    is_blacklisted: bool | None = None       # the contract CAN blacklist
    is_mintable: bool | None = None
    is_open_source: bool | None = None       # 0 = unverified code
    is_proxy: bool | None = None
    # 22/07 -- contract owner address, verified live on 4 real tokens: empty
    # string ("") or absent (None) = no active owner (renounced/canonical, e.g.
    # WETH); a real address = active owner (e.g. USDC admin proxy). Serves as
    # the basis for `ownership_verifiably_renounced` below -- never displayed
    # alone as a security verdict.
    owner_address: str | None = None
    available: bool = False
    error: str | None = None
    # #207, 18/07: True ONLY when GoPlus responded cleanly (no network/HTTP
    # failure) but has NO data for this contract (`result` empty/null --
    # common on Solana for a token that just launched, verified live).
    # Distinct from a real failure (timeout, 5xx, rate limit) -- only this
    # specific case authorizes a second opinion (services/rugcheck.py) in
    # momentum_entry._check_honeypot.
    no_data: bool = False

    @property
    def ownership_verifiably_renounced(self) -> bool:
        """True ONLY when GoPlus positively confirms that no active owner still
        controls this contract -- no function can then change its security
        behavior over time (22/07, operator observation). Requires BOTH signals
        at once (never just one): (1) `owner_address` empty/absent -- no active
        owner; (2) no confirmed takeover mechanism
        (`can_take_back_ownership`/`hidden_owner`/`owner_change_balance` all
        different from True) -- a contract that SEEMS renounced but keeps a
        backdoor is NOT considered definitively safe. `None` (unknown) on any
        of these 3 flags does NOT count as "confirmed absent" -- fail-closed
        on UNCERTAINTY, consistent with the doctrine of the rest of the module."""
        if not self.available:
            return False
        owner = (self.owner_address or "").strip()
        if owner:
            return False
        return (
            self.can_take_back_ownership is False
            and self.hidden_owner is False
            and self.owner_change_balance is False
        )


@dataclass
class AddressSecurity:
    """GoPlus Malicious Address API (AML) reading -- #157. ``flags`` contains
    ONLY the POSITIVELY confirmed categories (True); a missing key =
    unreported category, never reconstructed as False (consistent with the
    spirit of ``_tri``: silence != confirmation of innocence)."""

    address: str
    flags: dict[str, bool] = field(default_factory=dict)
    is_malicious: bool = False  # True if AT LEAST ONE category is positively confirmed
    available: bool = False
    error: str | None = None


# Fields of the `address_security` response that are NOT risk categories
# (metadata) -- excluded from the `is_malicious` computation.
_ADDRESS_SECURITY_META_FIELDS = {"contract_address", "data_source", "number_of_malicious_contracts_created"}


def _tri(value: object) -> bool | None:
    """"1" -> True, "0" -> False, "" / None / other -> None (unknown)."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "1":
        return True
    if s == "0":
        return False
    return None


def _tax(value: object) -> float | None:
    """Converts a GoPlus tax ("0.05") into a float fraction, or None if unreadable/absent."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class GoPlusClient:
    """Async HTTP client, read-only, moderate throttle (public API, no key)."""

    # 21/07 -- CORRECTION of a first miscalibration made the same day (1.212s,
    # based on a misread empirical burst test -- the "blocked at the 11th
    # request" observed was not an ambiguous ~55/min cap, it's EXACTLY
    # 150 CU / 15 CU-per-token = 10 requests, confirmed once the real billing
    # structure was known). Root cause: GoPlus bills PER VERIFIED TOKEN (15 CU
    # for Token Security API on EVM, 30 CU for Solana), not per HTTP call --
    # `get_token_security()` below ALWAYS queries a single contract per call,
    # so 1 call = 15 CU on Base. Real account limit CONFIRMED LIVE on the real
    # GoPlus dashboard (gopluslabs.io/dashboard, Free tier,
    # "Rate Limit: 150 CU/Min") -- the most reliable source possible, above
    # even an empirical test: 150 CU/min / 15 CU/token = **10 real req/min**.
    # CLAUDE.md "90% calibrated throughput" doctrine: 90% of 10/min = 9/min =
    # 6.667s. If this client one day queries Solana (30 CU/token) without
    # going through `_check_honeypot_rugcheck_fallback`, the real limit would
    # drop to 5 req/min -- not handled here, this client currently only makes
    # 1-token/EVM calls.
    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 6.667) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._auth_broken_until: float = 0.0
        # 22/07 -- per-contract security cache (dedup of a scarce resource, see
        # the "scarcity -> dedup before recalibrating" doctrine). Key
        # (chain_id, lowercase address) -> (TokenSecurity, expire_at epoch). A
        # token whose ownership is VERIFIABLY renounced (see
        # TokenSecurity.ownership_verifiably_renounced) can structurally no
        # longer change its security behavior over time -- cached for a very
        # long time (`_RENOUNCED_CACHE_TTL_S`). Everything else (active owner,
        # or unknown renouncement status) is cached BRIEFLY
        # (`_SHORT_CACHE_TTL_S`) -- enough to deduplicate close re-evaluations
        # of the same still-pending candidate (WebSocket ~30s), without
        # significantly delaying the detection of a newly malicious signal on
        # a contract whose owner keeps control. A failed result
        # (available=False) is NEVER cached -- the existing `no_data` retry
        # (`momentum_entry._check_honeypot`) must always be able to retry
        # without a cache freezing a transient "unavailable" state.
        self._security_cache: dict[tuple[str, str], tuple["TokenSecurity", float]] = {}

    async def _ensure_access_token(self) -> str | None:
        """Renews the access_token if absent/close to expiring. Returns None with
        no credentials configured (public path unchanged) or on network failure --
        never blocking, never an exception propagated to the caller."""
        app_key = os.environ.get("GOPLUS_APP_KEY", "").strip()
        app_secret = os.environ.get("GOPLUS_APP_SECRET", "").strip()
        if not app_key or not app_secret:
            return None

        async with self._token_lock:
            now = time.time()
            if self._access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_S:
                return self._access_token

            t = int(now)
            sign = hashlib.sha1(f"{app_key}{t}{app_secret}".encode()).hexdigest()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{self.base_url}/token",
                        data={"app_key": app_key, "time": t, "sign": sign},
                    )
                body = response.json()
            except Exception as exc:  # network, timeout, invalid JSON -- never blocking
                logger.warning("goplus: failed to renew access_token (%s) — falling back to the public API", exc)
                return self._access_token

            result = body.get("result") if isinstance(body, dict) else None
            token = result.get("access_token") if isinstance(result, dict) else None
            expires_in = result.get("expires_in") if isinstance(result, dict) else None
            if not token:
                logger.warning("goplus: /token response has no access_token — falling back to the public API")
                return self._access_token

            # 22/07 -- real bug found under real conditions: GoPlus sometimes
            # returns access_token ALREADY prefixed with "Bearer " in the string
            # itself (verified live: "Bearer eyJhY2NvdW50SWQi..."). _get_json
            # then builds "Authorization: Bearer {token}" -- without this
            # guard, the header sent becomes "Bearer Bearer eyJ...", which
            # GoPlus rejects (code 4012 "Wrong Signature", see the official
            # code table). Root cause of the code 4012 observed since 21/07
            # 10:37 (when switching to the Authorization: Bearer header) --
            # NOT a GoPlus-side credential rotation as initially assumed.
            # Normalized here so self._access_token is ALWAYS the bare JWT,
            # whatever format GoPlus returns.
            token = str(token).strip()
            if token.lower().startswith("bearer "):
                token = token[len("bearer "):].strip()

            self._access_token = token
            self._token_expires_at = now + float(expires_in or 0)
            logger.info("goplus: access_token renewed (expires in %ss)", expires_in)
            return self._access_token

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
        if self._consecutive_failures >= _CIRCUIT_FAIL_THRESHOLD:
            self._circuit_open_until = time.time() + _CIRCUIT_COOLDOWN_S
            logger.warning(
                "goplus: circuit breaker opened after %s consecutive failures (last: %s) — "
                "pausing %ss before retrying",
                self._consecutive_failures,
                detail,
                _CIRCUIT_COOLDOWN_S,
            )
        elif self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "goplus: %s consecutive failures (last: %s) — circuit breaker not yet open",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "goplus: call failure (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    def circuit_open(self) -> bool:
        return time.time() < self._circuit_open_until

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
        """GET with the dome's error policy. Returns (data, error)."""
        if self.circuit_open():
            return None, f"{UNAVAILABLE} (coupe-circuit ouvert, échecs consécutifs récents)"

        url = f"{self.base_url}{path}"
        attempt_429 = 0
        retried = False
        # 21/07 -- real bug found while investigating why the GoPlus account
        # showed NO consumption even over 30 days despite successful
        # authenticated calls: wrong header name. The official docs
        # (docs.gopluslabs.io/reference/tokensecurityusingget_1) require
        # "Authorization: Bearer <token>", never "access-token: <token>" --
        # the old header simply wasn't recognized. The endpoint stays
        # tolerant (returns 200 even without a valid token, tested live),
        # which masked the bug all this time: calls "succeeded" but were
        # never attributed to the authenticated account.
        # 22/07 -- broken-auth cooldown (see the code 4012 comment further
        # below): doesn't retry a systematically broken token on every call,
        # falls back directly to the public path as long as the cooldown
        # hasn't elapsed.
        if time.time() < self._auth_broken_until:
            token = None
        else:
            token = await self._ensure_access_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        auth_fallback_done = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (timeout GoPlus)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 apres {attempt_429} tentatives")
                    return None, f"{UNAVAILABLE} (rate limit GoPlus)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            # 17/07 -- real bug found while investigating the low purchase rate
            # of the $1M test: GoPlus signals its rate limit via an HTTP 200
            # with {"code":4029,"message":"too many requests"} in the body,
            # NOT a real HTTP 429 -- the branch above therefore never
            # triggered for this specific case, confirmed by a real call (20
            # candidates in a row: the first 9 OK, the next 11 code=4029).
            # Without a retry, every affected candidate silently fell back to
            # "no data for this contract" (a coverage false negative, not a
            # real security verdict) -- same backoff policy as a real 429, on
            # the same `attempt_429` counter.
            if response.status_code == 200:
                try:
                    probe = response.json()
                except ValueError:
                    probe = None
                if isinstance(probe, dict) and probe.get("code") == 4029:
                    attempt_429 += 1
                    if attempt_429 >= 3:
                        self._record_failure(f"{url} -> code 4029 apres {attempt_429} tentatives")
                        return None, f"{UNAVAILABLE} (rate limit GoPlus)"
                    await asyncio.sleep(0.5 * (2**attempt_429))
                    continue

                # 22/07 -- real bug found under real conditions: the token is
                # issued successfully (/token returns code=1 "ok") but
                # REJECTED by the data endpoint (code 4012 "signature
                # verification failure") -- confirmed on a very well-known
                # contract (WETH), so it was blocking THE ENTIRE momentum
                # pipeline, not just fresh tokens. Probable cause:
                # GOPLUS_APP_SECRET rotation on GoPlus's side following the
                # accidental exposure of 21/07 (the "rotate as a precaution"
                # recommendation, never confirmed applied on the .env side).
                # Immediate fallback to the call WITHOUT a token (public API,
                # historical behavior before authentication #207) rather than
                # treating every candidate as "no data" -- + a cooldown to
                # avoid hammering a broken token on every subsequent call as
                # long as it isn't fixed on the credentials side (operator
                # action).
                if (
                    headers is not None and not auth_fallback_done
                    and isinstance(probe, dict) and probe.get("code") == 4012
                ):
                    logger.warning(
                        "goplus: authentication rejected (code 4012, signature "
                        "verification failure despite a successfully issued "
                        "token) -- falling back to the public API for %ss, "
                        "check GOPLUS_APP_KEY/GOPLUS_APP_SECRET",
                        _AUTH_BROKEN_COOLDOWN_S,
                    )
                    self._auth_broken_until = time.time() + _AUTH_BROKEN_COOLDOWN_S
                    auth_fallback_done = True
                    headers = None
                    continue

            if response.status_code >= 500:
                if not retried:
                    retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (erreur serveur GoPlus)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_token_security(
        self, address: str, *, chain_id: str = BASE_CHAIN_ID
    ) -> TokenSecurity:
        """Queries GoPlus Token Security for a contract. Best-effort, never blocking.

        22/07 -- cache by (chain_id, address) before any network call (dedup of
        a scarce resource, see the `__init__`/`ownership_verifiably_renounced`
        comments). A cache entry is NEVER a failed result (see the storage
        point further below) -- no cache entry always triggers a real call."""
        addr = (address or "").strip()
        if not addr:
            return TokenSecurity(address=addr, available=False, error="adresse vide")

        cache_key = (str(chain_id), addr.lower())
        cached = self._security_cache.get(cache_key)
        if cached is not None:
            cached_security, expires_at = cached
            if time.time() < expires_at:
                return cached_security
            del self._security_cache[cache_key]

        data, error = await self._get_json(
            f"/token_security/{chain_id}", params={"contract_addresses": addr}
        )
        if error is not None:
            return TokenSecurity(address=addr, available=False, error=error)
        if not isinstance(data, dict):
            return TokenSecurity(address=addr, available=False, error=UNAVAILABLE)

        # GoPlus: {"code":1,"message":"OK","result":{"<addr_lower>":{...}}}
        result = data.get("result")
        if not isinstance(result, dict) or not result:
            # code != 1 or empty result = GoPlus doesn't (yet) have the data for
            # this token -- a clean HTTP response, not a failure (no_data=True, #207).
            msg = str(data.get("message") or "").strip()
            return TokenSecurity(
                address=addr,
                available=False,
                no_data=True,
                error=f"{UNAVAILABLE} (aucune donnée pour ce contrat{': ' + msg if msg else ''})",
            )

        row = result.get(addr.lower())
        if not isinstance(row, dict):
            # Case-insensitive key: take the first entry if the exact address is missing.
            row = next((v for v in result.values() if isinstance(v, dict)), None)
        if not isinstance(row, dict):
            return TokenSecurity(address=addr, available=False, error=UNAVAILABLE)

        security = TokenSecurity(
            address=addr,
            is_honeypot=_tri(row.get("is_honeypot")),
            cannot_sell_all=_tri(row.get("cannot_sell_all")),
            cannot_buy=_tri(row.get("cannot_buy")),
            buy_tax=_tax(row.get("buy_tax")),
            sell_tax=_tax(row.get("sell_tax")),
            hidden_owner=_tri(row.get("hidden_owner")),
            can_take_back_ownership=_tri(row.get("can_take_back_ownership")),
            owner_change_balance=_tri(row.get("owner_change_balance")),
            transfer_pausable=_tri(row.get("transfer_pausable")),
            trading_cooldown=_tri(row.get("trading_cooldown")),
            slippage_modifiable=_tri(row.get("slippage_modifiable")),
            is_blacklisted=_tri(row.get("is_blacklisted")),
            is_mintable=_tri(row.get("is_mintable")),
            is_open_source=_tri(row.get("is_open_source")),
            is_proxy=_tri(row.get("is_proxy")),
            owner_address=(row.get("owner_address") or None),
            available=True,
            error=None,
        )
        # 22/07 -- only caches AVAILABLE results (never a no_data/failure,
        # returned further above before reaching this point). Ownership
        # verifiably renounced -> long TTL (nothing can change anymore);
        # otherwise -> short TTL (dedup of close re-evaluations of the same
        # still-pending candidate, without significantly delaying the
        # detection of a real change).
        ttl = _RENOUNCED_CACHE_TTL_S if security.ownership_verifiably_renounced else _SHORT_CACHE_TTL_S
        self._security_cache[cache_key] = (security, time.time() + ttl)
        return security


    # ------------------------------------------------------------------
    # 2. Known malicious address (AML) -- #157, disqualifying layer 1 of
    # the wallet-centric evaluator. Second endpoint from the same provider
    # already integrated above (no new dependency/vendor diligence).
    # ------------------------------------------------------------------
    async def get_address_security(self, address: str, *, chain_id: str = BASE_CHAIN_ID) -> "AddressSecurity":
        """Queries the GoPlus Malicious Address API (AML). Verified live on Base
        (docs/aria-learning-inbox/2026-07-14-veille-registre-wallets-malveillants-157.md,
        14/07), then EXTENDED that same evening to the 13 chain_ids of the
        multi-chain scan (base, ethereum, arbitrum, optimism, polygon, celo,
        gnosis, scroll, zksync, rootstock, unichain, soneium, mode): all 13
        respond `code: 1, "ok"` with the SAME format -- format coverage
        confirmed everywhere WITHOUT an authorization key. NOT the real
        density of malicious data (the live test used a burn address, not an
        actually flagged address) -- and probably variable by chain: the
        `contract_address` field (resolving "is this a contract?") comes
        back indeterminate (`"-1"`) on celo/rootstock/unichain/soneium/mode
        for the same burn address, while it resolves on the other 8 chains --
        an indirect signal that finer coverage exists for some chains. Treat
        as an additional probabilistic filter, never presented as exhaustive,
        whatever the chain -- same doctrine as the rest of the dome: an
        unavailability never counts as "not malicious", it stays unavailable."""
        addr = (address or "").strip()
        if not addr:
            return AddressSecurity(address=addr, available=False, error="adresse vide")

        data, error = await self._get_json(f"/address_security/{addr}", params={"chain_id": chain_id})
        if error is not None:
            return AddressSecurity(address=addr, available=False, error=error)
        if not isinstance(data, dict):
            return AddressSecurity(address=addr, available=False, error=UNAVAILABLE)

        if data.get("code") != 1:
            msg = str(data.get("message") or "").strip()
            return AddressSecurity(
                address=addr, available=False, error=f"{UNAVAILABLE} ({msg or 'code GoPlus != 1'})",
            )

        result = data.get("result")
        if not isinstance(result, dict):
            return AddressSecurity(address=addr, available=False, error=UNAVAILABLE)

        flags = {
            key: True
            for key, raw in result.items()
            if key not in _ADDRESS_SECURITY_META_FIELDS and _tri(raw) is True
        }
        return AddressSecurity(address=addr, flags=flags, is_malicious=bool(flags), available=True, error=None)


goplus_client = GoPlusClient()
