"""Real implementation of the ``pay_fn`` expected by
``x402_executor.fetch_paid_resource`` -- signs an x402 payment via the
dedicated CDP wallet (same wallet as ``agent_wallet_cdp_adapter.py``,
identical pattern: lazy import, no key read or handled here).

Signing method verified against the official docs/source code before
writing this module (never guessed) -- two SDKs, neither documents a direct
CDP->x402 shortcut:

- ``cdp-sdk`` (Coinbase's official Python package) exposes
  ``cdp.evm_local_account.EvmLocalAccount``: a SYNCHRONOUS wrapper
  compatible with ``eth_account.signers.base.BaseAccount`` around an
  ``EvmServerAccount`` -- verified by reading its source code
  (github.com/coinbase/cdp-sdk/blob/main/python/cdp/evm_local_account.py):
  its ``sign_typed_data(domain_data=, message_types=, message_data=)``
  method has EXACTLY the same signature expected by the official x402
  SDK's ``EthAccountSigner`` class.
- ``x402`` (official Python package, x402-foundation/x402) provides NO
  ready-made CDP adapter -- only ``x402.mechanisms.evm.signers.EthAccountSigner``,
  designed for any ``eth_account``-compatible object. The ``cdp.x402``
  module (already in cdp-sdk) is an entirely different tool: it's a
  FACILITATOR client (verify/settle a RECEIVED payment), not a client-side
  signer that PAYS -- don't confuse the two.

So: ``EthAccountSigner(EvmLocalAccount(cdp_account))`` is the verified
bridge, built from BOTH official sources, never a guess on a shortcut that
doesn't exist.

**Not tested against a real network call at this stage** (no CDP
credentials in this session, secrets doctrine -- same caveat as
``agent_wallet_cdp_adapter.py``). Before any real activation, the 15/07
process norm (#157): verify at least once the exact shape of the response
against a real call on the VPS."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 22/07 -- imported from agent_wallet_cdp_adapter (SINGLE SOURCE) rather
# than duplicated here -- a duplicated constant is exactly what allowed the
# 21/07 incident (only one of the two copies fixed, the other would have
# kept signing via an empty CDP wallet with nothing flagging it).
#
# 24/07 -- 5-agent audit finding, verified live before fixing: this module
# still called ``cdp.evm.get_or_create_account`` directly (the exact unsafe
# pattern that caused the 21/07 and 23/07 phantom-wallet incidents) instead
# of the fail-closed ``_get_wallet_account`` helper introduced in
# agent_wallet_cdp_adapter.py (commit 0dbbc214) that same day for every OTHER
# call site -- this signer alone was missed. Verified live in the running
# container (``cdp.evm.list_accounts()``): exactly 3 real accounts exist
# today, no phantom wallet has been created by this signer so far -- but the
# risk was live (a future WALLET_NAME/CDP-dashboard rename mismatch would
# have silently started signing x402 payments against a brand-new empty
# wallet, same as before 0dbbc214).
from aria_core.agent_wallet_cdp_adapter import _get_wallet_account


async def build_x402_payment_header(payment_required: dict[str, Any]) -> str:
    """Injectable ``pay_fn`` for ``x402_executor.fetch_paid_resource`` -- signs
    the payment requested by ``payment_required`` (the 402 body's first
    ``accepts[0]``) and returns the ``X-PAYMENT`` header value (base64,
    x402 v1 protocol).

    Raises an exception on any failure (missing import, CDP outage, payment
    construction failure) -- ``x402_executor`` already logs
    ``status="failed"`` on exception, no silent error handling needed here.

    19/07 -- real bug found while testing 2 real v2 providers from the
    Bazaar catalog (lionx402, sociavault, verified live): the official SDK
    (``get_payment_required_response``) REQUIRES the raw header to decode a
    v2 offer -- its "synthetic body" fallback only accepts
    ``x402Version==1`` (read in the installed SDK's source code, not
    guessed) -- systematically failed with "Invalid payment required
    response" on every v2 provider despite a perfectly valid offer, while
    Cybercentry (v1) was never affected. If
    ``x402_executor._extract_payment_requirement`` carried the raw header
    (internal key ``_raw_v2_header``, v2 offer), ``decode_payment_required_header``
    is called directly on it -- the same function the SDK uses internally,
    never reinvented. Otherwise (v1, header absent), HISTORICAL behavior
    unchanged (synthetic body)."""
    from cdp import CdpClient
    from cdp.evm_local_account import EvmLocalAccount
    from x402 import x402Client
    from x402.http.utils import decode_payment_required_header
    from x402.http.x402_http_client import x402HTTPClient
    from x402.mechanisms.evm.exact import register_exact_evm_client
    from x402.mechanisms.evm.signers import EthAccountSigner

    async with CdpClient() as cdp:
        account = await _get_wallet_account(cdp)
        local_account = EvmLocalAccount(account)

    signer = EthAccountSigner(local_account)
    client = x402Client()
    register_exact_evm_client(client, signer)
    http_client = x402HTTPClient(client)

    raw_v2_header = payment_required.get("_raw_v2_header")
    if raw_v2_header:
        parsed = decode_payment_required_header(raw_v2_header)
    else:
        # ``payment_required`` here = a single ``accepts[0]`` (raw dict) --
        # the x402 SDK expects the full envelope to reconstruct the
        # ``PaymentRequired`` type. ``x402Version`` defaults to 1 if absent
        # (v1 protocol -- same default as
        # services/x402.py::payment_required_response).
        body = {
            "x402Version": payment_required.get("x402Version", 1),
            "accepts": [payment_required],
        }
        parsed = http_client.get_payment_required_response(lambda _name: None, body)
    payload = await client.create_payment_payload(parsed)
    headers = http_client.encode_payment_signature_header(payload)
    # 19/07 -- real bug found right after the previous one (same real call,
    # lionx402): the SDK returns the signed value under DIFFERENT KEYS
    # depending on the version -- "PAYMENT-SIGNATURE" for v2, "X-PAYMENT"
    # for v1 (explicitly commented "V1 legacy" in the installed SDK's
    # x402/http/constants.py) -- previously only looked for "X-PAYMENT", so
    # it always failed on v2 despite a successful signature. Returns the
    # VALUE alone (never the header name) -- x402_executor.py already picks
    # the right header name for the paid request based on x402Version.
    header_value = headers.get("PAYMENT-SIGNATURE") or headers.get("X-PAYMENT")
    if not header_value:
        raise RuntimeError(f"encode_payment_signature_header n'a produit ni PAYMENT-SIGNATURE ni X-PAYMENT : {headers!r}")
    return header_value
