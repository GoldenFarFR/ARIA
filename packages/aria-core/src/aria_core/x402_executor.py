"""Generic x402 payment execution mechanism (#202) -- independent of the
final resource (#199 not yet decided). Serves as a common layer regardless
of the first paid service (Nansen pay-per-call, x402stock.xyz, CoinGecko
premium, ...) -- laying this infrastructure now rather than coupling it to
the first choice.

Explicit operator decision (16/07, CLAUDE.md) on x402 micropayment autonomy:
no Telegram click per call (incompatible with the protocol's machine speed,
~200ms/call) -- a "verify after" model instead of "validate before": a hard
spending cap in the code (``x402_budget.py``, $5/week), a ``/stop``
kill-switch on top of it, every call logged and auditable. Scope STRICTLY
limited to data/API micropayments (cents) -- does NOT touch and does NOT
redefine the absolute rule of human validation on trading with real capital
(swaps, positions), which stays on its own separate, unchanged path
(``agent_wallet_pilot.py``, ``wallet_guard.py``).

Strict order of each attempt (fail-closed at every step):
  1. HTTP request to the resource. If the response is NOT 402 -> returned
     as-is, NOTHING logged in ``x402_budget`` (no payment involved, graceful
     degradation).
  2. If 402: ``/stop`` kill-switch (``outgoing_pause.is_paused(strict=True)``)
     -- same doctrine as ``agent_wallet_pilot.py``. Checked FIRST, before
     even knowing how much the resource costs -- it's the widest gate, it
     doesn't depend on any data already read.
  3. 402 body parsed defensively (x402 v1 schema, cf. ``services/x402.py``)
     -- non-USDC asset or unreadable amount -> blocked (the ``x402_budget``
     cap is denominated in dollars, it means nothing for another asset).
  4. Weekly cap (``x402_budget.can_spend(amount)``) -- refuses and logs if
     the amount would exceed the remaining budget of the calendar week.
  5. REAL wallet balance (injected ``balance_fn``, same pattern as
     ``agent_wallet_pilot.attempt_swap``) -- fail-closed if unavailable or
     insufficient.
  6. Signing + building the payment header (injected ``pay_fn`` -- never a
     real SDK call here; see ``x402_cdp_signer.py`` for the real CDP
     implementation).
  7. New HTTP request with the ``X-PAYMENT`` header.
  8. ``x402_budget.record_spend()`` with the REAL outcome (ok/failed/blocked)
     -- never just successes, a refusal or failure must stay traced and
     auditable.

No private key here (same doctrine as the whole dome): ``pay_fn``/``balance_fn``
are injected by the caller -- the real execution (CDP signing, balance
reading) runs on a dedicated adapter's side, never in this module. Zero
network call in the test suite (``http_fetch_fn``/``balance_fn``/``pay_fn``
always fakes in tests)."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from aria_core import outgoing_pause, x402_attempt_journal, x402_budget
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

logger = logging.getLogger(__name__)

# 23/07 -- named constant (not an inline string) for the ONE failure reason
# where real money may already have moved: `pay_fn` succeeded (the signed
# authorization was handed to the provider) but the follow-up request to
# actually fetch the paid resource then raised (network timeout/error) --
# the provider's facilitator may have already settled on-chain even though
# this client never saw a definitive response. Every OTHER "failed" reason
# in this module (signature échouée, toujours 402 après paiement) means no
# money moved (the header was never produced, or the provider explicitly
# refused settlement) -- only this one is genuinely ambiguous.
#
# `agent_wallet_monitor.py`'s real-money classifier matches on this SAME
# constant (never a hand-copied string) to recognize a real on-chain outflow
# that correlates with this spend as a known x402 payment rather than an
# "unexpected_outflow" false alarm -- real incident, 23/07 (twit.sh/fBOMB,
# 0.01 USDC settled on-chain, logged here as "failed" because the read of
# the response body itself failed).
REASON_PREFIX_PAID_BUT_FETCH_FAILED = "requête payée échouée"

_HTTP_TIMEOUT = 12.0
X_PAYMENT_HEADER = "X-PAYMENT"
# 19/07 -- real bug found while testing lionx402 (real v2 provider): the
# x402 v2 protocol expects the settled payment under a DIFFERENT header name
# ("PAYMENT-SIGNATURE", confirmed in x402/http/constants.py of the installed
# official SDK) -- always sending "X-PAYMENT" (legacy v1) made the paid
# request fail on every v2 provider, even after a successful signature.
# `requirement["x402Version"]` (already re-injected by
# `_extract_payment_requirement`) says which name to use -- never guessed.
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
_SUPPORTED_ASSET = "USDC"
_USDC_DECIMALS = 1_000_000  # native Base USDC -- 6 decimals
# Network declared by the 402 never taken at face value (same doctrine as
# the forced slippage, 09/07 -- never trust a value provided by a third
# party): accepted forms for Base mainnet, flat (v1 schema, services/x402.py)
# or CAIP-2 (v2).
_ALLOWED_NETWORKS = {"base", "eip155:8453"}

# 03/08 -- security-review workflow finding: nothing bounded a single
# provider from requesting up to the entire remaining weekly budget in one
# call. Hard per-transaction cap, checked IN ADDITION to (never instead of)
# the weekly cap -- ~3.3x the most expensive provider actually in prod today
# (Quick Intel, $0.03/call), leaving room for a future provider without
# needing to retouch this constant on every addition.
MAX_TRANSACTION_USD = 0.10

# 03/08 -- same workflow finding: only the NETWORK was ever checked against
# an allowlist, not the provider's own domain -- any URL could be called.
# Derived from the domain alone (``urlparse(url).hostname``), never touching
# any of the 7 existing callers' own call signature. Verified against each
# service's real URL constant before listing here, not guessed.
_ALLOWED_PROVIDER_DOMAINS = frozenset({
    "x402.twit.sh",                                          # services/twitsh.py
    "api.blockscout.com",                                     # services/blockscout_x402.py
    "x402-cybercentry-wallet-verification.up.railway.app",    # services/cybercentry.py
    "x402.quickintel.io",                                     # services/quickintel.py
    "x402.ottoai.services",                                   # services/ottoai.py, services/otto_ai.py
    "blockrun.ai",                                             # services/blockrun_kalshi.py
    # 05/08 -- ARIA's OWN x402 seller domain (vanguard/backend/app/x402_seller.py),
    # added for the operator-requested self-payment test: ARIA pays her own
    # real mainnet product from her own real x402 payer wallet, closing the
    # loop end-to-end with money she already controls on both sides (never a
    # third party). Zero external-domain risk -- this is ARIA's own prod API.
    "api.ariavanguardzhc.com",
})


@dataclass(frozen=True)
class HttpResult:
    """Minimal HTTP response, decoupled from ``httpx`` -- trivially fakeable in tests."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class X402ExecutionResult:
    status: str  # "ok" | "blocked" | "failed"
    reason: str = ""
    amount_usd: float = 0.0
    http_status: int | None = None
    body: bytes = b""


