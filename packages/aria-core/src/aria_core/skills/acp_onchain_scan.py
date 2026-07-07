"""On-chain context for ACP audit workflows — DexScreener (Base)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from aria_core.services.blockscout import (
    UNAVAILABLE as ONCHAIN_UNAVAILABLE,
    ContractFlags,
    TokenHoldersResult,
    blockscout_client,
)
from aria_core.services.coingecko import TokenFundamentals, coingecko_client
from aria_core.services.ohlcv import ohlcv_client
from aria_core.services.smart_money import analyze_smart_money
from aria_core.skills.ta_levels import (
    Candle,
    EntryZone,
    TALevels,
    compute_levels,
    suggest_entry_zone,
)

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_DEX_BASE = "https://api.dexscreener.com"
_QUALITY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "acp_quality.yaml"


@dataclass
class PairSnapshot:
    pair_address: str = ""
    dex_id: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    price_usd: float = 0.0
    price_change_24h: float = 0.0
    buys_24h: int = 0
    sells_24h: int = 0
    pair_created_at: int | None = None
    base_symbol: str = ""
    quote_symbol: str = ""
    project_links: list[dict] = field(default_factory=list)


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
    # Analyse technique (data-gated : peuplé uniquement si include_ta ET série OHLCV
    # disponible). Sans donnée, ces champs restent inertes → comportement inchangé.
    ta: TALevels | None = None
    ta_entry: EntryZone | None = None
    ta_candles: list[Candle] = field(default_factory=list)
    ta_timeframe: str | None = None


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


_SOCIAL_LABELS = {
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "telegram": "Telegram",
    "discord": "Discord",
    "github": "GitHub",
    "reddit": "Reddit",
}


def _extract_project_links(raw: dict) -> list[dict]:
    """Liens officiels déclarés par le projet (DexScreener `info.websites`/`socials`).

    Aucune estimation : uniquement ce que DexScreener retourne réellement, et
    uniquement des URL http(s) (allowlist de schéma — défense en profondeur,
    la donnée vient d'un tiers non fiable et sera de toute façon revalidée
    avant tout rendu HTML cliquable).
    """
    info = raw.get("info")
    if not isinstance(info, dict):
        return []

    links: list[dict] = []
    for site in info.get("websites") or []:
        if not isinstance(site, dict):
            continue
        url = str(site.get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            links.append({"label": str(site.get("label") or "Site officiel"), "url": url})

    for social in info.get("socials") or []:
        if not isinstance(social, dict):
            continue
        url = str(social.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        kind = str(social.get("type") or "").strip().lower()
        links.append({"label": _SOCIAL_LABELS.get(kind, kind.capitalize() or "Lien"), "url": url})

    return links


def _parse_pair(raw: dict) -> PairSnapshot:
    liq = raw.get("liquidity") or {}
    vol = raw.get("volume") or {}
    txns = raw.get("txns") or {}
    h24 = txns.get("h24") if isinstance(txns, dict) else {}
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    return PairSnapshot(
        pair_address=str(raw.get("pairAddress") or ""),
        dex_id=str(raw.get("dexId") or ""),
        liquidity_usd=float(liq.get("usd") or 0),
        volume_24h_usd=float(vol.get("h24") or 0),
        price_usd=float(raw.get("priceUsd") or 0),
        price_change_24h=float(raw.get("priceChange", {}).get("h24") or 0)
        if isinstance(raw.get("priceChange"), dict)
        else 0.0,
        buys_24h=int(h24.get("buys") or 0) if isinstance(h24, dict) else 0,
        sells_24h=int(h24.get("sells") or 0) if isinstance(h24, dict) else 0,
        pair_created_at=int(raw.get("pairCreatedAt") or 0) or None,
        base_symbol=str(base.get("symbol") or ""),
        quote_symbol=str(quote.get("symbol") or ""),
        project_links=_extract_project_links(raw),
    )


async def _fetch_token_pairs(contract: str) -> list[PairSnapshot]:
    chain = _dex_chain_id()
    url = f"{_DEX_BASE}/token-pairs/v1/{chain}/{contract}"
    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("DexScreener token-pairs %s: %s", contract[:10], exc)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_pair(row) for row in data if isinstance(row, dict)]


def _apply_onchain_signals(
    flags: list[str],
    contract_flags: ContractFlags | None,
    holders: TokenHoldersResult | None,
    pair: PairSnapshot | None,
) -> int:
    """Signaux Blockscout additionnels — lecture seule, purement additifs.

    Une donnée on-chain indisponible (rate limit, timeout, erreur réseau) ne
    dégrade jamais le score : le flag reflète l'absence de donnée, pas un
    risque. Seuls des signaux positivement confirmés (fonction sensible
    détectée, concentration whale) dégradent le score.
    """
    delta = 0

    if contract_flags is not None:
        if not contract_flags.available:
            flags.append(f"Blockscout (audit contrat) : {contract_flags.error or ONCHAIN_UNAVAILABLE}.")
        elif contract_flags.is_verified is False:
            flags.append("Contrat non vérifié sur Blockscout — audit du code source impossible.")
        else:
            if contract_flags.has_mint:
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
            known_lp = (pair.pair_address or "").lower() if pair else ""
            candidates = [h for h in holders.holders if (h.address or "").lower() != known_lp]
            top = max(candidates, key=lambda h: h.percentage or -1.0, default=None)
            if top is not None and top.percentage is not None and top.percentage > 50:
                flags.append(
                    f"Concentration whale — top holder détient {top.percentage:.1f}% "
                    "de la supply (hors LP connu)."
                )
                delta -= 20

    return delta


_HIGH_DILUTION_FDV_RATIO = 3.0


def _apply_fundamentals_signals(flags: list[str], fundamentals: TokenFundamentals | None) -> int:
    """Signaux CoinGecko additionnels — lecture seule, purement additifs.

    Comme pour Blockscout : une donnée fondamentale indisponible (rate limit,
    timeout, token non listé) ne dégrade jamais le score. Seul un ratio
    FDV/market cap élevé (dilution future significative, vesting/unlocks à
    venir) est un signal positivement confirmé qui dégrade le score.
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
    onchain_delta = _apply_onchain_signals(onchain_flags, contract_flags, holders, pair)

    if not pair:
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


