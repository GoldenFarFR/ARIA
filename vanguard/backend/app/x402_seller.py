"""x402 seller-side integration -- ARIA selling her own composite signals to other
agents via the x402 protocol (paid HTTP endpoints, USDC on Base), as opposed to
the existing payer-side client (aria_core.services.x402/x402_executor) which pays
OTHER services.

24/07 (#59): this file is now a THIN FastAPI wiring layer over
``aria_core.x402_seller`` -- the framework-agnostic gating/pricing/receiving-
address module. Reconciles two implementations that grew independently the
same day (23/07): this file originally hardcoded its own PaymentOption/mainnet
CAIP-2/env-var receiving address, while aria_core.x402_seller carried a safer
double-gate (testnet-first, real mainnet requires a SECOND explicit flag) and a
hardcoded receiving address (never an env var an operator could silently
misconfigure -- same doctrine as ``agent_wallet_pilot.ALLOWED_TRANSFER_ADDRESS``).
Both were inert in prod at the time of this reconciliation (``ARIA_X402_SELLER_ENABLED``
unset) -- no live payment surface was ever affected.

Gated OFF by default (``ARIA_X402_SELLER_ENABLED`` unset, checked live via
``aria_core.x402_seller.seller_enabled()`` -- never baked into a module-level
constant, so a test or a runtime env change is reflected immediately, not just
at import time). No separate "receiving address configured" check remains:
the address is now a hardcoded constant in aria_core.x402_seller, always
present once the gate is on.

Crypto-to-crypto only (USDC on Base) -- no fiat rail for this product. Per
docs/conformite-dossier-avocat.md §7, this scope is what the operator decided can
proceed without waiting on a lawyer review (fiat would re-trigger that gate).

Package: x402[evm,fastapi] (pyproject.toml, pinned >=2.16.0 -- Alpha-status
package, one breaking v1->v2 rewrite already behind it as of this integration,
per the 23/07 feasibility research). Facilitator defaults to the free
x402.org testnet facilitator unless ARIA_X402_SELLER_FACILITATOR_URL points to a
real mainnet-capable one (e.g. a zero-fee provider, or Coinbase's own CDP
facilitator) -- deliberately not defaulting to a mainnet facilitator so a
misconfigured deployment can't accidentally start accepting real payments.

Real bug found and fixed during this reconciliation (verified against the
installed x402 2.16.0 SDK directly, not assumed): ``aria_core.x402_seller.
resolve_network()`` used to return legacy plain network names ("base"/
"base-sepolia"), but ``x402ResourceServer.has_registered_scheme`` does an EXACT
string match against whatever ``register()`` was called with, with no legacy-
name <-> CAIP-2 normalization anywhere in the SDK. Every ``register()``/
``PaymentOption`` example in the SDK itself uses CAIP-2 -- this file already
did, which is exactly why the mismatch would have silently broken payment
verification (no scheme match => no route ever payable) the moment the two
modules were ever wired together, as they are now. Fixed at the source
(aria_core.x402_seller now emits CAIP-2 directly) rather than papered over
here with a translation layer.

04/08 -- CDP mainnet facilitator authentication (operator decision: mainnet
receiving via the CDP-hosted facilitator, free 1,000 tx/month, gas sponsored
by Coinbase -- verified against docs.cdp.coinbase.com, not assumed). The CDP
mainnet facilitator requires a signed JWT (``cdp.auth.generate_jwt``, the
official CDP SDK already used elsewhere in this project for the wallet
pilot -- reused here, never reimplemented) on every request; the free
x402.org testnet facilitator needs no auth at all, which is why this was
never wired before now. ``CdpFacilitatorAuthProvider`` below implements the
x402 SDK's ``AuthProvider`` protocol (``get_auth_headers() -> AuthHeaders``,
verified against the installed SDK: called fresh before EACH request, one
``AuthHeaders`` populated for verify/settle/supported all at once -- cheap,
local signing only, no network call).

Deliberately a DEDICATED CDP key pair (``ARIA_X402_FACILITATOR_CDP_API_KEY_ID``/
``_SECRET``), never the wallet's own ``CDP_API_KEY_ID``/``CDP_API_KEY_SECRET``
-- minimum-permission doctrine already established in this project for every
new CDP key (see docs/HANDOFF_COINBASE_CDP.md): the facilitator only ever
needs to call CDP's x402 verify/settle/supported endpoints, never wallet
signing, so it gets its own narrowly-scoped credential with a different
blast radius than a wallet-authority key.

Fails LOUD (not silently unauthenticated) if the mainnet gate is on but
these credentials are missing -- an authenticated mainnet endpoint called
with no auth headers would just 401 at the first real payment attempt,
which is a worse failure mode (silent, discovered live) than refusing to
mount at boot with a clear error.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from aria_core import x402_seller as aria_x402_seller

X402_SELLER_FACILITATOR_URL = os.getenv(
    "ARIA_X402_SELLER_FACILITATOR_URL", "https://x402.org/facilitator"
).strip()


class CdpFacilitatorAuthProvider:
    """Signs a fresh, short-lived (2min, CDP SDK default) JWT per request via
    ``cdp.auth.generate_jwt`` -- one exact-scoped (method+host+path) token
    for each of verify/settle/supported, all computed together since
    ``get_auth_headers()`` takes no arguments (the x402 SDK's own
    ``AuthProvider`` protocol) but is called fresh immediately before each
    individual request, so the 2min expiry is never at risk."""

    def __init__(self, api_key_id: str, api_key_secret: str, *, host: str, base_path: str) -> None:
        self._api_key_id = api_key_id
        self._api_key_secret = api_key_secret
        self._host = host
        self._base_path = base_path.rstrip("/")

    def _jwt_headers(self, method: str, path: str) -> dict[str, str]:
        from cdp.auth import generate_jwt
        from cdp.auth.utils.jwt import JwtOptions

        token = generate_jwt(
            JwtOptions(
                api_key_id=self._api_key_id,
                api_key_secret=self._api_key_secret,
                request_method=method,
                request_host=self._host,
                request_path=f"{self._base_path}{path}",
            )
        )
        return {"Authorization": f"Bearer {token}"}

    def get_auth_headers(self):
        from x402.http.facilitator_client_base import AuthHeaders

        return AuthHeaders(
            verify=self._jwt_headers("POST", "/verify"),
            settle=self._jwt_headers("POST", "/settle"),
            supported=self._jwt_headers("GET", "/supported"),
        )


def _facilitator_auth_provider() -> CdpFacilitatorAuthProvider | None:
    """``None`` on testnet (no auth needed/supported by the free facilitator).
    On mainnet, builds the CDP auth provider from the DEDICATED facilitator
    key pair -- raises ``RuntimeError`` if the mainnet gate is on but these
    are missing, rather than mounting an authenticated endpoint with no
    auth (see module docstring for why fail-loud beats a silent 401)."""
    if not aria_x402_seller.seller_mainnet_enabled():
        return None
    key_id = os.getenv("ARIA_X402_FACILITATOR_CDP_API_KEY_ID", "").strip()
    key_secret = os.getenv("ARIA_X402_FACILITATOR_CDP_API_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise RuntimeError(
            "ARIA_X402_SELLER_MAINNET is on but ARIA_X402_FACILITATOR_CDP_API_KEY_ID/"
            "_SECRET are not configured -- refusing to mount an authenticated mainnet "
            "facilitator with no credentials."
        )
    parsed = urlsplit(X402_SELLER_FACILITATOR_URL)
    return CdpFacilitatorAuthProvider(
        key_id, key_secret, host=parsed.netloc, base_path=parsed.path,
    )


def x402_seller_ready() -> bool:
    """True only if the gate is on (``aria_core.x402_seller.seller_enabled()``,
    read live -- never a stale module-level constant). Fail-closed doctrine
    unchanged, same as every other real-capital gate in this project; there is
    no longer a separate "address configured" check since the receiving
    address is a hardcoded constant in aria_core.x402_seller, not an operator-
    supplied env var that could be left unset."""
    return aria_x402_seller.seller_enabled()


def mount_x402_seller(app) -> None:
    """Wires the x402 payment middleware onto the FastAPI app. Only call this
    when x402_seller_ready() is True -- the caller (main.py) checks the gate
    once and this function assumes it's safe to mount, so the gate check and
    the mount stay at a single call site rather than duplicated here.

    Catalog of paid routes deliberately still small: ARIA's own composite
    wallet score (already cached in wallet_score_log) and, since 31/07, her
    B20 native-token role-holder safety verdict (services/b20.py) -- both
    zero third-party raw-data re-exposure (ARIA's own computed judgment
    only). Extending this catalog to GitHub/Website/Docs/X substance scores
    (now persisted, see backlog #40) still waits on written ToS clearance
    from GoPlus/Blockscout/CabalSpy (docs/conformite-dossier-avocat.md,
    HANDOFF pending) -- adding a route here without that clearance is a
    compliance mistake, not just a technical one."""
    from x402.extensions.bazaar import (
        OutputConfig,
        bazaar_resource_server_extension,
        declare_discovery_extension,
    )
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.server import x402ResourceServer

    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=X402_SELLER_FACILITATOR_URL, auth_provider=_facilitator_auth_provider())
    )
    server = x402ResourceServer(facilitator)
    # 07/08 -- Bazaar discovery (operator go-ahead): the CDP facilitator only
    # catalogs a route once its PaymentRequired response carries a valid
    # "bazaar" extension block, built by declare_discovery_extension() below.
    # Without register_extension() here, the discovery info would never be
    # enriched with the HTTP method/route template the SDK's own indexer
    # requires -- verified against the really-installed x402 2.18.0 SDK
    # (x402.extensions.bazaar), not assumed from the docs alone (same
    # discipline as every other x402 integration in this file).
    server.register_extension(bazaar_resource_server_extension)
    network = aria_x402_seller.resolve_network()
    server.register(network, ExactEvmServerScheme())

    def _route_config(
        product: str, description: str, discovery: dict | None = None,
    ) -> RouteConfig:
        resource_config = aria_x402_seller.build_resource_config(product)
        if resource_config is None:
            # x402_seller_ready() already confirmed the gate is on right before this
            # was called (single call site, main.py) -- a None here means the
            # catalog/gate changed between that check and this mount, which should
            # never happen at boot. Fail loud rather than silently mount nothing.
            raise RuntimeError(f"x402 seller ready but {product} resource config unavailable")
        return RouteConfig(
            accepts=[
                PaymentOption(
                    scheme=resource_config.scheme,
                    pay_to=resource_config.pay_to,
                    price=resource_config.price,
                    network=resource_config.network,
                    max_timeout_seconds=resource_config.max_timeout_seconds,
                    extra=resource_config.extra,
                )
            ],
            mime_type="application/json",
            description=description,
            extensions=discovery,
        )

    routes = {
        "GET /api/x402/walletscore": _route_config(
            "wallet_score",
            "ARIA's own composite wallet reputation score (Base wallets, cached)",
            discovery=declare_discovery_extension(
                input={"address": "0x1111111111166b7fe7bd91427724b487980afc69"},
                input_schema={
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "Base wallet address, 0x-prefixed, 40 hex chars",
                        },
                    },
                    "required": ["address"],
                },
                output=OutputConfig(
                    example={
                        "wallet": "0x1111111111166b7fe7bd91427724b487980afc69",
                        "composite_percentile": 74,
                    },
                    schema={
                        "properties": {
                            "wallet": {"type": "string"},
                            "composite_percentile": {"type": "number"},
                        },
                    },
                ),
            ),
        ),
        # 31/07 -- B20 native Base token safety verdict (services/b20.py).
        "GET /api/x402/b20score": _route_config(
            "b20_safety",
            "ARIA's own B20 role-holder safety verdict (MINT/PAUSE/BURN_BLOCKED, cache-first)",
            discovery=declare_discovery_extension(
                input={"contract": "0x1111111111166b7fe7bd91427724b487980afc69"},
                input_schema={
                    "properties": {
                        "contract": {
                            "type": "string",
                            "description": "Base token contract address, 0x-prefixed, 40 hex chars",
                        },
                    },
                    "required": ["contract"],
                },
                output=OutputConfig(
                    example={
                        "contract": "0x1111111111166b7fe7bd91427724b487980afc69",
                        "b20_verdict": "MINT",
                        "reason": "mint role held by a non-renounced EOA",
                        "role_holders": {"MINT": ["0xabc0000000000000000000000000000000dead"]},
                        "scanned_at": "2026-08-07T09:00:00+00:00",
                    },
                    schema={
                        "properties": {
                            "contract": {"type": "string"},
                            "b20_verdict": {"type": "string"},
                            "reason": {"type": ["string", "null"]},
                            "role_holders": {"type": "object"},
                            "scanned_at": {"type": ["string", "null"]},
                        },
                    },
                ),
            ),
        ),
    }
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
