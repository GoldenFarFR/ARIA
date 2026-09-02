"""Single resolution point for Base's HTTPS RPC endpoint (02/09).

Why this exists: five modules -- ``early_legitimacy_shadow``, ``services/b20``,
``services/doppler``, ``services/base_onchain``, ``services/basenames`` -- each
carried the SAME line, copied five times:

    (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or _DEFAULT_RPC_URL

That is the exact defect constitution §1bis names ("a constant redefined
locally when it already exists elsewhere is a defect, even if the code
works"), and it turned into a real production incident the day the operator
retired the Alchemy key that variable pointed at.

**The trap, worth stating because it is not obvious**: `or _DEFAULT_RPC_URL`
only falls back when the variable is EMPTY. A variable that is *set but dead*
sails straight past it. So a destroyed key is strictly worse than a missing
one -- every caller kept dialling an endpoint answering
``{"code": -32600, "message": "Must be authenticated!"}``, and each module's
own fail-closed posture converted that infrastructure failure into a verdict.
Measured impact before this fix: ``b20.is_b20()`` returned None, so
``evaluate_b20_safety`` returned "opaque", so 43 of 250 live candidates (17%)
were rejected as ``b20_unresolved_risk`` -- for a reason that had nothing to
do with those tokens.

Order of preference, and why:
  1. **Chainstack, derived from the WSS variable** (this repo's standing
     wss->https rule). It is the paid, working provider, and its Base budget
     was measured 76% idle on 02/09.
  2. ``ARIA_BASE_RPC_URL`` -- kept so setting a fresh key restores the old
     behaviour with no code change.
  3. The public endpoint -- last resort, rate-limited, never a silent default
     anyone should rely on.

``base_rpc_provider()`` returns which one was actually used. Callers that
persist a verdict should record it: two providers have already served this
chain at once, and a stored result that does not say which is indistinguishable
later from a real change in on-chain behaviour.

Never returns or logs the credentialed URL -- only the provider name is safe
to surface (two real secret leaks through Bash in July made that rule absolute).
"""
from __future__ import annotations

import os

# Public Base endpoint -- the historical `_DEFAULT_RPC_URL` every one of the
# five modules restated. Defined ONCE here; importing it is the correct
# reflex, restating it is the defect described above.
PUBLIC_BASE_RPC_URL = "https://mainnet.base.org"

PROVIDER_CHAINSTACK = "chainstack"
PROVIDER_ENV = "aria_base_rpc_url"
PROVIDER_PUBLIC = "public"


def _chainstack_https() -> str:
    """Chainstack's HTTPS endpoint, derived from the WSS one (the repo's
    standing rule: same host, same credential, different scheme -- never a
    second variable to keep in sync)."""
    ws = (os.environ.get("ARIA_BASE_RPC_WS", "") or "").strip()
    if not ws:
        return ""
    if ws.startswith("wss://"):
        return "https://" + ws[len("wss://"):]
    if ws.startswith("ws://"):
        return "http://" + ws[len("ws://"):]
    return ""


def base_rpc_url() -> str:
    """The HTTPS endpoint to dial for Base. Never empty."""
    return _chainstack_https() or (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip() or PUBLIC_BASE_RPC_URL


def base_rpc_provider() -> str:
    """Which source ``base_rpc_url()`` just resolved to -- for recording
    alongside any persisted verdict, never inferred from the chain name."""
    if _chainstack_https():
        return PROVIDER_CHAINSTACK
    if (os.environ.get("ARIA_BASE_RPC_URL", "") or "").strip():
        return PROVIDER_ENV
    return PROVIDER_PUBLIC