BalanceFn = Callable[[], Awaitable[float | None]]
PayFn = Callable[[dict[str, Any]], Awaitable[str]]
HttpFetchFn = Callable[..., Awaitable[HttpResult]]


async def _default_http_fetch(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
    timeout: float = _HTTP_TIMEOUT, json_body: dict[str, Any] | None = None,
) -> HttpResult:
    """Real default implementation (httpx). Never used in tests --
    always replaced by a fake (cf. test_x402_executor.py).

    21/07 -- ``timeout`` made configurable (default unchanged, ``_HTTP_TIMEOUT``):
    found while testing Blockscout in live conditions that an
    ``httpx.ReadTimeout`` (silent -- its ``str()`` is empty, hence a phantom
    ``reason=""`` in ``X402ExecutionResult``) was hitting the payment
    settlement round, slower than a simple API call (on-chain verification
    on the provider's side). The 12s default stays unchanged for every
    provider already in prod (Cybercentry/Otto AI/twit.sh, never an issue
    observed) -- only a caller that explicitly passes a longer ``timeout``
    changes behavior.

    30/07 -- ``json_body`` added (default ``None``, historical GET-only
    behavior unchanged): every provider so far (Cybercentry/Otto AI/twit.sh)
    identifies the resource via the URL alone (query string or path).
    Quick Intel's ``POST /v1/scan/full`` is the first that requires a JSON
    body (``{"chain", "tokenAddress"}``) on BOTH the unpaid challenge
    request and the paid retry -- passed through as-is to ``httpx``."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url, headers=headers or {}, json=json_body)
    return HttpResult(status_code=r.status_code, headers=dict(r.headers), body=r.content)


def _extract_payment_requirement(
    body: bytes, headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Defensive parsing of the 402 (x402 schema: ``{"accepts": [...]}``, cf.
    ``services/x402.py::payment_required_response``) -- returns the first
    ``accepts[0]`` if present and well-formed, otherwise ``None`` (graceful
    degradation, never an exception).

    Real bug fixed on 17/07 (found while testing 3 real providers from the
    x402 Bazaar catalog): several providers (ottoai, lonestaroracle --
    verified live) do NOT put the offer in the JSON body (often empty
    ``{}`` or a custom format with no ``accepts``) but in a
    ``payment-required`` response HEADER, base64-encoded -- confirmed to
    conform to the standard x402 v2 schema once decoded. The body is still
    tried FIRST (faster, no decoding); the header is only tried IF the body
    gave no usable result -- never the reverse, so as not to regress
    providers (e.g. Cybercentry) that do use the body.

    ``x402Version`` (ROOT of the envelope, NOT inside ``accepts[0]``)
    explicitly re-injected into the returned dict -- real bug found on
    17/07: without this, ``x402_cdp_signer.py`` would fall back to its
    default (1) for EVERY v2 provider, routing the signature to the wrong
    official SDK schema (V1, which only knows flat network names like
    "base", never the CAIP-2 format "eip155:8453" used by real v2 offers)
    -- systematic "No payment requirements match registered schemes"
    failure on every v2 provider.

    30/07 -- real bug found while diligencing Quick Intel's x402 endpoint
    (live 402 captured, no payment made): unlike lionx402/sociavault (offer
    ONLY in the header, empty/custom body), Quick Intel sends a fully valid
    v2 ``accepts`` array in BOTH the JSON body AND the ``payment-required``
    header at once. The body path below resolves first and used to return
    immediately, never looking at the header -- so ``_raw_v2_header`` was
    never attached for x402Version==2 offers resolved from the body. Per
    the installed SDK's own source (``x402HTTPClient.get_payment_required_response``),
    its synthetic-body fallback raises ``ValueError("Invalid payment
    required response")`` for anything other than x402Version==1 -- every
    such provider would fail signing 100% of the time, having never been
    exercised against a real payment before. Fixed by always checking for
    the header and attaching it whenever the resolved offer is v2,
    regardless of which path (body or header) produced it."""
    header_value = None
    for key, value in (headers or {}).items():
        if key.lower() == "payment-required":
            header_value = value
            break

    requirement: dict[str, Any] | None = None
    try:
        data = json.loads(body.decode("utf-8"))
        accepts = data.get("accepts") if isinstance(data, dict) else None
        if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
            first = dict(accepts[0])
            first.setdefault("x402Version", data.get("x402Version", 1))
            requirement = first
    except Exception:  # noqa: BLE001 — unreadable body, retry via the header
        pass

    if requirement is None:
        if not header_value:
            return None
        try:
            padded = header_value + "=" * (-len(header_value) % 4)
            decoded = base64.b64decode(padded)
            data = json.loads(decoded.decode("utf-8"))
        except Exception:  # noqa: BLE001 — unreadable header, graceful degradation
            return None
        accepts = data.get("accepts") if isinstance(data, dict) else None
        if not isinstance(accepts, list) or not accepts:
            return None
        first = accepts[0]
        if not isinstance(first, dict):
            return None
        first = dict(first)
        first.setdefault("x402Version", data.get("x402Version", 1))
        requirement = first

    # 19/07 -- real bug found while testing 2 real v2 providers from the
    # Bazaar catalog (lionx402, sociavault): x402_cdp_signer.py was
    # reconstructing a synthetic body and passing a no-op header getter to
    # the official SDK, which STRICTLY requires the raw header to decode a
    # v2 offer (its "body" fallback only accepts x402Version==1) --
    # systematic "Invalid payment required response" failure on every v2
    # provider despite a perfectly valid offer. Internal key ("_" prefix)
    # carrying the RAW header as-is -- never in PayFn (unchanged signature,
    # no existing fake pay_fn broken), x402_cdp_signer.py reads it if
    # present and ignores it otherwise (V1/Cybercentry unchanged).
    # 30/07 -- widened (see docstring) to also cover a v2 offer resolved
    # from the BODY when a header is also present -- never overwrites an
    # explicit ``_raw_v2_header`` a future caller might have set upstream.
    if requirement.get("x402Version") == 2 and header_value:
        requirement.setdefault("_raw_v2_header", header_value)
    return requirement


