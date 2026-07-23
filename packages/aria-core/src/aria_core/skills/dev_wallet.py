"""Dev/team wallet behavior — committed builder or farmer?

A token's deployer leaves an on-chain trail that says a lot about its LEGITIMACY.
Four dimensions (operator intuition), judged CASE BY CASE, never in binary:

  1. **Does he hold?** (`holds_pct`) — skin-in-the-game vs sell pressure. Relative
     healthy zone: on Virtuals a ~15-20% team allocation is normal; a solo founder
     with no money at 0% isn't necessarily bad faith; but >40% out of norm =
     dump risk.
  2. **Did he buy with his own money** (`acquired='bought'`) or just **self-allocate**
     (`'allocation'`)? Buying = aligned (he committed capital).
  3. **Did he sell** (`sold_pct_of_received`) — to **fund** the project (small
     tranches, healthy) or to **extract** (big early dump, concern)?
  4. **All-in?** (buy + hold + little/no selling) = strong conviction.

The JUDGE is PURE and deterministic; it produces factual OBSERVATIONS + a
weighted signal (aligned / neutral / concern / unknown) that FEEDS ARIA's
reasoning — it doesn't reject outright. The logic adapts to the project's size: an
organized team committing nothing is abnormal; a solo dev is excused.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# RELATIVE landmarks (not hard cutoffs): above these, scrutiny is warranted.
_HIGH_HOLD_PCT = 40.0        # very high holding, out of launchpad norm -> dump risk
_HEAVY_SELL_PCT = 50.0       # sold the majority of what he received -> likely extraction


@dataclass(frozen=True)
class DevWalletFacts:
    """On-chain facts about the deployer's wallet (collected, never invented)."""

    creator: str | None
    holds_pct: float | None = None            # % of supply held (excl. LP/burn)
    acquired: str | None = None               # 'allocation' | 'bought' | 'mixed' | None
    sold_events: int = 0
    sold_pct_of_received: float | None = None  # share of what he received that he resold
    available: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DevWalletVerdict:
    """Weighted judgment of the dev's behavior, with its factual observations."""

    signal: str  # aligned / neutral / concern / unknown
    points: list[str] = field(default_factory=list)


def judge_dev_wallet(
    facts: DevWalletFacts,
    *,
    launchpad_team_norm: tuple[float, float] | None = None,
    team_is_large: bool | None = None,
) -> DevWalletVerdict:
    """Judges the dev's behavior case by case. Returns signal + observations.

    ``launchpad_team_norm``: the launchpad's normal team allocation range (e.g.
    (15, 20) for Virtuals) — a holding WITHIN this range isn't a concern.
    ``team_is_large``: team-size indicator (if known) — a large team that
    commits nothing is more suspicious than a solo dev.
    """
    if not facts.available:
        return DevWalletVerdict(signal="unknown", points=[facts.error or "wallet du dev non analysable"])

    points: list[str] = []
    concern = 0
    aligned = 0

    hp = facts.holds_pct
    within_norm = (
        hp is not None and launchpad_team_norm is not None
        and launchpad_team_norm[0] <= hp <= launchpad_team_norm[1] * 1.25
    )

    if hp is None:
        points.append("détention du dev inconnue")
    elif hp == 0:
        points.append("le dev ne détient rien : peu de pression de vente, mais conviction à confirmer")
        if team_is_large:
            points.append("équipe apparemment organisée mais sans skin-in-the-game : incohérent")
            concern += 1
    elif within_norm:
        points.append(f"détient {hp:.1f}% (dans la norme du launchpad, aligné)")
        aligned += 1
    elif hp >= _HIGH_HOLD_PCT:
        points.append(f"détient {hp:.1f}% (très concentré : risque de dump)")
        concern += 1
    else:
        points.append(f"détient {hp:.1f}% (skin-in-the-game)")
        aligned += 1

    if facts.acquired == "bought":
        points.append("a ACHETÉ ses tokens avec son capital (aligné)")
        aligned += 1
    elif facts.acquired == "allocation":
        points.append("détention par auto-allocation (aucun capital engagé)")
    elif facts.acquired == "mixed":
        points.append("mélange d'allocation et d'achats")
        aligned += 1

    if facts.sold_pct_of_received is not None:
        sp = facts.sold_pct_of_received
        if sp >= _HEAVY_SELL_PCT:
            points.append(f"a revendu {sp:.0f}% de sa dotation (extraction probable)")
            concern += 2
        elif facts.sold_events >= 3 and sp < _HEAVY_SELL_PCT:
            points.append(f"ventes échelonnées ({facts.sold_events}x, {sp:.0f}%) : possible financement du dev")
        elif facts.sold_events == 1 and sp >= 25:
            points.append(f"un seul gros dégagement ({sp:.0f}%) : à surveiller")
            concern += 1
        elif sp == 0:
            points.append("n'a rien vendu (conviction)")
            aligned += 1

    # Weighted signal (never a hard cutoff — feeds ARIA's judgment).
    if concern >= 2 and concern > aligned:
        signal = "concern"
    elif aligned >= 2 and concern == 0:
        signal = "aligned"
    elif concern > 0 and concern >= aligned:
        signal = "concern"
    else:
        signal = "neutral"
    return DevWalletVerdict(signal=signal, points=points)


