"""x402 seller-side integration -- ARIA selling her own composite signals to other
agents via the x402 protocol (paid HTTP endpoints, USDC on Base), as opposed to
the existing payer-side client (aria_core.services.x402/x402_executor) which pays
OTHER services.

Gated OFF by default (ARIA_X402_SELLER_ENABLED unset). Fail-closed on top of that:
even with the gate on, x402_seller_ready() also requires a receiving address to be
configured (ARIA_X402_SELLER_PAYTO_ADDRESS) -- a half-configured payment surface
(gate on, no address) never gets mounted.

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
"""
from __future__ import annotations

import os

X402_SELLER_ENABLED = os.getenv("ARIA_X402_SELLER_ENABLED", "").strip().lower() in {"1", "true", "yes"}
X402_SELLER_PAYTO_ADDRESS = os.getenv("ARIA_X402_SELLER_PAYTO_ADDRESS", "").strip()
X402_SELLER_FACILITATOR_URL = os.getenv(
    "ARIA_X402_SELLER_FACILITATOR_URL", "https://x402.org/facilitator"
).strip()

# Base mainnet, CAIP-2 chain id -- the only network this seller integration
# registers a payment scheme for (matches the rest of ARIA's on-chain footprint).
_BASE_MAINNET_CAIP2 = "eip155:8453"


def x402_seller_ready() -> bool:
    """True only if the feature gate is on AND a receiving address is configured.
    Fail-closed on any missing piece -- never a half-configured live payment
    surface, same doctrine as every other real-capital gate in this project."""
    return X402_SELLER_ENABLED and bool(X402_SELLER_PAYTO_ADDRESS)


def mount_x402_seller(app) -> None:
    """Wires the x402 payment middleware onto the FastAPI app. Only call this
    when x402_seller_ready() is True -- the caller (main.py) checks the gate
    once and this function assumes it's safe to mount, so the gate check and
    the mount stay at a single call site rather than duplicated here."""
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI
    from x402.http.types import RouteConfig
    from x402.mechanisms.evm.exact import ExactEvmServerScheme
    from x402.server import x402ResourceServer

    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=X402_SELLER_FACILITATOR_URL))
    server = x402ResourceServer(facilitator)
    server.register(_BASE_MAINNET_CAIP2, ExactEvmServerScheme())

    # Catalog of paid routes. Deliberately small at v0 -- only ARIA's own
    # composite wallet score (already cached in wallet_score_log, zero
    # third-party raw-data re-exposure). Extending this catalog to
    # GitHub/Website/Docs/X substance scores waits on the persisted cache
    # layer (backlog #40) and on written ToS clearance from GoPlus/Blockscout/
    # CabalSpy (docs/conformite-dossier-avocat.md, HANDOFF pending) -- adding a
    # route here without both of those is a compliance/cost mistake, not just
    # a technical one.
    routes = {
        "GET /api/x402/walletscore": RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=X402_SELLER_PAYTO_ADDRESS,
                    price="$0.01",
                    network=_BASE_MAINNET_CAIP2,
                )
            ],
            mime_type="application/json",
            description="ARIA's own composite wallet reputation score (Base wallets, cached)",
        ),
    }
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
