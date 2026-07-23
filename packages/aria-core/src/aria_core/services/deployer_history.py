"""Deployer reputation — has this deployer already shipped a contract that rugged?

`dev_wallet.py`/`insider_wallets.py` judge the deployer's behavior ON this
specific token. This module looks at their HISTORY: other contracts deployed
by the SAME address, and whether any of them is already confirmed a scam by
ARIA herself (`momentum_blacklist.py` — free, zero network call, first-hand
data, never an unverified third-party source).

Honest limit, documented rather than hidden: enumerating the contracts
created by an address has no cheap dedicated endpoint on Blockscout (verified
22/07: no `type=contract_creation` parameter, no "creation first" sort). The
search therefore goes through the transaction history already explored
elsewhere (`get_transactions_bounded`, same bounded-pagination doctrine as the
rest of the project) and covers ONLY the deployer's most RECENT transactions
— never guaranteed exhaustive. `truncated=True` explicitly flags it if the
page cap is reached without exhausting the history.

Purely CONSULTATIVE signal (same doctrine as dev_wallet.py/insider_wallets.py)
— never a hard veto, even on a confirmed repeat offender. Important finding
made while verifying this module on a real case (CNX, 22/07): a 'creator'
address can be a DELEGATED ACCOUNT (EIP-7702 -- confirmed via a direct
Blockscout call, field ``proxy_type: "eip7702"``), so it isn't always a
stable identity from one deployment to the next. One more reason to never
auto-reject on this signal alone."""
from __future__ import annotations

from dataclasses import dataclass, field

# Maximum deployer transaction pages explored (bounded, never
# exhaustive — see doctrine above). Same order of magnitude as the project's
# other bounded scans (e.g. get_first_funded_by on the Dune side).
_MAX_PAGES = 3


@dataclass(frozen=True)
class DeployerHistoryFacts:
    prior_contracts_found: int = 0
    known_rugs: list[str] = field(default_factory=list)  # addresses already blacklisted
    truncated: bool = False
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DeployerHistoryVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_deployer_history(facts: DeployerHistoryFacts) -> DeployerHistoryVerdict:
    """Pure, deterministic judgment, same doctrine as judge_dev_wallet/judge_insider_wallets."""
    if not facts.available:
        return DeployerHistoryVerdict(
            signal="unknown", points=[facts.error or "historique du déployeur non analysable"],
        )
    if not facts.known_rugs:
        note = (
            f"{facts.prior_contracts_found} contrat(s) antérieur(s) du déployeur trouvé(s), "
            "aucun déjà confirmé scam par ARIA"
        )
        if facts.truncated:
            note += " (historique borné aux transactions récentes, pas garanti exhaustif)"
        return DeployerHistoryVerdict(signal="neutral", points=[note])
    n = len(facts.known_rugs)
    return DeployerHistoryVerdict(
        signal="concern",
        points=[
            f"le déployeur a créé {n} contrat(s) déjà confirmé(s) scam par ARIA elle-même "
            "(récidiviste -- signal fort, jamais un rejet automatique à lui seul, l'adresse "
            "'creator' peut aussi être un compte délégué non stable d'un déploiement à l'autre)"
        ],
    )


async def gather_deployer_history_facts(
    creator: str | None,
    *,
    chain: str = "base",
    exclude_contract: str | None = None,
    max_pages: int = _MAX_PAGES,
    client=None,
    blacklist_module=None,
) -> DeployerHistoryFacts:
    """Best-effort collection: contracts already created by this deployer
    (bounded, most recent first) then cross-referenced against ARIA's own
    blacklist. Defensive, never blocking. ``client``/``blacklist_module``
    injectable for offline tests (default: blockscout_client / momentum_blacklist)."""
    if not creator:
        return DeployerHistoryFacts(available=False, error="déployeur inconnu")
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client
    if blacklist_module is None:
        from aria_core import momentum_blacklist as blacklist_module

    try:
        result = await client.get_transactions_bounded(creator, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — bonus history, never blocking
        return DeployerHistoryFacts(available=False, error=f"historique indisponible ({exc})")
    if not result.available:
        return DeployerHistoryFacts(available=False, error=result.error)

    excl = (exclude_contract or "").lower()
    seen: set[str] = set()
    prior_contracts: list[str] = []
    for tx in result.transactions:
        addr = (getattr(tx, "created_contract", None) or "").lower()
        if not addr or addr == excl or addr in seen:
            continue
        seen.add(addr)
        prior_contracts.append(addr)

    known_rugs: list[str] = []
    for addr in prior_contracts:
        try:
            if await blacklist_module.is_blacklisted(addr, chain):
                known_rugs.append(addr)
        except Exception:  # noqa: BLE001 — an unreadable entry doesn't invalidate the others
            continue

    return DeployerHistoryFacts(
        prior_contracts_found=len(prior_contracts),
        known_rugs=known_rugs,
        truncated=result.truncated,
        available=True,
    )
