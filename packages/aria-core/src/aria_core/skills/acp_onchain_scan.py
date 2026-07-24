"""On-chain context for ACP audit workflows — DexScreener (Base)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.services.blockscout import (
    UNAVAILABLE as ONCHAIN_UNAVAILABLE,
    ContractFlags,
    TokenHoldersResult,
    blockscout_client,
)
from aria_core.services.coingecko import TokenFundamentals, coingecko_client
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.dexscreener import fetch_token_pairs as _dexscreener_fetch_token_pairs
from aria_core.services.ohlcv import ohlcv_client
from aria_core.services.smart_money import _is_recognized_stablecoin, analyze_smart_money
from aria_core.skills.candlestick_patterns import CandlePattern, detect_patterns
from aria_core.skills.entry_signals import EntrySignal, detect_entry
from aria_core.skills.indicators import ema_series, macd_series
from aria_core.skills.mint_authority import SAFE_AUTHORITIES
from aria_core.skills.ta_levels import (
    Candle,
    EntryZone,
    TALevels,
    compute_levels,
    suggest_entry_zone,
)

_EMA_FAST_PERIOD = 12
_EMA_SLOW_PERIOD = 26


def _last_value(series: list[float | None]) -> float | None:
    """Last defined value of a series (None during warm-up) -- never an
    estimate, just the last point actually computed."""
    return next((v for v in reversed(series) if v is not None), None)

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# "Black hole" addresses: a large balance here is NOT a risky concentration
# (burned tokens / sent to the void). Excluded from the concentration
# calculation, same as the LP pool (whose large balance is structural, not a
# whale that could dump).
_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",  # common "community" burn
    "0x0000000000000000000000000000000000000001",  # sometimes used as a sink
}


def _is_burn_address(address: str | None) -> bool:
    """True if the address is a burn sink (legitimate holder of large shares).

    Beyond the known list, recognizes the "dead address" PATTERN: a body
    entirely zero, ending with (or prefixed by) ``dead`` -- e.g. 0x...0000dEaD.
    Deliberately broadened because projects burn to multiple variants of
    ``dead``.
    """
    if not address:
        return False
    a = address.strip().lower()
    if a in _BURN_ADDRESSES:
        return True
    body = a[2:] if a.startswith("0x") else a
    if len(body) != 40:
        return False
    if body.endswith("dead") and set(body[:-4]) <= {"0"}:
        return True
    if body.startswith("dead") and set(body[4:]) <= {"0"}:
        return True
    return False


def _holder_concentration(
    holders: "TokenHoldersResult", lp_address: str | None
) -> tuple[float | None, float | None, int]:
    """(% of the largest holder, % of the top 10, number of holders counted)
    EXCLUDING LP and burn.

    The LP pool and burn addresses legitimately hold large shares: including
    them would fail every token wrongly. Only real holders are counted.
    Returns ``(None, None, 0)`` if no usable data.
    """
    lp = (lp_address or "").lower()
    pcts: list[float] = []
    for h in holders.holders:
        if h.percentage is None:
            continue
        addr = (h.address or "").lower()
        if addr == lp or addr in _BURN_ADDRESSES:
            continue
        pcts.append(float(h.percentage))
    if not pcts:
        return None, None, 0
    pcts.sort(reverse=True)
    return pcts[0], sum(pcts[:10]), len(pcts)


async def _resolve_mint_authority(ctx: "TokenScanContext", token_address: str) -> None:
    """Classifies the authority of an external mint (renounced / launchpad /
    contract / dev / unknown).

    Best-effort and defensive: every network call can fail without ever
    blocking (authority -> 'unknown', cautious downstream). Only called IF
    ``has_mint`` is true, hence rare. Populates ``ctx.mint_authority`` /
    ``ctx.launchpad``.
    """
    from aria_core.skills.mint_authority import classify_authority, match_launchpad

    creator = None
    owner_addr = None
    owner_is_contract = None
    try:
        info = await blockscout_client.get_address_info(token_address)
        creator = info.creator_address if info.available else None
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("mint_authority: get_address_info(%s) failed (%s)", token_address, exc)

    # If already recognized as a launchpad, no need to read the owner (authority = protocol).
    if not match_launchpad(creator):
        try:
            owner_addr, _ = await blockscout_client.read_owner(token_address)
        except Exception as exc:  # noqa: BLE001
            logger.info("mint_authority: read_owner(%s) failed (%s)", token_address, exc)
        if owner_addr:
            try:
                oinfo = await blockscout_client.get_address_info(owner_addr)
                owner_is_contract = oinfo.is_contract if oinfo.available else None
            except Exception as exc:  # noqa: BLE001
                logger.info("mint_authority: owner info(%s) failed (%s)", owner_addr, exc)

    verdict = classify_authority(
        has_mint=ctx.has_mint,
        creator_address=creator,
        owner_address=owner_addr,
        owner_is_contract=owner_is_contract,
    )
    ctx.mint_authority = verdict.kind
    ctx.mint_authority_detail = verdict.detail
    ctx.launchpad = verdict.launchpad
_QUALITY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_quality.yaml"


@dataclass
class TokenScanContext:
    contract: str
    valid_address: bool
    pairs_found: int = 0
    best_pair: PairSnapshot | None = None
    risk_flags: list[str] = field(default_factory=list)
    security_score: int = 35
    lite_verdict: str = "CAUTION"
    data_source: str = "heuristic"
    # Technical analysis (data-gated: populated only if include_ta AND an OHLCV
    # series is available). With no data, these fields stay inert -> unchanged behavior.
    ta: TALevels | None = None
    ta_entry: EntryZone | None = None
    ta_candles: list[Candle] = field(default_factory=list)
    ta_timeframe: str | None = None
    # EMA/MACD (indicators.py) + golden pocket/RSI divergence setup (entry_signals.py) --
    # wired on 10/07 (operator decision), same ``include_ta`` guard as above.
    # Last values only (the LLM reasons on the current state, not the series).
    ta_ema_fast: float | None = None
    ta_ema_slow: float | None = None
    ta_macd_line: float | None = None
    ta_macd_signal: float | None = None
    ta_macd_histogram: float | None = None
    ta_golden_pocket_signal: EntrySignal | None = None
    # Candlestick patterns (candlestick_patterns.py, a tested module never
    # wired before this fix) -- same `include_ta` guard, same real OHLC candles.
    # Only the most recent patterns are kept (the LLM reasons on the current
    # state, not the whole history).
    ta_candle_patterns: list[CandlePattern] = field(default_factory=list)
    # Structured security barriers (populated at scan time if the on-chain
    # data exists; None otherwise). Expose in the clear what the score
    # aggregates, for a strict binary filter (cf. skills/safety_screen.py).
    # Concentration computed EXCLUDING the LP pool and burn addresses
    # (otherwise every token would fail wrongly).
    contract_verified: bool | None = None
    has_mint: bool | None = None
    has_blacklist: bool | None = None
    has_disable_transfers: bool | None = None
    top_holder_pct: float | None = None
    top10_holder_pct: float | None = None
    holders_counted: int | None = None
    # CoinGecko fundamentals (populated only if include_fundamentals AND data
    # is available). Exposed in the clear to feed the comparables ROI
    # projection (Vault 3, skills/roi_comparables.py) without a re-fetch.
    # None -> section omitted.
    market_cap_usd: float | None = None
    fully_diluted_valuation_usd: float | None = None
    categories: list[str] = field(default_factory=list)
    # Contract authority (resolved only if has_mint: an external mint exists).
    # Distinguishes a dev-controlled mint (danger) from a legitimate mint
    # (renounced, known launchpad, or contract-driven). See skills/mint_authority.py.
    mint_authority: str | None = None  # na/renounced/launchpad/contract/eoa/unknown
    mint_authority_detail: str = ""
    launchpad: str | None = None  # launchpad label if the deployer is recognized
    # Liquidity depth (liquidity / market cap). Populated if both are known
    # (so the VC analysis path with fundamentals). Thin market = fragile. Judged case by case.
    liq_mcap_ratio: float | None = None
    # Dev wallet behavior (populated if include_dev_behavior). Weighted signal
    # (aligned/neutral/concern/unknown) + factual observations. Feeds the
    # judgment, doesn't reject outright.
    dev_signal: str | None = None
    dev_points: list[str] = field(default_factory=list)
    # 22/07 -- task #4: share (0-100) of their received allocation that the
    # deployer has already resold, captured as-is
    # (DevWalletFacts.sold_pct_of_received) -- never derived from dev_signal (a
    # simple label), to allow a comparable numeric snapshot on a re-scan while
    # holding a VC position (Formula B). None if unresolved (unknown deployer,
    # no transfer) -- never a fabricated value.
    dev_sold_pct: float | None = None
    # 22/07 -- "disguised liquidity exit" signal (carried over from the
    # stress-test): wallets that received a significant share of the initial
    # distribution directly from the deployer/mint (via Dune, excluding
    # 'creator' already covered by dev_signal above) and hold almost nothing
    # today -- an insider that sells without ever carrying the 'creator' tag.
    # Populated if include_insider_check AND data is available. See
    # skills/insider_wallets.py. Never an outright rejection (LLM advisory only).
    insider_signal: str | None = None
    insider_points: list[str] = field(default_factory=list)
    # 22/07 -- "deployer reputation" signal: have they already created ANOTHER
    # contract already confirmed as a scam by ARIA (momentum_blacklist.py)?
    # Distinct from the behavior ON this token (dev_signal above) -- looks at
    # the same wallet's cross-token history. Populated if
    # include_deployer_reputation AND data is available. See
    # services/deployer_history.py. Never an outright rejection (LLM advisory only).
    deployer_reputation_signal: str | None = None
    deployer_reputation_points: list[str] = field(default_factory=list)
    # 23/07 -- "Sybil cluster" signal: holders grouped by common funding
    # source, beyond simple individual concentration (top_holder_pct above).
    # Populated if include_sybil_check AND data is available. Higher network
    # cost than the other signals (1 Blockscout call per verified holder) --
    # see skills/sybil_cluster.py. Never an outright rejection (LLM advisory only).
    sybil_cluster_signal: str | None = None
    sybil_cluster_points: list[str] = field(default_factory=list)
    # Dynamic GoPlus security (populated if include_honeypot AND data is
    # available). What Blockscout's static ABI can't see: is reselling
    # REALLY possible, real buy/sell taxes, hidden powers. None = not scanned
    # or unavailable -> strictly unchanged behavior (additive, data-gated).
    is_honeypot: bool | None = None
    cannot_sell: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    # 22/07 -- item #2 (post-stress-test hardening plan): GoPlus exposes an
    # additional hidden power, distinct from hidden_owner/can_take_back_ownership
    # -- the dev can change the tax/slippage rate AFTER the fact, without
    # regaining ownership. A token "clean" at scan time can become extractive
    # later without any other GoPlus signal detecting it. None = not
    # scanned/unavailable.
    slippage_modifiable: bool | None = None
    # 22/07 -- security gap found while observing a REALLY open momentum
    # position (CNX): GoPlus "Owner can change balance" = Yes was consulted
    # NOWHERE (neither VC nor momentum). A power DISTINCT from the classic
    # honeypot (which blocks RESELLING) -- here the owner can directly modify a
    # wallet's balance, a total-loss vector that is_honeypot/cannot_sell_all
    # don't capture. Same name as the source GoPlus field
    # (services/goplus.py::TokenSecurity), never renamed to stay traceable
    # end-to-end through the pipeline.
    owner_change_balance: bool | None = None
    # 24/07 -- operator question after a real confusion risk: GoPlus flags
    # "Proxy contract" (upgradeable logic) as a red warning regardless of the
    # issuer -- correct instinct for an anonymous deployer (the owner can
    # swap the logic at will), but a FALSE ALARM for a known regulated
    # stablecoin issuer (Circle's USDC/EURC, Tether's USDT all use this exact
    # architecture deliberately, for compliance -- e.g. sanctioned-address
    # freezing -- never as a rug vector). `is_proxy` alone was never surfaced
    # anywhere before this (captured by services/goplus.py, never consumed).
    # See `_apply_honeypot_signals` for the contextualized risk_flags text.
    is_proxy: bool | None = None
    # Virtuals bonding niche (populated ONLY if no DexScreener pair exists AND
    # the contract is genuinely indexed by Virtuals in pre-graduation status --
    # see services/virtuals.py). No DEX pair is NORMAL at this stage (DEX
    # liquidity only exists after graduation): without this field,
    # `_score_and_verdict` treated this as a generic security defect and could
    # produce an ill-founded AVOID.
    bonding_phase: bool = False
    bonding_progress: float | None = None  # 0.0-1.0, share of graduation threshold reached
    bonding_holder_count: int | None = None
    bonding_mcap_virtual: float | None = None  # denominated in VIRTUAL, not converted to USD
    # 22/07 -- fills a gap found while checking the bonding coverage of the
    # insider_wallets/deployer_history signals (both anchored on
    # ctx.best_pair.pair_created_at, which stays None as long as no DEX pair
    # exists -- NORMAL in pre-graduation bonding). `VirtualsToken.created_at`
    # (already collected by the same network call as bonding_phase above, zero
    # extra cost) serves as a fallback: creation date of the bonding
    # PROTOTYPE, not of a DEX pair, but the same time window remains relevant
    # for spotting an insider distribution that happened at launch.
    token_created_at_ms: int | None = None
    # Virtuals product diligence (11/07 audit, cf. skills/vc_analysis.py).
    # Populated AS SOON AS a token is found on Virtuals via
    # `_resolve_bonding_phase` (bonding or not -- zero extra network cost, same
    # call as above); for a token ALREADY graduated (a DEX pair exists, so
    # `_resolve_bonding_phase` is never called),
    # `vc_analysis._fetch_virtuals_product_diligence` does a best-effort
    # fallback via the same singleton client. DECLARATIVE text (the team
    # speaks about itself on its own virtuals.io page) -- never verified
    # on-chain, same doctrine as website_snapshot.
    virtuals_description: str | None = None
    virtuals_tokenomics: str | None = None
    virtuals_additional_details: str | None = None


@lru_cache(maxsize=1)
def _quality_cfg() -> dict[str, Any]:
    if not _QUALITY_PATH.is_file():
        return {}
    try:
        return yaml.safe_load(_QUALITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _onchain_thresholds() -> dict[str, Any]:
    return (_quality_cfg().get("onchain") or {}) if _quality_cfg() else {}


def _dex_chain_id() -> str:
    return str(_onchain_thresholds().get("chain_dex_id") or "base")


async def _fetch_token_pairs(contract: str) -> list[PairSnapshot]:
    """Delegates to ``services.dexscreener`` (14/07, #157) -- this client used
    to be hardcoded here; extracted to be reusable (wallet-scoring,
    triangulation with GeckoTerminal) without duplicating a second DexScreener
    call. `/vc` scan behavior strictly unchanged (same parsing, same
    dataclass)."""
    return await _dexscreener_fetch_token_pairs(contract, chain=_dex_chain_id())


def _apply_onchain_signals(
    flags: list[str],
    contract_flags: ContractFlags | None,
    holders: TokenHoldersResult | None,
    pair: PairSnapshot | None,
    *,
    mint_authority: str | None = None,
) -> int:
    """Additional Blockscout signals — read-only, purely additive.

    Unavailable on-chain data (rate limit, timeout, network error) never
    degrades the score: the flag reflects the absence of data, not a risk.
    Only positively confirmed signals (sensitive function detected, whale
    concentration) degrade the score.

    ``mint_authority`` (#164, fixed 22/07): a mint DETECTED in the ABI is only
    a risk if a DEV keeps control of it — a renounced mint/one driven by a
    known launchpad/locked in a contract (timelock/multisig/issuance) is
    neutralized by ``mint_authority.classify_authority``, exactly like the
    hard filter (``safety_screen._mint_is_dev_controlled``) already does.
    Before this fix, this function completely ignored ``mint_authority`` (it
    didn't even receive it as a parameter) and applied -30 on the mere
    presence of the function — a project with healthy vesting (reputable
    deployer, timelocked mint) fell below the score-70 threshold required by
    ``safety_screen`` and missed automatic sourcing, despite a hard filter that
    otherwise judged it clean. ``None``/``"unknown"``/``"eoa"`` remain
    fail-closed (penalty applied) — only a VERIFIED and safe authority
    (``SAFE_AUTHORITIES``) neutralizes the penalty.
    """
    delta = 0

    if contract_flags is not None:
        if not contract_flags.available:
            flags.append(f"Blockscout (audit contrat) : {contract_flags.error or ONCHAIN_UNAVAILABLE}.")
        elif contract_flags.is_verified is False:
            flags.append("Contrat non vérifié sur Blockscout — audit du code source impossible.")
        else:
            if contract_flags.has_mint:
                if mint_authority in SAFE_AUTHORITIES:
                    flags.append(
                        f"Fonction mint détectée mais autorité neutralisée ({mint_authority}) "
                        "— aucune pénalité."
                    )
                else:
                    flags.append("Fonction mint détectée dans le contrat — supply potentiellement inflatable.")
                    delta -= 30
            if contract_flags.has_blacklist:
                flags.append("Fonction blacklist détectée — l'équipe peut bloquer des wallets.")
                delta -= 30
            if contract_flags.has_disable_transfers:
                flags.append("Fonction de désactivation des transferts détectée — risque honeypot.")
                delta -= 30

    if holders is not None:
        if not holders.available:
            flags.append(f"Blockscout (holders) : {holders.error or ONCHAIN_UNAVAILABLE}.")
        else:
            if holders.error:
                flags.append(f"Blockscout (holders) : {holders.error}.")
            # Excludes the LP pool AND burn addresses: they legitimately hold
            # large shares (a burned supply isn't a whale). Consistent with
            # _holder_concentration (otherwise a deflationary token gets
            # wrongly penalized/rejected).
            known_lp = (pair.pair_address or "").lower() if pair else ""
            candidates = [
                h for h in holders.holders
                if (h.address or "").lower() != known_lp
                and (h.address or "").lower() not in _BURN_ADDRESSES
            ]
            top = max(candidates, key=lambda h: h.percentage or -1.0, default=None)
            if top is not None and top.percentage is not None and top.percentage > 50:
                flags.append(
                    f"Concentration whale — top holder détient {top.percentage:.1f}% "
                    "de la supply (hors LP et burn)."
                )
                delta -= 20

    return delta


_HIGH_DILUTION_FDV_RATIO = 3.0


def _apply_fundamentals_signals(flags: list[str], fundamentals: TokenFundamentals | None) -> int:
    """Additional CoinGecko signals — read-only, purely additive.

    Same as Blockscout: unavailable fundamental data (rate limit, timeout,
    unlisted token) never degrades the score. Only a high FDV/market-cap
    ratio (significant future dilution, upcoming vesting/unlocks) is a
    positively confirmed signal that degrades the score.
    """
    if fundamentals is None:
        return 0

    if not fundamentals.available:
        flags.append(f"CoinGecko (fondamentaux) : {fundamentals.error or 'donnée fondamentale indisponible'}.")
        return 0

    delta = 0
    mc = fundamentals.market_cap_usd
    fdv = fundamentals.fully_diluted_valuation_usd
    if mc and fdv and mc > 0:
        ratio = fdv / mc
        if ratio >= _HIGH_DILUTION_FDV_RATIO:
            flags.append(
                f"Dilution future importante — FDV/market cap = {ratio:.1f}x "
                "(supply non circulante conséquente, vesting/unlocks à surveiller)."
            )
            delta -= 10

    if fundamentals.market_cap_usd:
        flags.append(f"CoinGecko : market cap ${fundamentals.market_cap_usd:,.0f}.")
    if fundamentals.categories:
        flags.append(f"CoinGecko : catégorie(s) {', '.join(fundamentals.categories[:3])}.")

    return delta


def _score_and_verdict(
    ctx: TokenScanContext,
    pair: PairSnapshot | None,
    *,
    contract_flags: ContractFlags | None = None,
    holders: TokenHoldersResult | None = None,
) -> None:
    cfg = _onchain_thresholds()
    liq_caution = float(cfg.get("min_liquidity_usd_caution") or 5000)
    liq_danger = float(cfg.get("min_liquidity_usd_danger") or 500)
    min_vol = float(cfg.get("min_volume_24h_usd") or 1000)

    score = 50
    flags: list[str] = []

    if not ctx.valid_address:
        ctx.security_score = 15
        ctx.lite_verdict = "DANGER"
        ctx.risk_flags = ["Adresse contrat absente ou invalide."]
        return

    ca = ctx.contract.lower()
    if ca.endswith("0000000000000000000000000000000000000000"):
        ctx.security_score = 5
        ctx.lite_verdict = "DANGER"
        ctx.risk_flags = ["Adresse nulle — risque critique."]
        return

    onchain_flags: list[str] = []
    onchain_delta = _apply_onchain_signals(
        onchain_flags, contract_flags, holders, pair, mint_authority=ctx.mint_authority,
    )

    if not pair:
        if ctx.bonding_phase:
            # Virtuals token still on the bonding curve: the absence of a DEX
            # pair is NORMAL at this stage (DEX liquidity only exists after
            # graduation), not a generic security defect. Scored on the
            # available native signals.
            score = 50 + onchain_delta
            if ctx.bonding_progress is not None:
                score += round(ctx.bonding_progress * 15)
            if ctx.bonding_holder_count is not None and ctx.bonding_holder_count >= 50:
                score += 5
            score = max(5, min(95, score))
            ctx.security_score = score
            ctx.lite_verdict = "SAFE" if score >= 70 else ("DANGER" if score < 35 else "CAUTION")
            progress_note = (
                f"{ctx.bonding_progress:.0%} du seuil de graduation atteint"
                if ctx.bonding_progress is not None
                else "progression vers la graduation non disponible"
            )
            ctx.risk_flags = [
                f"Token Virtuals en phase de bonding (pré-graduation) — {progress_note}.",
                "Aucune paire DexScreener : normal à ce stade, la liquidité DEX n'existe "
                "qu'après graduation — pas un signal de danger.",
                *onchain_flags,
            ]
            return
        score = max(5, min(95, 35 + onchain_delta))
        ctx.security_score = score
        ctx.lite_verdict = "DANGER" if score < 35 else "CAUTION"
        ctx.risk_flags = [
            "Aucune paire DexScreener trouvée sur Base — liquidité non vérifiable.",
            "Confirmer le contrat sur Basescan avant toute allocation.",
            *onchain_flags,
        ]
        return

    liq = pair.liquidity_usd
    vol = pair.volume_24h_usd

    if liq < liq_danger:
        flags.append(f"Liquidité très faible (${liq:,.0f}) — risque de sortie difficile.")
        score -= 25
    elif liq < liq_caution:
        flags.append(f"Liquidité modérée (${liq:,.0f}) — size prudente recommandée.")
        score -= 12
    else:
        score += 10

    if vol < min_vol:
        flags.append(f"Volume 24h faible (${vol:,.0f}) — marché peu actif.")
        score -= 10
    else:
        score += 5

    total_tx = pair.buys_24h + pair.sells_24h
    if total_tx > 0:
        sell_ratio = pair.sells_24h / total_tx
        if sell_ratio > 0.7:
            flags.append(f"Pression vendeuse 24h ({sell_ratio:.0%} sells) — momentum négatif.")
            score -= 8
        elif sell_ratio < 0.35 and total_tx >= 20:
            score += 5

    if pair.price_change_24h <= -40:
        flags.append(f"Chute prix 24h ({pair.price_change_24h:.1f}%) — volatilité extrême.")
        score -= 15
    elif pair.price_change_24h <= -20:
        flags.append(f"Baisse prix 24h ({pair.price_change_24h:.1f}%).")
        score -= 8

    if pair.dex_id:
        flags.append(f"Meilleure paire : {pair.base_symbol}/{pair.quote_symbol} sur {pair.dex_id}.")

    score += onchain_delta
    flags.extend(onchain_flags)

    score = max(5, min(95, score))
    ctx.security_score = score
    ctx.risk_flags = flags

    if score >= 70 and liq >= liq_caution and vol >= min_vol:
        ctx.lite_verdict = "SAFE"
    elif score < 35 or liq < liq_danger:
        ctx.lite_verdict = "DANGER"
    else:
        ctx.lite_verdict = "CAUTION"


# Informational honeypot tax threshold (GoPlus): beyond this, the tax is
# flagged as extractive in risk_flags. The HARD barrier (pool rejection)
# lives in safety_screen.
_HONEYPOT_TAX_FLAG = 0.10  # 10%


def _apply_honeypot_signals(ctx: "TokenScanContext", sec) -> None:
    """Absorbs the GoPlus read into the context — additive, never blocking here.

    Populates the decision fields + risk_flags and adjusts the score only on
    POSITIVELY confirmed signals. An unavailability (None / available=False)
    degrades nothing: it's flagged as absence of data, not as risk (doctrine:
    a network outage doesn't ban a good token).
    """
    if sec is None or not sec.available:
        detail = (getattr(sec, "error", None) if sec else None) or ONCHAIN_UNAVAILABLE
        ctx.risk_flags.append(f"GoPlus (honeypot/taxes) : {detail}.")
        return

    ctx.is_honeypot = sec.is_honeypot
    ctx.cannot_sell = sec.cannot_sell_all
    ctx.buy_tax = sec.buy_tax
    ctx.sell_tax = sec.sell_tax
    ctx.hidden_owner = sec.hidden_owner
    ctx.can_take_back_ownership = sec.can_take_back_ownership
    ctx.slippage_modifiable = sec.slippage_modifiable
    ctx.owner_change_balance = sec.owner_change_balance
    ctx.is_proxy = sec.is_proxy

    delta = 0
    if sec.is_honeypot is True:
        ctx.risk_flags.append("HONEYPOT confirmé (GoPlus) — revente bloquée. À éviter.")
        delta -= 60
    if sec.cannot_sell_all is True:
        ctx.risk_flags.append("Vente totale impossible (GoPlus cannot_sell_all) — levier honeypot.")
        delta -= 40
    if sec.sell_tax is not None and sec.sell_tax >= _HONEYPOT_TAX_FLAG:
        ctx.risk_flags.append(f"Taxe de vente élevée {sec.sell_tax * 100:.0f}% (GoPlus) — extractif.")
        delta -= 20
    if sec.buy_tax is not None and sec.buy_tax >= _HONEYPOT_TAX_FLAG:
        ctx.risk_flags.append(f"Taxe d'achat élevée {sec.buy_tax * 100:.0f}% (GoPlus).")
        delta -= 10
    if sec.hidden_owner is True:
        ctx.risk_flags.append("Owner caché (GoPlus hidden_owner) — pouvoir dissimulé.")
        delta -= 20
    if sec.can_take_back_ownership is True:
        ctx.risk_flags.append("Reprise de propriété possible (GoPlus) — renoncement réversible.")
        delta -= 20
    if sec.slippage_modifiable is True:
        ctx.risk_flags.append(
            "Taxe/slippage modifiable après coup (GoPlus slippage_modifiable) — "
            "peut devenir extractif sans reprise de propriété visible."
        )
        delta -= 15
    if sec.owner_change_balance is True:
        ctx.risk_flags.append(
            "Owner peut modifier le solde d'un wallet (GoPlus owner_change_balance) — "
            "vecteur de perte totale, distinct du honeypot classique."
        )
        delta -= 40
    if sec.is_proxy is True:
        # 24/07 -- operator question after a real confusion risk (screenshot of
        # GoPlus flagging EURC's "Proxy contract" red): a proxy is genuinely a
        # risk signal for an ANONYMOUS deployer (the owner can swap the logic
        # at will), but Circle (USDC/EURC) and Tether (USDT) deliberately use
        # this exact architecture for regulatory compliance (e.g. freezing a
        # sanctioned address) -- never surfaced as a plain, uncontextualized
        # flag, exactly the doctrine already applied to mint authority
        # (skills/mint_authority.py: "renounced" vs "launchpad", never one
        # brand flag for every case). No score delta either way -- a proxy
        # alone (without another confirmed hard signal above) is a structural
        # fact to weigh, not a penalty to apply mechanically.
        if _is_recognized_stablecoin(ctx.contract):
            ctx.risk_flags.append(
                "Contrat proxy upgradeable (GoPlus is_proxy) — normal et attendu pour cet "
                "émetteur de stablecoin réglementé déjà reconnu (ex. Circle/Tether) : sert la "
                "conformité (gel d'adresses sanctionnées), jamais observé comme vecteur de rug "
                "chez ces émetteurs. Pas un signal de danger ici."
            )
        else:
            ctx.risk_flags.append(
                "Contrat proxy upgradeable (GoPlus is_proxy) — la logique peut être modifiée "
                "par l'owner à tout moment. Normal pour un émetteur institutionnel connu et "
                "réglementé, un vrai point d'attention pour un déployeur anonyme/inconnu — à "
                "pondérer avec les autres signaux d'owner ci-dessus (hidden_owner, "
                "can_take_back_ownership, owner_change_balance)."
            )

    if delta:
        ctx.security_score = max(5, min(95, ctx.security_score + delta))
    # A confirmed honeypot / impossible resell = unambiguous danger: the
    # readable verdict is aligned so the analysis AND the filter stay
    # consistent. owner_change_balance (22/07) joins this group -- a power of
    # the SAME severity (direct total loss), not just a moderate distrust
    # signal like hidden_owner/slippage.
    if sec.is_honeypot is True or sec.cannot_sell_all is True or sec.owner_change_balance is True:
        ctx.lite_verdict = "DANGER"


def _iso_to_epoch_ms(iso_ts: str | None) -> int | None:
    """``VirtualsToken.created_at`` (ISO 8601, e.g. '2026-07-06T12:00:00.000Z')
    -> ms epoch, same conversion as ``PairSnapshot.pair_created_at``. None if
    unreadable -- never a fabricated date."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return int(dt.timestamp() * 1000)


async def _resolve_bonding_phase(ctx: "TokenScanContext", contract: str) -> None:
    """Best-effort, called ONLY when no DexScreener pair was found: a contract
    with no pool can legitimately be a Virtuals token still on the bonding
    curve (no DEX liquidity before graduation — normal, not a defect). Any
    Virtuals outage leaves `ctx.bonding_phase = False` (unchanged behavior,
    generic "no pair" verdict) — never blocking, never fabricated data.

    ALSO captures (11/07 audit) `ctx.virtuals_description`/`_tokenomics`/
    `_additional_details` as soon as a Virtuals token is found -- bonding or
    not -- since the same network call has already fetched this payload: zero
    extra cost to feed the product diligence
    (`vc_analysis._fetch_product_diligence`).

    On-chain fallback (11/07 audit, gate OFF by default
    `ARIA_ONCHAIN_GRADUATION_ENABLED`): when the API heuristic
    (`graduation_progress`) returns `None`, attempts a real on-chain read
    (`services/base_onchain.py`) -- PARTIAL and honest coverage (only one
    known instance of the Bonding V5 contract), never blocking (separate
    thread via `asyncio.to_thread`, same conventions as
    `mailer.py`/`x_twitter.py` for synchronous calls), never a fabricated
    value if the read fails or doesn't cover this token."""
    try:
        from aria_core.services.base_onchain import onchain_graduation_enabled, onchain_graduation_progress
        from aria_core.services.virtuals import graduation_progress, is_in_bonding, virtuals_client

        token = await virtuals_client.fetch_by_address(contract)
        if token is None:
            return
        ctx.virtuals_description = token.description
        ctx.virtuals_tokenomics = token.tokenomics
        ctx.virtuals_additional_details = token.additional_details
        ctx.token_created_at_ms = _iso_to_epoch_ms(token.created_at)
        if is_in_bonding(token):
            ctx.bonding_phase = True
            ctx.bonding_progress = graduation_progress(token)
            if ctx.bonding_progress is None and onchain_graduation_enabled():
                ctx.bonding_progress = await asyncio.to_thread(
                    onchain_graduation_progress,
                    pair_address=token.pair_address,
                    token_address=token.pre_token_address or token.token_address,
                )
            ctx.bonding_holder_count = token.holder_count
            ctx.bonding_mcap_virtual = token.mcap
    except Exception:  # noqa: BLE001 — best-effort, never breaks the scan
        pass


async def scan_base_token(
    contract: str,
    *,
    include_smart_money: bool = False,
    include_fundamentals: bool = False,
    include_ta: bool = False,
    include_dev_behavior: bool = False,
    include_honeypot: bool = False,
    include_insider_check: bool = False,
    include_deployer_reputation: bool = False,
    include_sybil_check: bool = False,
) -> TokenScanContext:
    """Fetch DexScreener + compute heuristic security score.

    `include_smart_money` is disabled by default: the wallet-tracker analysis
    makes one Blockscout call per top holder (throttle ~0.35s/call) and would
    slow down every standard scan. Enable explicitly for a deeper analysis
    (e.g. Telegram command /scan <address> smart).

    `include_fundamentals` is disabled by default: the CoinGecko throttle
    (~2.2s/call, public tier) would slow down every standard scan. Enable
    explicitly (e.g. /scan <address> fond).

    `include_ta` is disabled by default: fetches the pool's OHLCV series
    (GeckoTerminal, throttle ~2.2s/call) and derives levels + entry zone
    (facts-only). Populates ctx.ta / ctx.ta_entry / ctx.ta_candles ONLY if a
    series is available; otherwise these fields stay None -> unchanged
    behavior. Wired on 10/07 (operator decision): EMA/MACD
    (ctx.ta_ema_*/ctx.ta_macd_*) and the golden pocket + RSI divergence setup
    (ctx.ta_golden_pocket_signal), same facts-only fields, same `include_ta`
    guard.

    `include_insider_check` is disabled by default: one extra Dune Analytics
    call (billed, SQL query latency) per scan. Spots wallets that received a
    direct insider distribution (excluding 'creator') and checks whether
    they've since sold everything (skills/insider_wallets.py). Reuses the
    holders already fetched above -- zero extra Blockscout call.

    `include_deployer_reputation` is disabled by default: bounded transaction
    history (Blockscout, `get_transactions_bounded`) of the deployer wallet
    looking for OTHER contracts already created, cross-checked against ARIA's
    own blacklist (services/deployer_history.py). Advisory signal only, never
    a veto.

    `include_sybil_check` is disabled by default: groups the already-fetched
    top holders by common funding source (skills/sybil_cluster.py) --
    NOTABLY more network-expensive than the other signals (up to 15 bounded
    Blockscout calls, one per verified holder). Advisory signal only, never a
    veto.
    """
    ca = (contract or "").strip()
    valid = bool(_ADDR_RE.match(ca))
    ctx = TokenScanContext(contract=ca, valid_address=valid)

    if not valid:
        _score_and_verdict(ctx, None)
        return ctx

    pairs, contract_flags, holders = await asyncio.gather(
        _fetch_token_pairs(ca),
        blockscout_client.check_contract_flags(ca),
        blockscout_client.get_token_holders(ca),
    )
    # 19/07 -- same fix as momentum_entry._best_pair/paper_trader.
    # _default_pair_lookup (real bug found under real conditions, position
    # PLAZM #21 == actually ESHARE): ``fetch_token_pairs`` returns EVERY pair
    # involving ``ca``, including as a simple QUOTE of another token's pool.
    # Without this filter, /vc could analyze and publish (PDF report sent by
    # email) the price/OHLCV/project links of a token completely different
    # from the one actually scanned.
    ca_lower = ca.strip().lower()
    own_pairs = [p for p in pairs if (p.base_address or "").lower() == ca_lower]
    ctx.pairs_found = len(own_pairs)
    if own_pairs:
        best = max(own_pairs, key=lambda p: p.liquidity_usd)
        ctx.best_pair = best
        ctx.data_source = "dexscreener"
    else:
        await _resolve_bonding_phase(ctx, ca)

    # Exposes the security barriers in the clear (the binary filter reads
    # them) — moved BEFORE _score_and_verdict (22/07, fixes #164): the score
    # used to read has_mint blindly because contract_verified/has_mint/
    # mint_authority were only set AFTER the score computation, so
    # ctx.mint_authority was still None (never resolved) at the moment the
    # mint penalty was applied.
    if contract_flags is not None and contract_flags.available:
        ctx.contract_verified = contract_flags.is_verified
        ctx.has_mint = contract_flags.has_mint
        ctx.has_blacklist = contract_flags.has_blacklist
        ctx.has_disable_transfers = contract_flags.has_disable_transfers
    if holders is not None and holders.available:
        lp = ctx.best_pair.pair_address if ctx.best_pair else None
        top, top10, counted = _holder_concentration(holders, lp)
        ctx.top_holder_pct = top
        ctx.top10_holder_pct = top10
        ctx.holders_counted = counted

    # Mint authority: only if an EXTERNAL mint exists (rare since the ABI
    # fix). A legitimate mint (renounced, known launchpad, contract) must not
    # get a good token rejected; only a mint controlled by a dev wallet is a
    # danger. Best-effort: any unavailability -> 'unknown' (cautious
    # downstream), never blocking. Must imperatively run before
    # _score_and_verdict (see comment above).
    if ctx.has_mint is True:
        await _resolve_mint_authority(ctx, ca)

    _score_and_verdict(ctx, ctx.best_pair, contract_flags=contract_flags, holders=holders)

    if include_smart_money:
        smart_money = await analyze_smart_money(
            ca,
            holders,
            client=blockscout_client,
            lp_address=ctx.best_pair.pair_address if ctx.best_pair else None,
            pair_created_at_ms=ctx.best_pair.pair_created_at if ctx.best_pair else None,
        )
        if smart_money.available:
            ctx.security_score = max(5, min(95, ctx.security_score + smart_money.score_delta))
            ctx.risk_flags.extend(smart_money.flags)
        else:
            ctx.risk_flags.append(f"Smart-money : {smart_money.error or ONCHAIN_UNAVAILABLE}.")

    if include_fundamentals:
        fundamentals = await coingecko_client.get_token_fundamentals(ca)
        fundamentals_flags: list[str] = []
        fundamentals_delta = _apply_fundamentals_signals(fundamentals_flags, fundamentals)
        ctx.security_score = max(5, min(95, ctx.security_score + fundamentals_delta))
        ctx.risk_flags.extend(fundamentals_flags)
        if fundamentals and fundamentals.available:
            ctx.market_cap_usd = fundamentals.market_cap_usd
            ctx.fully_diluted_valuation_usd = fundamentals.fully_diluted_valuation_usd
            ctx.categories = list(fundamentals.categories or [])
            # Liquidity depth: is the market thin relative to the valuation?
            # Neutralized on a bonding curve (exponential liquidity, thin at
            # the start -> the ratio isn't a fragility signal).
            if ctx.market_cap_usd and ctx.best_pair:
                from aria_core.skills.liquidity_depth import assess_liquidity_depth
                from aria_core.skills.mint_authority import is_bonding_launchpad

                depth = assess_liquidity_depth(
                    ctx.best_pair.liquidity_usd,
                    ctx.market_cap_usd,
                    bonding_curve=is_bonding_launchpad(ctx.launchpad),
                )
                ctx.liq_mcap_ratio = depth.ratio
                if depth.healthy is False:
                    ctx.risk_flags.append(f"Liquidité : {depth.note}.")

    if include_ta and ctx.best_pair and ctx.best_pair.pair_address:
        ohlcv = await ohlcv_client.get_ohlcv(ctx.best_pair.pair_address)
        if ohlcv.available and ohlcv.candles:
            ctx.ta_candles = ohlcv.candles
            ctx.ta_timeframe = ohlcv.timeframe
            ctx.ta = compute_levels(ohlcv.candles)
            ctx.ta_entry = suggest_entry_zone(ctx.ta)

            closes = [c.close for c in ohlcv.candles]
            ctx.ta_ema_fast = _last_value(ema_series(closes, _EMA_FAST_PERIOD))
            ctx.ta_ema_slow = _last_value(ema_series(closes, _EMA_SLOW_PERIOD))
            macd_line, macd_signal, macd_hist = macd_series(closes)
            ctx.ta_macd_line = _last_value(macd_line)
            ctx.ta_macd_signal = _last_value(macd_signal)
            ctx.ta_macd_histogram = _last_value(macd_hist)
            ctx.ta_golden_pocket_signal = detect_entry(ohlcv.candles)
            ctx.ta_candle_patterns = detect_patterns(ohlcv.candles)[-3:]

    # Dev wallet behavior: committed builder vs. farmer (contextual judgment,
    # never an outright rejection). Best-effort; any unavailability -> 'unknown'.
    if include_dev_behavior:
        await _resolve_dev_behavior(ctx, ca)

    # "Disguised liquidity exit" signal (22/07): insider wallets excluding
    # 'creator' that received a direct distribution and have already sold
    # everything. Best-effort, never blocking; reuses `holders` already
    # fetched above (no re-fetch).
    if include_insider_check:
        await _resolve_insider_wallets(ctx, ca, holders)

    # "Deployer reputation" signal (22/07): other contracts already created by
    # the same wallet, cross-checked against ARIA's own blacklist.
    # Best-effort, never blocking.
    if include_deployer_reputation:
        await _resolve_deployer_reputation(ctx, ca)

    # "Sybil cluster" signal (23/07): holders grouped by common funding
    # source. Best-effort, never blocking. Reuses `holders` already fetched --
    # zero re-fetch of the holder LIST (but one network call per verified
    # holder for their funding source, a real cost higher than the other
    # signals).
    if include_sybil_check:
        await _resolve_sybil_cluster(ctx, holders)

    # Dynamic security (honeypot / real taxes / hidden powers) via GoPlus.
    # Disabled by default (one more network call); enabled on the VC analysis
    # path where a real decision is made. Additive: with no data, ctx unchanged.
    if include_honeypot:
        from aria_core.services.goplus import goplus_client

        sec = await goplus_client.get_token_security(ca)
        _apply_honeypot_signals(ctx, sec)

    return ctx


async def _resolve_dev_behavior(ctx: "TokenScanContext", token_address: str) -> None:
    """Gathers + judges the deployer wallet's behavior. Defensive, never blocking."""
    from aria_core.skills.dev_wallet import (
        gather_dev_wallet_facts,
        judge_dev_wallet,
    )
    from aria_core.skills.mint_authority import launchpad_norms

    try:
        info = await blockscout_client.get_address_info(token_address)
        creator = info.creator_address if info.available else None
        if not creator:
            ctx.dev_signal = "unknown"
            ctx.dev_points = ["déployeur du contrat inconnu"]
            return
        facts = await gather_dev_wallet_facts(
            token_address,
            creator,
            lp_address=ctx.best_pair.pair_address if ctx.best_pair else None,
        )
        norms = launchpad_norms(ctx.launchpad)
        team_norm = norms.get("team_allocation_pct")
        team_norm = tuple(team_norm) if isinstance(team_norm, (list, tuple)) and len(team_norm) == 2 else None
        verdict = judge_dev_wallet(facts, launchpad_team_norm=team_norm)
        ctx.dev_signal = verdict.signal
        ctx.dev_points = verdict.points
        ctx.dev_sold_pct = facts.sold_pct_of_received
    except Exception as exc:  # noqa: BLE001 — dev behavior is a bonus, never blocking
        logger.info("dev_behavior: analysis of %s failed (%s)", token_address, exc)
        ctx.dev_signal = "unknown"
        ctx.dev_points = []


async def _resolve_insider_wallets(ctx: "TokenScanContext", token_address: str, holders) -> None:
    """Gathers + judges insider wallets excluding 'creator'. Defensive, never blocking.

    Requires a known deployer (resolved by `_resolve_dev_behavior`, called
    just before if `include_dev_behavior` is also active -- otherwise
    re-resolved here) and a reference date to bound the window:
    `ctx.best_pair.pair_created_at` (graduated DEX pair) OTHERWISE
    `ctx.token_created_at_ms` (22/07 fallback -- Virtuals bonding prototype
    creation date, same network call as bonding_phase, zero extra cost) --
    with neither of the two (contract neither graduated nor known to
    Virtuals), the signal is simply left unpopulated, never a block."""
    from aria_core.skills.insider_wallets import gather_insider_wallet_facts, judge_insider_wallets

    try:
        creator = None
        info = await blockscout_client.get_address_info(token_address)
        creator = info.creator_address if info.available else None
        reference_ts = (
            ctx.best_pair.pair_created_at if ctx.best_pair and ctx.best_pair.pair_created_at
            else ctx.token_created_at_ms
        )
        if not creator or not reference_ts:
            ctx.insider_signal = "unknown"
            ctx.insider_points = ["déployeur ou date de création de référence inconnus"]
            return
        facts = await gather_insider_wallet_facts(
            token_address,
            creator,
            pair_created_at_ms=reference_ts,
            lp_address=ctx.best_pair.pair_address if ctx.best_pair else None,
            holders=holders,
        )
        verdict = judge_insider_wallets(facts)
        ctx.insider_signal = verdict.signal
        ctx.insider_points = verdict.points
    except Exception as exc:  # noqa: BLE001 — bonus signal, never blocking
        logger.info("insider_wallets: analysis of %s failed (%s)", token_address, exc)
        ctx.insider_signal = "unknown"
        ctx.insider_points = []


async def _resolve_deployer_reputation(ctx: "TokenScanContext", token_address: str) -> None:
    """Gathers + judges the deployer's cross-token reputation. Defensive, never blocking."""
    from aria_core.services.deployer_history import gather_deployer_history_facts, judge_deployer_history

    try:
        info = await blockscout_client.get_address_info(token_address)
        creator = info.creator_address if info.available else None
        if not creator:
            ctx.deployer_reputation_signal = "unknown"
            ctx.deployer_reputation_points = ["déployeur du contrat inconnu"]
            return
        facts = await gather_deployer_history_facts(creator, exclude_contract=token_address)
        verdict = judge_deployer_history(facts)
        ctx.deployer_reputation_signal = verdict.signal
        ctx.deployer_reputation_points = verdict.points
    except Exception as exc:  # noqa: BLE001 — bonus signal, never blocking
        logger.info("deployer_history: analysis of %s failed (%s)", token_address, exc)
        ctx.deployer_reputation_signal = "unknown"
        ctx.deployer_reputation_points = []


async def _resolve_sybil_cluster(ctx: "TokenScanContext", holders: "TokenHoldersResult | None") -> None:
    """Gathers + judges the Sybil clustering of holders. Defensive, never blocking."""
    from aria_core.skills.sybil_cluster import gather_sybil_cluster_facts, judge_sybil_cluster

    if holders is None or not holders.available or not holders.holders:
        ctx.sybil_cluster_signal = "unknown"
        ctx.sybil_cluster_points = ["holders indisponibles"]
        return

    lp = (ctx.best_pair.pair_address if ctx.best_pair else "") or ""
    exclude = {lp.lower()} | _BURN_ADDRESSES if lp else set(_BURN_ADDRESSES)
    try:
        facts = await gather_sybil_cluster_facts(holders.holders, exclude_addresses=exclude)
        verdict = judge_sybil_cluster(facts)
        ctx.sybil_cluster_signal = verdict.signal
        ctx.sybil_cluster_points = verdict.points
    except Exception as exc:  # noqa: BLE001 — bonus signal, never blocking
        logger.info("sybil_cluster: analysis of %s failed (%s)", ctx.contract, exc)
        ctx.sybil_cluster_signal = "unknown"
        ctx.sybil_cluster_points = []


def scan_base_token_sync(contract: str) -> TokenScanContext:
    """Sync wrapper for provider poll (no running loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(scan_base_token(contract))
    # Called from async context — should use await scan_base_token directly
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, scan_base_token(contract)).result()