def _amount_to_usd(requirement: dict[str, Any]) -> float | None:
    """Converts the amount (smallest unit, e.g. 6 USDC decimals) into dollars.
    Fail-closed: non-USDC asset or malformed amount -> ``None``.

    Real bug fixed on 17/07 (never exercised against a real facilitator
    until now, ``x402_executor.py`` says so itself at the top of the file --
    first real call made tonight against Cybercentry, settled via the
    official Coinbase CDP facilitator): the REAL x402 v1 schema has no
    ``amount`` field (``maxAmountRequired``, a string in the smallest unit)
    and ``asset`` is the token's CONTRACT ADDRESS (e.g. USDC on Base =
    ``0x8335...``), never the literal string ``"USDC"`` -- with the old
    code, EVERY real call would have been rejected by this guard
    (fail-closed, so safe, but also never working). Both old conventions
    remain accepted as a fallback, in case another facilitator uses them
    one day -- never a regression for a future caller."""
    asset = str(requirement.get("asset") or "").strip()
    is_usdc = asset.upper() == _SUPPORTED_ASSET or asset.lower() == USDC_BASE_ADDRESS.lower()
    if not is_usdc:
        return None
    raw = requirement.get("maxAmountRequired", requirement.get("amount"))
    try:
        return float(raw) / _USDC_DECIMALS
    except (TypeError, ValueError):
        return None