_ZERO = "0x0000000000000000000000000000000000000000"

# 23/07 -- live calibration (task #26): CNX contract (already scanned in prod)
# verified as a Uniswap V4 pool (dexId "uniswap" with no further detail on
# DexScreener's side). On V4, ALL swaps from ALL pools go through this
# SINGLETON PoolManager, never through a dedicated pool address like on V2/V3
# -- comparing `frm`/`to` against just `lp_address` (the logical pool identifier
# returned by DexScreener) therefore NEVER captures a real buy/sell on a
# V4 pool, whatever the token. Official address verified (BaseScan, "Uniswap
# V4: Pool Manager") -- Base only, consistent with the rest of this module.
_UNISWAP_V4_POOL_MANAGER_BASE = "0x498581ff718922c3f8e6a244956af099b2652b2b"


async def gather_dev_wallet_facts(
    contract: str,
    creator: str | None,
    *,
    lp_address: str | None = None,
    client=None,
) -> DevWalletFacts:
    """Best-effort collection of on-chain facts about the deployer (defensive, never blocking).

    - **Holding**: the deployer's share in the token's holder list.
    - **Buys / allocation / sells**: classifies token transfers involving the
      deployer — received from zero/contract = allocation; received from the LP pool
      (or the Uniswap V4 PoolManager, cf. `_UNISWAP_V4_POOL_MANAGER_BASE`) = bought;
      sent to either of the two = sold.

    Injectable ``client`` (default: blockscout_client) for offline tests. Any
    unavailability -> ``available=False`` (the judge will return 'unknown'). The classification
    remains a heuristic — calibrated live on 23/07 against a real case (CNX, V4 pool)."""
    if not creator:
        return DevWalletFacts(creator=None, available=False, error="déployeur inconnu")
    if client is None:
        from aria_core.services.blockscout import blockscout_client as client

    dev = creator.lower()
    lp_candidates = {_UNISWAP_V4_POOL_MANAGER_BASE}
    if lp_address:
        lp_candidates.add(lp_address.lower())

    holds_pct: float | None = None
    try:
        holders = await client.get_token_holders(contract)
        if holders.available:
            for h in holders.holders:
                if (h.address or "").lower() == dev:
                    holds_pct = float(h.percentage) if h.percentage is not None else None
                    break
            else:
                holds_pct = 0.0  # absent des holders = ne détient pas
    except Exception as exc:  # noqa: BLE001
        return DevWalletFacts(creator=dev, available=False, error=f"holders indisponibles ({exc})")

    received = 0.0
    bought = 0.0
    sold = 0.0
    sold_events = 0
    try:
        transfers = await client.get_token_transfers(dev, limit=100)
        items = getattr(transfers, "transfers", None) or []
        for t in items:
            if (getattr(t, "token_address", "") or "").lower() != contract.lower():
                continue
            amt = getattr(t, "amount", None) or 0.0
            frm = (getattr(t, "from_address", "") or "").lower()
            to = (getattr(t, "to_address", "") or "").lower()
            if to == dev:  # le dev reçoit
                received += amt
                if frm in lp_candidates:
                    bought += amt
            elif frm == dev:  # le dev envoie
                if to in lp_candidates:
                    sold += amt
                    sold_events += 1
    except Exception:  # noqa: BLE001 — les transferts sont un bonus, pas bloquant
        pass

    if received > 0:
        if bought > 0 and bought < received:
            acquired = "mixed"
        elif bought > 0:
            acquired = "bought"
        else:
            acquired = "allocation"
    else:
        acquired = None

    sold_pct = round(100.0 * sold / received, 1) if received > 0 else None

    return DevWalletFacts(
        creator=dev,
        holds_pct=holds_pct,
        acquired=acquired,
        sold_events=sold_events,
        sold_pct_of_received=sold_pct,
        available=True,
    )