async def scan_base_token(
    contract: str,
    *,
    include_smart_money: bool = False,
    include_fundamentals: bool = False,
    include_ta: bool = False,
) -> TokenScanContext:
    """Fetch DexScreener + compute heuristic security score.

    `include_smart_money` est desactive par defaut : l'analyse wallet-tracker
    fait un appel Blockscout par top holder (throttle ~0.35s/appel) et
    ralentirait chaque scan standard. A activer explicitement pour une
    analyse plus poussee (ex. commande Telegram /scan <adresse> smart).

    `include_fundamentals` est desactive par defaut : le throttle CoinGecko
    (~2.2s/appel, tier public) ralentirait chaque scan standard. A activer
    explicitement (ex. /scan <adresse> fond).

    `include_ta` est desactive par defaut : recupere la serie OHLCV du pool
    (GeckoTerminal, throttle ~2.2s/appel) et derive niveaux + zone d'entree
    (facts-only). Peuple ctx.ta / ctx.ta_entry / ctx.ta_candles UNIQUEMENT si une
    serie est disponible ; sinon ces champs restent None → comportement inchange.
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
    ctx.pairs_found = len(pairs)
    if pairs:
        best = max(pairs, key=lambda p: p.liquidity_usd)
        ctx.best_pair = best
        ctx.data_source = "dexscreener"
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

    if include_ta and ctx.best_pair and ctx.best_pair.pair_address:
        ohlcv = await ohlcv_client.get_ohlcv(ctx.best_pair.pair_address)
        if ohlcv.available and ohlcv.candles:
            ctx.ta_candles = ohlcv.candles
            ctx.ta_timeframe = ohlcv.timeframe
            ctx.ta = compute_levels(ohlcv.candles)
            ctx.ta_entry = suggest_entry_zone(ctx.ta)

    return ctx


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