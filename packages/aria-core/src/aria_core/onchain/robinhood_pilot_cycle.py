"""Robinhood Chain TESTNET rehearsal cycle (24/08) — the first
production/heartbeat caller for the homemade agent-wallet's Robinhood leg.

Distinct from the "prove it once" milestone already recorded live
(``docs/HANDOFF_AGENT_WALLET.md``, 23/08 entry): that proved the mechanics
work. This cycle proves they KEEP working under continuous, unattended
operation — the actual substance of CLAUDE.md's third named prerequisite for
a real Robinhood pilot ("wallet_guard/kill-switch wiring"). Rehearsing that
wiring repeatedly on worthless testnet funds, on a live heartbeat cadence, is
the point — never a shortcut past it.

Deliberately NOT a trading/sourcing cycle: the testnet stub token
(``docs/HANDOFF_AGENT_WALLET.md``, 23/08) has no real market, so there is no
momentum/candidate decision to rehearse. Each tick attempts one small,
fixed-amount transfer of the already-configured periodic allowance BACK TO
THE DELEGATE'S OWN ADDRESS — a closed loop that never accumulates value
anywhere and never needs a second destination address — purely to keep
exercising gate -> kill-switch -> on-chain cap -> signing -> execution ->
logging on a live cadence, exactly the same order
``homemade_agent_wallet.attempt_transfer`` already enforces (this module adds
no guardrail of its own, only wiring).

Safe/module/token addresses below are the SAME ones proven live 23/08 (see
HANDOFF) — real, deployed, but hold zero value (testnet). The allowance was
reconfigured 24/08 from its original one-shot cap (spent 100/100, exhausted)
to a PERIODIC one (20 units per 60 minutes) specifically so this cycle can
run repeatedly without needing a human to reset it — see
``docs/HANDOFF_AGENT_WALLET.md``'s 24/08 entry.

Reuses ``safe_robinhood_deploy.deployer_account()`` for the delegate key
(this dome's existing ``ARIA_ROBINHOOD_DEPLOYER_PRIVATE_KEY`` — same
single-key rehearsal simplification already documented in
``safe_robinhood_deploy.py``, owner and delegate are the same key for this
first cycle) rather than writing a second copy of the same key material to a
JSON file on disk.
"""
from __future__ import annotations

import logging
import os

from aria_core import homemade_agent_wallet

logger = logging.getLogger(__name__)

CHAIN = "robinhood_testnet"

# Real addresses proven live 23/08 (docs/HANDOFF_AGENT_WALLET.md) — testnet
# only, zero value held. Not secrets: a Safe address, a module address, and a
# stub ERC-20 test-token address are all public on-chain data.
SAFE_ADDRESS = "0x9Cb5A6B26E2e8F1b69AAb6555C72122FBEEdb1BE"
TOKEN_ADDRESS = "0xde4fFDd94BFEe476F10E079D4cd66918c4131d0c"

# Fixed, deliberately small rehearsal amount — well under the 20-unit/60min
# periodic cap (docs/HANDOFF_AGENT_WALLET.md, 24/08 entry), leaving headroom
# for the burn-in cadence to tick a few times per reset window without ever
# hitting the on-chain cap itself (that on-chain rejection path is already
# proven separately, 23/08 — this cycle rehearses the SUCCESS path).
REHEARSAL_AMOUNT = 1


def robinhood_testnet_rehearsal_enabled() -> bool:
    """Dedicated gate, OFF by default — fail-closed until explicitly set.
    Distinct from ``ARIA_HOMEMADE_AGENT_WALLET_ENABLED`` (the shared
    guardrail wrapper's own gate, which this cycle also goes through) so this
    specific rehearsal cadence can be toggled independently of any other
    caller of that wrapper."""
    return os.environ.get("ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def run_robinhood_testnet_rehearsal_cycle() -> dict:
    """One rehearsal tick. Never raises (soft degradation, same doctrine as
    the rest of the heartbeat) — any failure translates into an explicit
    ``outcome`` via ``homemade_agent_wallet.attempt_transfer``'s own
    ``TransferAttemptResult``, never a silent crash of the heartbeat tick."""
    if not robinhood_testnet_rehearsal_enabled():
        return {"outcome": "disabled"}

    from aria_core.onchain import safe_robinhood_deploy as deploy
    from aria_core.onchain import safe_robinhood_signer as signer
    from aria_core.onchain import safe_robinhood_wallet as wallet

    try:
        account = deploy.deployer_account()
    except RuntimeError as exc:
        logger.warning("robinhood_pilot_cycle: no deployer key configured (%s)", exc)
        return {"outcome": "no_key"}

    delegate_address = account.address

    async def remaining_fn() -> int | None:
        live = wallet.read_allowance(SAFE_ADDRESS, delegate_address, TOKEN_ADDRESS)
        if live.get("error"):
            return None
        return live.get("remaining")

    async def send_fn(*, amount: int, **_kw) -> dict:
        return await signer.send_allowance_transfer(
            safe=SAFE_ADDRESS, token=TOKEN_ADDRESS, to=delegate_address,
            amount=amount, account=account,
        )

    result = await homemade_agent_wallet.attempt_transfer(
        chain=CHAIN,
        amount=REHEARSAL_AMOUNT,
        remaining_fn=remaining_fn,
        send_fn=send_fn,
        wallet_product=homemade_agent_wallet.WALLET_PRODUCT,
    )
    return {"outcome": result.status, "reason": result.reason, "tx_hash": result.tx_hash}