async def fetch_paid_resource(
    url: str,
    *,
    resource: str,
    provider: str = "",
    method: str = "GET",
    balance_fn: BalanceFn,
    pay_fn: PayFn,
    http_fetch_fn: HttpFetchFn = _default_http_fetch,
    contract: str = "",
    token_symbol: str = "",
    timeout: float | None = None,
    json_body: dict[str, Any] | None = None,
) -> X402ExecutionResult:
    """Attempts to fetch ``url``, automatically paying if the resource responds 402.

    ``resource``/``provider`` identify the call in the ``x402_budget`` log
    (auditability -- never an anonymous payment). ``balance_fn``/``pay_fn``
    are injected by the caller: in production, ``x402_cdp_signer.py``
    provides the real implementation (dedicated CDP wallet); in tests,
    always fakes. ``contract``/``token_symbol`` (19/07, #143): the token
    concerned if applicable -- passed through as-is to
    ``x402_budget.record_spend`` so every payment is traceable back to the
    token without after-the-fact forensic reconstruction.

    ``timeout`` (21/07): ``None`` by default -- HISTORICAL behavior
    unchanged (``http_fetch_fn`` manages its own default timeout, never an
    extra kwarg sent to a custom ``http_fetch_fn`` that doesn't expect it,
    e.g. test fakes). Only passed through if explicitly provided by the
    caller -- cf. Blockscout, whose payment settlement turned out to be
    slower than the 12s default (silent ``httpx.ReadTimeout``, its
    ``str()`` is empty -- which explained a phantom ``reason=""``)).

    ``json_body`` (30/07): ``None`` by default -- HISTORICAL behavior
    unchanged for every GET-only provider (Cybercentry/Otto AI/twit.sh).
    Quick Intel's ``POST /v1/scan/full`` identifies the resource via a JSON
    body (``{"chain", "tokenAddress"}``), not the URL alone -- sent as-is on
    BOTH the unpaid challenge request and the paid retry (the provider must
    see the identical request both times, or it could charge for one token
    while returning data for another)."""
    # 03/08 -- checked BEFORE even the unpaid challenge request (not just
    # before payment): a non-whitelisted domain never receives ANY request,
    # not even a free one -- minor information-leak-avoidance, zero cost.
    hostname = (urlparse(url).hostname or "").lower()
    if hostname not in _ALLOWED_PROVIDER_DOMAINS:
        return await _blocked(
            resource, provider, 0.0,
            reason=f"domaine non autorisé ({hostname!r}) -- jamais d'appel hors de l'allowlist",
            contract=contract, token_symbol=token_symbol,
        )

    fetch_kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    if json_body is not None:
        fetch_kwargs["json_body"] = json_body
    try:
        first = await http_fetch_fn(url, method=method, headers=None, **fetch_kwargs)
    except Exception as exc:  # noqa: BLE001
        return X402ExecutionResult(status="failed", reason=f"requête initiale échouée : {exc}")

    if first.status_code != 402:
        # No payment involved -- graceful degradation, nothing to log.
        return X402ExecutionResult(status="ok", http_status=first.status_code, body=first.body)

    # /stop kill-switch: the widest gate, checked before even knowing how
    # much the resource costs -- same doctrine as agent_wallet_pilot.py.
    if outgoing_pause.is_paused(strict=True):
        return await _blocked(
            resource, provider, 0.0,
            reason=outgoing_pause.blocked_notice("Ce paiement x402"),
            contract=contract, token_symbol=token_symbol,
        )

    requirement = _extract_payment_requirement(first.body, first.headers)
    if requirement is None:
        return await _blocked(
            resource, provider, 0.0, reason="corps 402 illisible/mal formé",
            contract=contract, token_symbol=token_symbol,
        )

    # 17/07 -- real bug found while testing real x402 v2 providers (Bazaar):
    # the official Python x402 SDK (x402_cdp_signer.py, PaymentRequiredV1)
    # requires "maxAmountRequired" (never "amount") AND "resource" (the URL)
    # INSIDE each accepts[0] object to build the signature -- but the wire
    # (v2, confirmed live) sends "amount" without "resource" at that level
    # (the resource field lives at the 402's root level, not per offer).
    # Without this normalization, pay_fn would fail with "Field required" on
    # both fields for EVERY v2 provider -- Cybercentry (v1, already has
    # "maxAmountRequired") is unaffected, setdefault never touches a field
    # already present.
    requirement.setdefault("maxAmountRequired", requirement.get("amount"))
    requirement.setdefault("resource", url)

    amount_usd = _amount_to_usd(requirement)
    if amount_usd is None:
        return await _blocked(
            resource, provider, 0.0,
            reason=f"actif non supporté ou montant illisible ({requirement.get('asset')!r})",
            contract=contract, token_symbol=token_symbol,
        )

    # 03/08 -- security-review workflow finding: nothing bounded a single
    # provider's REQUESTED amount before this point -- checked before the
    # weekly cap (a provider could otherwise request up to the entire
    # remaining budget in one call and still pass ``can_spend``).
    if amount_usd > MAX_TRANSACTION_USD:
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"montant {amount_usd}$ > plafond dur par transaction {MAX_TRANSACTION_USD}$",
            contract=contract, token_symbol=token_symbol,
        )

    # 17/07 -- settlement address of the 402, already known here (no extra
    # network call): logged with every attempt so agent_wallet_monitor.py
    # can correlate a detected on-chain movement with an already-known x402
    # payment (cf. comment in x402_budget.py::_ADDED_COLUMNS -- found after
    # a real "OUTFLOW NOT INITIATED BY ARIA" false positive on the very
    # first real payment).
    pay_to = str(requirement.get("payTo") or "")

    network = str(requirement.get("network") or "").lower()
    if network not in _ALLOWED_NETWORKS:
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"réseau non autorisé ({network!r}) -- jamais signer hors de l'allowlist",
            pay_to=pay_to, contract=contract, token_symbol=token_symbol,
        )

    # 10/08 -- safety net, written BEFORE any reservation/payment attempt and
    # INDEPENDENT of SQLite (real incident: a legitimate twit.sh payment
    # settled on-chain with ZERO trace in x402_spend_log -- not even a
    # "pending" row, meaning try_reserve itself likely never completed its
    # write, plausibly SQLite contention from a concurrent process on the
    # same aria.db). A plain file append can't be starved the same way --
    # never raises, never blocks a real payment attempt either way.
    x402_attempt_journal.record_attempt(
        resource=resource, provider=provider, amount_usd=amount_usd, pay_to=pay_to,
        contract=contract, token_symbol=token_symbol,
    )

    # 03/08 -- atomic reserve, replacing can_spend() (check-then-act, unsafe
    # under concurrent callers -- see try_reserve's own docstring for the
    # real incident that motivated this). Reserves BEFORE the real balance
    # check/signature/paid fetch below -- any failure from here on MUST
    # settle() this reservation (never leave it "pending", it would still
    # count against the budget for PENDING_TIMEOUT_MINUTES).
    # 10/08 -- try_reserve itself is now wrapped: an exception here (e.g. a
    # SQLite contention error) used to propagate straight out of this
    # function, past every OTHER try/except in this file, and get silently
    # swallowed by callers' own defensive `except Exception: return []`
    # (twitsh.py and siblings) -- the real cause of the incident above. Never
    # again allowed to escape unlogged: caught, logged loudly, reported as a
    # normal "failed" result like every other failure mode in this function.
    try:
        reservation_id = await x402_budget.try_reserve(
            amount_usd, resource=resource, provider=provider, contract=contract, token_symbol=token_symbol,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "x402 try_reserve raised (%s, %s$, pay_to=%s) -- payment journal entry is the only "
            "trace if a payment is attempted below: %s", resource, amount_usd, pay_to, exc,
        )
        return X402ExecutionResult(
            status="failed", reason=f"réservation budget échouée : {exc}", amount_usd=amount_usd,
        )
    if reservation_id is None:
        return await _blocked(
            resource, provider, amount_usd,
            reason=f"plafond hebdomadaire x402 dépassé ({amount_usd}$ demandé)",
            pay_to=pay_to, contract=contract, token_symbol=token_symbol,
        )

    try:
        balance_usd = await balance_fn()
    except Exception as exc:  # noqa: BLE001
        reason = f"solde réel indisponible (fail-closed) : {exc}"
        await _settle_safe(reservation_id, status="failed", reason=reason, pay_to=pay_to)
        return X402ExecutionResult(status="blocked", reason=reason, amount_usd=amount_usd)
    if balance_usd is None:
        reason = "solde réel indisponible (fail-closed) : balance_fn a renvoyé None"
        await _settle_safe(reservation_id, status="failed", reason=reason, pay_to=pay_to)
        return X402ExecutionResult(status="blocked", reason=reason, amount_usd=amount_usd)
    if amount_usd > balance_usd:
        reason = f"montant {amount_usd}$ > solde réel {balance_usd}$"
        await _settle_safe(reservation_id, status="failed", reason=reason, pay_to=pay_to)
        return X402ExecutionResult(status="blocked", reason=reason, amount_usd=amount_usd)

    try:
        payment_header = await pay_fn(requirement)
    except Exception as exc:  # noqa: BLE001
        await _settle_safe(
            reservation_id, status="failed", reason=f"signature échouée : {exc}", pay_to=pay_to,
        )
        return X402ExecutionResult(status="failed", reason=str(exc), amount_usd=amount_usd)

    payment_header_name = (
        PAYMENT_SIGNATURE_HEADER if requirement.get("x402Version") == 2 else X_PAYMENT_HEADER
    )
    try:
        paid = await http_fetch_fn(
            url, method=method, headers={payment_header_name: payment_header}, **fetch_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        await _settle_safe(
            reservation_id, status="failed",
            reason=f"{REASON_PREFIX_PAID_BUT_FETCH_FAILED} : {exc}", pay_to=pay_to,
        )
        return X402ExecutionResult(status="failed", reason=str(exc), amount_usd=amount_usd)

    if paid.status_code == 402:
        await _settle_safe(
            reservation_id, status="failed",
            reason="toujours 402 après paiement (règlement refusé)", pay_to=pay_to,
        )
        return X402ExecutionResult(
            status="failed", reason="toujours 402 après paiement", amount_usd=amount_usd,
            http_status=402,
        )

    await _settle_safe(reservation_id, status="ok", pay_to=pay_to)
    return X402ExecutionResult(
        status="ok", amount_usd=amount_usd, http_status=paid.status_code, body=paid.body,
    )


async def _settle_safe(reservation_id: int, *, status: str, reason: str = "", pay_to: str = "") -> None:
    """Wraps x402_budget.settle() -- 10/08, real incident: this call is the
    ONE place in the whole payment flow where a failure previously propagated
    straight out of fetch_paid_resource, past every callsite's own broad
    `except Exception: return []` (twitsh.py and siblings), leaving a REAL
    on-chain payment with zero trace anywhere. Never raises -- if the DB
    write itself fails, this is now loud (logger.error) instead of silent;
    x402_attempt_journal's entry (written before any reservation was even
    attempted) remains the one thing that can never be lost this way."""
    try:
        await x402_budget.settle(reservation_id, status=status, reason=reason, pay_to=pay_to)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "x402_budget.settle FAILED (reservation_id=%s, status=%s, pay_to=%s) -- a real "
            "payment outcome may now be untracked in x402_spend_log: %s",
            reservation_id, status, pay_to, exc,
        )


async def _blocked(
    resource: str, provider: str, amount_usd: float, *, reason: str, pay_to: str = "",
    contract: str = "", token_symbol: str = "",
) -> X402ExecutionResult:
    logger.warning("x402 payment blocked (%s): %s", resource, reason)
    await x402_budget.record_spend(
        resource=resource, provider=provider, amount_usd=amount_usd,
        status="blocked", reason=reason, pay_to=pay_to,
        contract=contract, token_symbol=token_symbol,
    )
    return X402ExecutionResult(status="blocked", reason=reason, amount_usd=amount_usd)
