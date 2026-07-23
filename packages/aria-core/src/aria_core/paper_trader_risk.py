"""Paper-portfolio risk management (#187) -- continuous monitoring of OPEN
positions + concentration cap per category.

Module kept separate from ``paper_trader.py`` (instead of added inline) to
limit the collision surface with the parallel work on that file (#186) --
``paper_trader.py`` only gains 2 additive DB columns (``category``,
``entry_security_json``) and 2 optional kwargs on ``open_position``,
everything else lives here.

Two mechanisms, both called from ``paper_trader.run_paper_cycle`` (no new
heartbeat cadence -- reuses the existing ``paper_trade_cycle`` cycle,
180 min):

1. CONTINUOUS MONITORING -- ``rescan_open_position`` compares a position's
   CURRENT security state (GoPlus honeypot/taxes + Blockscout
   verification/ownership) against the snapshot captured AT ENTRY
   (``capture_entry_snapshot``, reuses fields already computed by
   ``scan_base_token`` -- no duplicated GoPlus/Blockscout call at entry, only
   ``read_owner`` is a new call since ``TokenScanContext`` has no owner
   address). NEVER closes the position itself: returns a diagnostic, it's
   the caller (``paper_trader.py``, sole holder of ``close_position``) that
   decides.

   WARNING -- REAL CAPITAL DOCTRINE (wallet_guard.py): in paper-trading,
   auto-closing on a hard signal is risk-free -- it tests the REACTION. With
   REAL capital, this mechanism should NEVER trigger an automatic sale: only
   a Telegram ALERT with mandatory operator confirmation, exactly like
   ``wallet_guard`` already enforces for every ACP spend (``escalate_spend``
   only alerts, only a real Telegram click triggers ``resolve_spend``). If
   this module is ever wired to a real portfolio, ``run_paper_cycle``'s
   automatic close must be replaced by the same escalation pattern.

2. CONCENTRATION CAP -- never more than ``CONCENTRATION_CAP_PCT`` of the
   pocket's capital (``STARTING_CAPITAL_USD``, the proof's fixed envelope,
   not the currently deployed subset -- a half-empty till shouldn't read as
   "diversified" just because it only has 2 positions of the same type)
   concentrated in a single category open at the same time. A new entry that
   would exceed the cap is REDUCED in size to fit exactly under it; if the
   remaining room is too small for a meaningful position (< 20% of the
   normal allocation), the position is SKIPPED rather than opened as a dust
   position.

   Category = ``launchpad`` (already resolved by ``scan_base_token`` -- a
   finer-grained field than ``network``, which doesn't exist on
   ``TokenScanContext``/doesn't vary in this Base-only portfolio) suffixed
   with ``-bonding`` if ``bonding_phase`` -- e.g. ``virtuals_bonding``,
   ``virtuals_bonding-bonding``, ``clanker``, ``unknown``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields

logger = logging.getLogger(__name__)

# ── Concentration cap ─────────────────────────────────────────────────────────────────

CONCENTRATION_CAP_PCT = 0.40
# Below this threshold (fraction of a position's NORMAL allocation), skip rather than
# open a dust position that clutters the portfolio for a negligible amount.
MIN_CONCENTRATION_ALLOC_FRACTION = 0.2


def derive_category(launchpad: str | None, *, bonding_phase: bool = False) -> str:
    base = (launchpad or "unknown").strip() or "unknown"
    return f"{base}-bonding" if bonding_phase else base


def category_exposure_usd(category: str, open_positions: list[dict]) -> float:
    if not category:
        return 0.0
    return sum(
        float(p.get("cost_usd") or 0.0)
        for p in open_positions
        if (p.get("category") or "") == category
    )


def fit_alloc_to_concentration_cap(
    *,
    category: str,
    alloc: float,
    already_deployed_usd: float,
    starting_capital: float,
    min_alloc: float,
) -> float:
    """Returns the adjusted allocation to respect ``CONCENTRATION_CAP_PCT`` of
    ``starting_capital`` on ``category``, or 0.0 if the remaining room is too
    small (< ``min_alloc``) to be worth opening a position."""
    if not category or starting_capital <= 0 or alloc <= 0:
        return alloc
    cap_usd = CONCENTRATION_CAP_PCT * starting_capital
    room = cap_usd - already_deployed_usd
    if room <= 0:
        return 0.0
    fitted = min(alloc, room)
    return fitted if fitted >= min_alloc else 0.0


# ── Entry security snapshot + continuous rescan ───────────────────────────────────────

_RENOUNCED_OWNER_MARKERS = (
    "0x" + "0" * 40,
    "0x000000000000000000000000000000000000dead",
)


@dataclass
class EntrySecuritySnapshot:
    """Security state at the moment a position OPENS -- the reference against
    which ``rescan_open_position`` detects a NEW signal (that appeared after
    entry), never an absolute state (a token can legitimately have high taxes
    from the start -- it's the CHANGE after opening that's the hard signal)."""

    is_honeypot: bool | None = None
    cannot_sell: bool | None = None
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    contract_verified: bool | None = None
    owner_address: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | None) -> "EntrySecuritySnapshot | None":
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


async def capture_entry_snapshot(contract: str, ctx) -> EntrySecuritySnapshot:
    """Reuses fields already computed by ``scan_base_token`` on ``ctx`` (no
    duplicated GoPlus/Blockscout call); only ``read_owner`` is a new network
    call, since ``TokenScanContext`` has no owner address."""
    from aria_core.services.blockscout import blockscout_client

    owner, _err = await blockscout_client.read_owner(contract)
    return EntrySecuritySnapshot(
        is_honeypot=getattr(ctx, "is_honeypot", None),
        cannot_sell=getattr(ctx, "cannot_sell", None),
        hidden_owner=getattr(ctx, "hidden_owner", None),
        can_take_back_ownership=getattr(ctx, "can_take_back_ownership", None),
        contract_verified=getattr(ctx, "contract_verified", None),
        owner_address=owner,
    )


async def rescan_open_position(position: dict, *, pair=None) -> dict | None:
    """Re-checks an OPEN position against its entry snapshot. Returns
    ``{"contract": ..., "reasons": [...]}`` if a NEW hard signal is detected,
    otherwise ``None``. Positions opened before this mechanism existed (no
    ``entry_security_json``): no reference to compare against -- we don't
    reinvent a baseline, we silently skip (honest degradation, never a
    fabricated signal).

    ``pair`` (17/07, blind spot found that same evening): DexScreener
    ``PairSnapshot`` already fetched by the caller (``paper_trader.py``,
    which fetches it anyway to know the current price -- never a duplicated
    second network call). ``None`` by default -- the volume/liquidity ratio
    check is then simply SKIPPED (same honest-degradation doctrine as the
    rest of this function), never an autonomous network call triggered from
    here. Without this check, a token could enter cleanly (healthy ratio at
    opening, see ``momentum_entry.py``) then drift into a manipulated pool
    DURING the holding period without ever being re-checked -- the trailing
    stop would then confidently follow a wash-trading price."""
    snapshot = EntrySecuritySnapshot.from_json(position.get("entry_security_json"))

    contract = position["contract"]
    reasons: list[str] = []

    if pair is not None and pair.liquidity_usd and pair.liquidity_usd > 0:
        from aria_core.momentum_entry import MAX_VOLUME_TO_LIQUIDITY_RATIO

        volume_to_liq = (pair.volume_24h_usd or 0.0) / pair.liquidity_usd
        if volume_to_liq > MAX_VOLUME_TO_LIQUIDITY_RATIO:
            reasons.append(
                f"ratio volume 24h/liquidité extrême détecté en cours de détention "
                f"({volume_to_liq:.0f}x > {MAX_VOLUME_TO_LIQUIDITY_RATIO:.0f}x) -- "
                f"signal de wash-trading, absent ou non détecté à l'entrée"
            )

    if snapshot is None:
        return {"contract": contract, "reasons": reasons} if reasons else None

    from aria_core.services.goplus import goplus_client

    try:
        security = await goplus_client.get_token_security(contract)
    except Exception as exc:  # noqa: BLE001 — a GoPlus outage must never crash the cycle
        logger.info("rescan_open_position: GoPlus %s failed (%s)", contract, exc)
        security = None

    if security is not None and security.available:
        if security.is_honeypot and not snapshot.is_honeypot:
            reasons.append("honeypot détecté (absent à l'entrée)")
        if security.cannot_sell_all and not snapshot.cannot_sell:
            reasons.append("revente totale bloquée détectée (absente à l'entrée)")
        if security.hidden_owner and not snapshot.hidden_owner:
            reasons.append("owner caché détecté (absent à l'entrée)")
        if security.can_take_back_ownership and not snapshot.can_take_back_ownership:
            reasons.append("reprise de propriété possible détectée (absente à l'entrée)")

    from aria_core.services.blockscout import blockscout_client

    try:
        flags = await blockscout_client.check_contract_flags(contract)
    except Exception as exc:  # noqa: BLE001
        logger.info("rescan_open_position: Blockscout flags %s failed (%s)", contract, exc)
        flags = None
    if flags is not None and flags.available and flags.is_verified is False and snapshot.contract_verified:
        reasons.append("contrat n'est plus vérifié (l'était à l'entrée)")

    try:
        owner_now, owner_err = await blockscout_client.read_owner(contract)
    except Exception as exc:  # noqa: BLE001
        logger.info("rescan_open_position: Blockscout read_owner %s failed (%s)", contract, exc)
        owner_now, owner_err = None, str(exc)
    if owner_err is None and owner_now:
        was_renounced = (
            not snapshot.owner_address
            or snapshot.owner_address.lower() in _RENOUNCED_OWNER_MARKERS
        )
        if was_renounced and owner_now.lower() not in _RENOUNCED_OWNER_MARKERS:
            reasons.append(f"ownership repris par {owner_now} (renoncée ou inconnue à l'entrée)")

    if not reasons:
        return None
    return {"contract": contract, "reasons": reasons}


# ── USDC depeg -- blocks NEW entries, never already-open positions ───────────────────

USDC_DEPEG_THRESHOLD_PCT = 0.01  # 1% deviation from the $1 peg
USDC_COINGECKO_ID = "usd-coin"


async def usdc_depeg_pct() -> float | None:
    """Absolute deviation from the $1 peg, or ``None`` if the price is
    unavailable -- fail-open: a CoinGecko outage never blocks the cycle
    (dome doctrine), see ``is_usdc_depegged``."""
    from aria_core.services.coingecko import coingecko_client

    try:
        result = await coingecko_client.get_simple_price([USDC_COINGECKO_ID], vs_currencies=["usd"])
    except Exception as exc:  # noqa: BLE001
        logger.info("usdc_depeg_pct: CoinGecko failed (%s)", exc)
        return None
    if not result.available:
        return None
    price = result.prices.get(USDC_COINGECKO_ID, {}).get("usd")
    if not price or price <= 0:
        return None
    return abs(price - 1.0)


async def is_usdc_depegged() -> bool:
    pct = await usdc_depeg_pct()
    return pct is not None and pct > USDC_DEPEG_THRESHOLD_PCT
