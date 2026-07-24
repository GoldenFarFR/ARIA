"""x402 SELLER service layer -- ARIA sells her own synthesized judgment (#39,
operator decision 23/07). The mirror of every real-money mechanism so far: this
is the first where ARIA RECEIVES capital rather than spends it.

Scope of THIS module: the framework-agnostic service logic -- gating, the
receiving wallet, the pricing catalog, and building a validated x402
``ResourceConfig`` for each product. It does NOT contain the HTTP route nor the
verify/settle wiring against a live facilitator -- that final integration lives
in the FastAPI host and is validated end-to-end on TESTNET (operator's own test
plan: pay a tiny real x402 from their own wallet to ARIA's, base-sepolia first).

Key safety property (verified against x402 v2.16.0): RECEIVING an x402 payment
never requires ARIA's private key -- the buyer signs the EIP-3009 authorization,
the facilitator settles it, ARIA's side only needs the public receiving
ADDRESS. So this seller path exposes no signing key, unlike every spending path.

Defense-in-depth gating (both OFF by default):
  - ``ARIA_X402_SELLER_ENABLED`` -- the whole seller off unless explicitly on.
  - ``ARIA_X402_SELLER_MAINNET`` -- even when the seller is on, it defaults to
    the base-sepolia TESTNET facilitator; real Base mainnet receiving requires
    this SECOND flag, set only after the testnet self-payment test passes. A
    single careless "enable" can never accidentally take real mainnet money."""
from __future__ import annotations

import os

# Receiving wallet -- aria-wallet-X402-EVM (see agent_wallet_cdp_adapter.WALLET_NAME).
# Public address only; the receiving side never signs (EIP-3009, buyer-signed).
# Operator decision 23/07: this is the x402 wallet. NOTE it is still shared with
# the old EOA pilot until the Smart Account migration retires that pilot (#41).
ARIA_X402_RECEIVING_ADDRESS = "0xF04625162b616c5ad9788811b7be8CDd425B37Ef"

# EIP-3009 "exact" scheme -- the only one this seller offers (a fixed price per
# call, the buyer authorizes exactly that amount).
_SCHEME = "exact"

# Pricing catalog. STARTING values (07/23) -- to calibrate on real COGS before
# any mainnet launch (same "verify, never guess" doctrine as API throttling): a
# cached composite score costs ARIA almost nothing to serve -> cheap; a fresh
# full consultation triggers a real scan (web research + on-chain security +
# one LLM call) -> must cover real per-call cost + margin. Only ARIA's OWN
# synthesized judgment is priced here, never a raw third-party data
# pass-through. Prices are USDC (the "$" form x402 accepts).
PRICING_CATALOG: dict[str, str] = {
    # ARIA's own composite wallet score (percentile + confidence), already cached.
    "wallet_score": "$0.02",
    # Full synthesized token verdict served from cache (no fresh scan).
    "token_analysis_cached": "$0.10",
    # Full synthesized token verdict forcing a fresh scan (real network COGS).
    "token_analysis_fresh": "$0.50",
}

# CAIP-2 chain identifiers -- verified against the installed x402 2.16.0 SDK
# (server_base.py::has_registered_scheme does an EXACT string match against
# whatever was passed to x402ResourceServer.register(), with no legacy-name
# normalization; every register()/PaymentOption example in the SDK itself
# uses CAIP-2, e.g. "eip155:8453"). A previous version of this constant used
# the plain legacy names ("base"/"base-sepolia") -- harmless while this module
# was never wired to a real server, but it would have silently matched no
# registered scheme (no payment ever verifiable) the moment it was actually
# mounted, since the FastAPI host registers schemes under CAIP-2 (see
# vanguard/backend/app/x402_seller.py). Found and fixed during the 24/07
# reconciliation of the two divergent seller modules (#59).
_TESTNET_NETWORK = "eip155:84532"
_MAINNET_NETWORK = "eip155:8453"


def seller_enabled() -> bool:
    """Master gate, OFF by default (fail-closed)."""
    return os.environ.get("ARIA_X402_SELLER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def seller_mainnet_enabled() -> bool:
    """Second gate: real Base mainnet receiving. OFF by default -> testnet.
    Required IN ADDITION to ``seller_enabled`` for any real mainnet money."""
    return os.environ.get("ARIA_X402_SELLER_MAINNET", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def resolve_network() -> str:
    """The network the seller settles on: base-sepolia (testnet) unless the
    mainnet gate is explicitly on. Defense in depth -- enabling the seller alone
    never touches real mainnet capital."""
    return _MAINNET_NETWORK if seller_mainnet_enabled() else _TESTNET_NETWORK


def price_for(product: str) -> str | None:
    """Catalog price for ``product`` (USDC), or ``None`` for an unknown product
    (fail-closed -- an unpriced product is never sold)."""
    return PRICING_CATALOG.get(product)


def build_resource_config(product: str):
    """Builds the validated x402 ``ResourceConfig`` for ``product``: pay_to =
    ARIA's receiving address, price from the catalog, network from the mainnet
    gate. Returns ``None`` if the seller is disabled or the product is unknown
    (fail-closed -- never assembles a payable config when it shouldn't).
    Imports ``x402`` lazily so this module (and its tests) load without the
    dependency present."""
    if not seller_enabled():
        return None
    price = price_for(product)
    if price is None:
        return None
    from x402.server import ResourceConfig

    return ResourceConfig(
        scheme=_SCHEME,
        pay_to=ARIA_X402_RECEIVING_ADDRESS,
        price=price,
        network=resolve_network(),
    )


def deliver_scrubbed(product: str, analysis: dict) -> dict:
    """The paid payload for ``product``: ARIA's synthesized analysis with every
    upstream provider name scrubbed and raw provider fields dropped (see
    ``skills.x402_analysis_export.build_sellable_analysis``). Pure, no money
    moves here -- this is what the buyer receives AFTER the facilitator has
    settled their payment."""
    from aria_core.skills.x402_analysis_export import build_sellable_analysis

    payload = build_sellable_analysis(analysis)
    payload["product"] = product
    return payload
