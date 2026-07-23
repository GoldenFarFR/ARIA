"""Contract authority classification — a mint is only dangerous if a DEV controls it.

A token can expose an external ``mint`` function without being a trap: it all
depends on WHO can call it.
  - **renounced**: owner = dead address (0x0 / 0x…dead) -> nobody can mint -> safe;
  - **launchpad**: the token was deployed by a known launchpad (Virtuals, Flaunch,
    Clanker, Zora...) -> authority belongs to the PROTOCOL, never renounced but
    legitimate (that's how the launchpad normally operates);
  - **contract**: owner = a contract (timelock / multisig / issuance contract) ->
    power locked/shared, not a single dev wallet -> acceptable;
  - **eoa**: owner = an external wallet (a person) -> the dev can dilute at will
    -> DANGER;
  - **unknown**: impossible to determine -> stay cautious (fail-closed downstream).

This module is PURE (no network): it classifies from facts already collected
(deployer, owner, owner-is-a-contract) and the ``knowledge/launchpads.yaml``
registry. Deterministic: same facts -> same verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_LAUNCHPADS_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "launchpads.yaml"

# "Dead" addresses = effective renouncement (nobody holds the key).
_DEAD_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",
}


def _is_dead_address(addr: str) -> bool:
    """Recognizes a dead address (renouncement): known list OR zeros+dead pattern."""
    a = (addr or "").strip().lower()
    if a in _DEAD_ADDRESSES:
        return True
    body = a[2:] if a.startswith("0x") else a
    if len(body) != 40:
        return False
    if body.endswith("dead") and set(body[:-4]) <= {"0"}:
        return True
    if body.startswith("dead") and set(body[4:]) <= {"0"}:
        return True
    return False

# Authority verdicts where the mint is considered NEUTRALIZED (not controlled by a dev).
# Public (no _ prefix): single source imported by safety_screen.py (hard gate)
# AND acp_onchain_scan.py (score penalty) — fixes #164, a timelocked/launchpad/
# renounced mint must never be penalized twice with two lists that could diverge.
SAFE_AUTHORITIES = frozenset({"renounced", "launchpad", "contract"})


@dataclass(frozen=True)
class AuthorityVerdict:
    """Who controls the contract (and therefore the mint), with its factual basis."""

    kind: str  # renounced / launchpad / contract / eoa / unknown / na
    launchpad: str | None = None  # launchpad label if recognized
    owner: str | None = None
    detail: str = ""

    @property
    def mint_neutralized(self) -> bool:
        """True if a possible mint is NOT in a dev's hands (therefore acceptable).

        ``na`` (no external mint) is trivially neutralized: nothing to control.
        """
        return self.kind == "na" or self.kind in SAFE_AUTHORITIES


@lru_cache(maxsize=1)
def _launchpad_index() -> dict[str, str]:
    """Table of deployer_address (lowercase) -> launchpad label."""
    idx: dict[str, str] = {}
    if not _LAUNCHPADS_PATH.is_file():
        return idx
    try:
        cfg: dict[str, Any] = yaml.safe_load(_LAUNCHPADS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return idx
    for _key, block in (cfg.get("launchpads") or {}).items():
        label = str((block or {}).get("label") or _key)
        for addr in (block or {}).get("addresses", []) or []:
            a = str(addr).strip().lower()
            if a.startswith("0x") and len(a) == 42:
                idx[a] = label
    return idx


def match_launchpad(creator_address: str | None) -> str | None:
    """Launchpad label if the deployer is known, otherwise None."""
    if not creator_address:
        return None
    return _launchpad_index().get(str(creator_address).strip().lower())


@lru_cache(maxsize=1)
def _norms_by_label() -> dict[str, dict]:
    """Table of launchpad label -> its tokenomics norms (bonding, alloc, liq...)."""
    out: dict[str, dict] = {}
    if not _LAUNCHPADS_PATH.is_file():
        return out
    try:
        cfg: dict[str, Any] = yaml.safe_load(_LAUNCHPADS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    for _key, block in (cfg.get("launchpads") or {}).items():
        label = str((block or {}).get("label") or _key)
        out[label] = dict((block or {}).get("norms") or {})
    return out


def launchpad_norms(label: str | None) -> dict:
    """A launchpad's tokenomics norms (empty dict if unknown)."""
    if not label:
        return {}
    return _norms_by_label().get(label, {})


def is_bonding_launchpad(label: str | None) -> bool:
    """True if the launchpad operates via a bonding curve (exponential liquidity)."""
    return bool(launchpad_norms(label).get("bonding_curve"))


def classify_authority(
    *,
    has_mint: bool | None,
    creator_address: str | None = None,
    owner_address: str | None = None,
    owner_is_contract: bool | None = None,
) -> AuthorityVerdict:
    """Classifies the contract's authority from already-collected on-chain facts.

    Priority order:
      1. no external mint -> ``na`` (nothing to neutralize);
      2. deployed by a known launchpad -> ``launchpad`` (the protocol holds authority);
      3. owner = dead address -> ``renounced``;
      4. owner = a contract -> ``contract`` (locked/multisig/issuance);
      5. owner = a wallet (EOA) -> ``eoa`` (dev can dilute -> danger);
      6. undeterminable -> ``unknown``.
    """
    if not has_mint:
        return AuthorityVerdict(kind="na", detail="pas de fonction mint externe")

    launchpad = match_launchpad(creator_address)
    if launchpad:
        return AuthorityVerdict(
            kind="launchpad",
            launchpad=launchpad,
            detail=f"déployé par {launchpad} (autorité du protocole, mint légitime)",
        )

    owner = (owner_address or "").strip().lower() or None
    if owner and _is_dead_address(owner):
        return AuthorityVerdict(
            kind="renounced", owner=owner, detail="propriété renoncée (owner = adresse morte)"
        )
    if owner and owner_is_contract is True:
        return AuthorityVerdict(
            kind="contract",
            owner=owner,
            detail="owner = contrat (timelock/multisig/émission, pas un wallet de dev)",
        )
    if owner and owner_is_contract is False:
        return AuthorityVerdict(
            kind="eoa",
            owner=owner,
            detail="owner = wallet externe (le dev peut créer des tokens)",
        )
    return AuthorityVerdict(kind="unknown", owner=owner, detail="autorité du mint indéterminable")
