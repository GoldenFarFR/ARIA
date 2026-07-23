"""Signal for a "disguised liquidity exit" — insider wallets outside the labeled 'creator'.

`dev_wallet.py` ONLY monitors the explicitly identified deployer wallet
(`creator_address`, via Blockscout) — an insider who received a significant
share of the initial distribution (direct mint or transfer from the
deployer, never routed through a DEX) can sell off their entire allocation
without any existing signal catching it, since they never carry the
"creator" label.

Detected via `services/dune.py::get_insider_recipients` (the raw
`erc20_base.evt_transfer` table, every ERC-20 transfer on Base — unlike
`dex.trades` which only covers DEX trades). Verified under real conditions
(22/07, CNX contract): the deployer receives the initial mint from the zero
address then distributes to several third-party wallets in the following
hours/days — exactly the pattern this module is meant to catch.

Pure, deterministic JUDGE, same doctrine as `dev_wallet.py`: produces a
weighted signal (concern/neutral/unknown) that FEEDS ARIA's reasoning —
never an automatic rejection (only honeypot/owner_change_balance remain
hard vetoes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# A wallet must have received at least 1% of what the top recipient
# received (typically the deployer itself, who holds the initial mint) to
# be considered a "significant allocation" — filters out noise from
# micro-transfers unrelated to a real insider distribution (dust, gas
# refunds...).
_MIN_SHARE_OF_TOP_RECIPIENT = 0.01
# Current holding below this threshold (%, same scale as
# TokenHolder.percentage) = "has almost nothing left" — sold/transferred
# most of their allocation.
_NEAR_ZERO_HOLD_PCT = 0.05
# Maximum number of insider wallets examined (the top of the distribution,
# not the long tail of anecdotal recipients).
_MAX_INSIDERS_EXAMINED = 10
# Default window after the pair's creation — same horizon as the momentum
# pipeline's minimum age gate (14 days): beyond that, a distribution becomes
# normal market activity, not a TGE signal.
_DEFAULT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class InsiderWalletFacts:
    """On-chain facts about the direct recipients of the initial distribution
    (excluding the 'creator' wallet, already covered by dev_wallet.py)."""

    examined: int = 0
    flagged: list[str] = field(default_factory=list)  # addresses that sold off almost everything
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class InsiderWalletVerdict:
    signal: str  # concern / neutral / unknown
    points: list[str] = field(default_factory=list)


def judge_insider_wallets(facts: InsiderWalletFacts) -> InsiderWalletVerdict:
    """Weighted judgment, never a hard cutoff — same doctrine as judge_dev_wallet."""
    if not facts.available:
        return InsiderWalletVerdict(
            signal="unknown", points=[facts.error or "distribution initiale non analysable"],
        )
    if facts.examined == 0:
        return InsiderWalletVerdict(
            signal="neutral", points=["aucune distribution insider significative détectée (hors déployeur)"],
        )
    if not facts.flagged:
        return InsiderWalletVerdict(
            signal="neutral",
            points=[f"{facts.examined} wallet(s) ayant reçu une allocation directe, aucun n'a tout revendu"],
        )
    n = len(facts.flagged)
    return InsiderWalletVerdict(
        signal="concern",
        points=[
            f"{n}/{facts.examined} wallet(s) ayant reçu une allocation directe du déployeur/mint "
            "et ne détenant plus quasiment rien aujourd'hui (sortie possible, jamais visible sur "
            "le wallet 'creator' labellisé seul)"
        ],
    )


async def gather_insider_wallet_facts(
    contract: str,
    creator: str | None,
    *,
    pair_created_at_ms: int | None,
    lp_address: str | None = None,
    holders=None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    dune_module=None,
    client=None,
) -> InsiderWalletFacts:
    """Best-effort collection of facts about insider wallets (defensive, never blocking).

    ``holders``: `TokenHoldersResult` already in hand (reused —
    `scan_base_token` already fetched it for concentration, no extra network
    call). If absent, unavailable, or empty → ``available=False``
    (fail-closed on unknown, NEVER an "everything was sold" inferred from
    missing data). ``dune_module``/``client`` injectable for offline tests
    (default: `services.dune` / `blockscout_client`)."""
    if not creator:
        return InsiderWalletFacts(available=False, error="déployeur inconnu (fenêtre de distribution non bornable)")
    if pair_created_at_ms is None:
        return InsiderWalletFacts(available=False, error="date de création de la paire inconnue")

    if dune_module is None:
        from aria_core.services import dune as dune_module

    try:
        created = datetime.fromtimestamp(pair_created_at_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        return InsiderWalletFacts(available=False, error=f"horodatage de paire invalide ({exc})")

    window_start = (created - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (created + timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

    result = await dune_module.get_insider_recipients(
        contract, creator, window_start=window_start, window_end=window_end,
        limit=_MAX_INSIDERS_EXAMINED + 5,
    )
    if not result.available:
        return InsiderWalletFacts(available=False, error=result.error)

    recipients = result.recipients
    if not recipients:
        return InsiderWalletFacts(examined=0, available=True)

    top_amount = recipients[0].total_received_raw
    if not top_amount or top_amount <= 0:
        return InsiderWalletFacts(examined=0, available=True)

    if holders is None:
        if client is None:
            from aria_core.services.blockscout import blockscout_client as client
        holders = await client.get_token_holders(contract)

    if holders is None or not getattr(holders, "available", False):
        return InsiderWalletFacts(
            available=False,
            error="holders actuels indisponibles : impossible de vérifier si la distribution initiale a été revendue",
        )

    current_pct: dict[str, float] = {}
    for h in holders.holders:
        addr = (h.address or "").lower()
        if addr:
            current_pct[addr] = float(h.percentage) if h.percentage is not None else 0.0

    dev = creator.lower()
    lp = (lp_address or "").lower()

    examined = 0
    flagged: list[str] = []
    for r in recipients[:_MAX_INSIDERS_EXAMINED]:
        addr = r.address.lower()
        if addr in (dev, lp, _ZERO_ADDRESS):
            continue  # already covered by dev_wallet.py, or LP pool / zero address — not a third-party insider
        if r.total_received_raw < top_amount * _MIN_SHARE_OF_TOP_RECIPIENT:
            continue  # allocation too small to be significant
        examined += 1
        if current_pct.get(addr, 0.0) < _NEAR_ZERO_HOLD_PCT:
            flagged.append(r.address)

    return InsiderWalletFacts(examined=examined, flagged=flagged, available=True)
