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
"""
from __future__ import annotations

import os

from aria_core import x402_seller as aria_x402_seller

X402_SELLER_FACILITATOR_URL = os.getenv(
    "ARIA_X402_SELLER_FACILITATOR_URL", "https://x402.org/facilitator"
).strip()


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

    Catalog of paid routes deliberately still small at v0 -- only ARIA's own
    composite wallet score (already cached in wallet_score_log, zero
    third-party raw-data re-exposure). Extending this catalog to
    GitHub/Website/Docs/X substance scores (now persisted, see backlog #40)
    still waits on written ToS clearance from GoPlus/Blockscout/CabalSpy
    (docs/conformite-dossier-avocat.md, HANDOFF pending) -- adding a route here
    without that clearance is a compliance mistake, not just a technical one."""
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.server import x402ResourceServer

    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=X402_SELLER_FACILITATOR_URL))
    server = x402ResourceServer(facilitator)
    network = aria_x402_seller.resolve_network()
    server.register(network, ExactEvmServerScheme())

    resource_config = aria_x402_seller.build_resource_config("wallet_score")
    if resource_config is None:
        # x402_seller_ready() already confirmed the gate is on right before this
        # was called (single call site, main.py) -- a None here means the
        # catalog/gate changed between that check and this mount, which should
        # never happen at boot. Fail loud rather than silently mount nothing.
        raise RuntimeError("x402 seller ready but wallet_score resource config unavailable")

    routes = {
        "GET /api/x402/walletscore": RouteConfig(
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
            description="ARIA's own composite wallet reputation score (Base wallets, cached)",
        ),
    }
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
