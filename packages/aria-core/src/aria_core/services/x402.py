"""x402 seam — Base agentic payment protocol (HTTP 402 Payment Required).

ANTICIPATION (motto): this module PLACES the anchor point for Base's agentic
economy (x402 / on-chain USDC payments) without activating anything live. It
is **gated OFF by default** (`ARIA_X402_ENABLED`) and respects the guardrail:

  - **No automatic financial spending**: the "ARIA pays" side ONLY builds a
    PROPOSAL marked `requires_human=True`. It never executes a payment itself
    (human validation via Telegram/Tangem, like wallet_guard). Nothing to
    sign here.
  - **No key on the server**: this module holds, generates, or reads no key.
  - **Graceful degradation**: disabled or misconfigured -> neutral return
    (`None` / invalid), never a bubbling exception. Fail-closed: when in
    doubt, refuse.
  - **Isolated external client**: the only network call (verifying a
    settlement via a facilitator) is a `httpx` client with a timeout,
    tolerant to failures.

Two directions, two risk levels:
  1. **ARIA collects** (revenue): `build_payment_requirement` /
     `payment_required_response` build the payment demand (402) that would
     gate a premium resource. No movement of ARIA's funds. Safe.
  2. **ARIA pays** (spend): `propose_payment` returns a proposal to be
     validated by the operator. **Never autonomous execution** (guardrail, rule 3).

Live wiring (when the day comes, operator-gated): set `ARIA_X402_ENABLED=1`,
`ARIA_X402_PAY_TO=<Base collection address>`,
`ARIA_X402_FACILITATOR_URL=<facilitator>`, then wire
`payment_required_response` onto the showcase's premium resource.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

# Default network and asset of Base's agentic economy (native USDC on Base).
_DEFAULT_NETWORK = "base"
_DEFAULT_ASSET = "USDC"
_HTTP_TIMEOUT = 12.0


def x402_enabled() -> bool:
    """Seam gated OFF by default. Nothing x402 activates until this flag is set."""
    return os.environ.get("ARIA_X402_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _pay_to() -> str:
    """Base collection address (ARIA receives). Never a key, just a public address."""
    return (os.environ.get("ARIA_X402_PAY_TO", "") or "").strip()


def _facilitator_url() -> str:
    return (os.environ.get("ARIA_X402_FACILITATOR_URL", "") or "").strip().rstrip("/")


@dataclass
class X402PaymentRequirement:
    """Payment demand (ARIA-collects side) — used to gate a premium resource."""
    scheme: str
    network: str
    asset: str
    amount: str            # amount in smallest unit (string, to avoid losing precision)
    pay_to: str
    resource: str
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "network": self.network, "asset": self.asset,
            "amount": self.amount, "payTo": self.pay_to, "resource": self.resource,
            "description": self.description,
        }


@dataclass
class X402Verification:
    """Result of verifying a settlement. Fail-closed: invalid by default."""
    valid: bool = False
    reason: str = "x402 disabled"
    tx_hash: str | None = None


@dataclass
class X402PaymentProposal:
    """ARIA-PAYS side — PROPOSAL only. `requires_human` always True, never executed."""
    amount: str
    to: str
    resource: str
    network: str = _DEFAULT_NETWORK
    asset: str = _DEFAULT_ASSET
    requires_human: bool = True
    status: str = "proposed"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount, "to": self.to, "resource": self.resource,
            "network": self.network, "asset": self.asset,
            "requires_human": True, "status": self.status, "reason": self.reason,
        }


def build_payment_requirement(
    resource: str,
    amount: str,
    *,
    description: str = "",
    scheme: str = "exact",
    network: str = _DEFAULT_NETWORK,
    asset: str = _DEFAULT_ASSET,
) -> X402PaymentRequirement | None:
    """Builds the payment demand to gate `resource`. Fail-closed: returns
    `None` if the seam is OFF or if the collection address isn't configured."""
    if not x402_enabled():
        return None
    pay_to = _pay_to()
    if not resource or not amount or not pay_to:
        return None
    return X402PaymentRequirement(
        scheme=scheme, network=network, asset=asset, amount=str(amount),
        pay_to=pay_to, resource=resource, description=description or resource,
    )


def payment_required_response(requirement: X402PaymentRequirement | None) -> dict[str, Any] | None:
    """HTTP 402 envelope a future web gateway would return to demand payment.
    Returns `None` (so no gating) if the demand is absent — graceful
    degradation: without x402 config, the resource is simply not gated by
    this mechanism."""
    if requirement is None:
        return None
    return {
        "status": 402,
        "headers": {"X-Payment-Required": "x402"},
        "body": {"x402Version": 1, "accepts": [requirement.as_dict()]},
    }


async def verify_settlement(payload: dict[str, Any]) -> X402Verification:
    """Verifies a settlement with the facilitator (graceful degradation, fail-closed).

    `payload` = proof of payment provided by the client (decoded X-PAYMENT
    header). We trust NOTHING: validity comes from the facilitator, not the
    client. Any failure/timeout -> invalid (we refuse rather than wrongly
    grant access)."""
    if not x402_enabled():
        return X402Verification(valid=False, reason="x402 disabled")
    url = _facilitator_url()
    if not url:
        return X402Verification(valid=False, reason="no facilitator configured")
    if not isinstance(payload, dict) or not payload:
        return X402Verification(valid=False, reason="empty payload")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(f"{url}/verify", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        # Guardrail: a facilitator failure doesn't break the flow AND doesn't grant access.
        return X402Verification(valid=False, reason="facilitator unreachable")
    if not isinstance(data, dict):
        return X402Verification(valid=False, reason="bad facilitator response")
    valid = bool(data.get("isValid") or data.get("valid"))
    return X402Verification(
        valid=valid,
        reason=str(data.get("reason") or ("ok" if valid else "not settled")),
        tx_hash=(data.get("txHash") or data.get("transaction") or None),
    )


def propose_payment(*, amount: str, to: str, resource: str, reason: str = "") -> X402PaymentProposal:
    """ARIA-PAYS side: builds a PROPOSAL to be validated by the operator.
    NEVER executes a payment (guardrail, rule 3: no automatic financial
    execution). The movement of funds, if it ever happens, goes through human
    validation and local signing."""
    return X402PaymentProposal(
        amount=str(amount), to=(to or "").strip(), resource=resource,
        status="proposed", reason=reason or "requires operator validation",
    )
