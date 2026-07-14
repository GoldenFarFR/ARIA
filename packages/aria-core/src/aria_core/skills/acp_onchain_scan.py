"""On-chain context for ACP audit workflows — DexScreener (Base)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
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
from aria_core.services.smart_money import analyze_smart_money
from aria_core.skills.candlestick_patterns import CandlePattern, detect_patterns
from aria_core.skills.entry_signals import EntrySignal, detect_entry
from aria_core.skills.indicators import ema_series, macd_series
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
    """Dernière valeur définie d'une série (None pendant la chauffe) -- jamais
    une estimation, juste le dernier point réellement calculé."""
    return next((v for v in reversed(series) if v is not None), None)

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Adresses de « trou noir » : un gros solde ici n'est PAS une concentration risquée
# (tokens brûlés / envoyés au néant). À exclure du calcul de concentration, tout
# comme le pool LP (dont le gros solde est structurel, pas une baleine qui peut dumper).
_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0xdead000000000000000042069420694206942069",  # burn « communautaire » répandu
    "0x0000000000000000000000000000000000000001",  # parfois utilisée comme puits
}


def _is_burn_address(address: str | None) -> bool:
    """True si l'adresse est un puits de burn (détenteur légitime de grosses parts).

    Au-delà de la liste connue, reconnaît le MOTIF « adresse morte » : un corps
    entièrement à zéro terminé (ou préfixé) par ``dead`` — ex. 0x…0000dEaD. Élargi
    volontairement car les projets brûlent vers des variantes multiples de ``dead``.
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
    """(% du plus gros holder, % du top 10, nombre de holders comptés) HORS LP et burn.

    Le pool LP et les adresses de burn détiennent légitimement de grosses parts :
    les inclure ferait échouer tout token à tort. On ne compte que les vrais porteurs.
    Retourne ``(None, None, 0)`` si aucune donnée exploitable.
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
    """Classe l'autorité d'un mint externe (renoncé / launchpad / contrat / dev / inconnu).

    Best-effort et défensif : chaque appel réseau peut échouer sans jamais bloquer
    (autorité -> 'unknown', prudent en aval). N'est appelée QUE si ``has_mint`` est
    vrai, donc rare. Peuple ``ctx.mint_authority`` / ``ctx.launchpad``.
    """
    from aria_core.skills.mint_authority import classify_authority, match_launchpad

    creator = None
    owner_addr = None
    owner_is_contract = None
    try:
        info = await blockscout_client.get_address_info(token_address)
        creator = info.creator_address if info.available else None
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        logger.info("mint_authority: get_address_info(%s) échoué (%s)", token_address, exc)

    # Si déjà reconnu comme launchpad, inutile de lire l'owner (autorité = protocole).
    if not match_launchpad(creator):
        try:
            owner_addr, _ = await blockscout_client.read_owner(token_address)
        except Exception as exc:  # noqa: BLE001
            logger.info("mint_authority: read_owner(%s) échoué (%s)", token_address, exc)
        if owner_addr:
            try:
                oinfo = await blockscout_client.get_address_info(owner_addr)
                owner_is_contract = oinfo.is_contract if oinfo.available else None
            except Exception as exc:  # noqa: BLE001
                logger.info("mint_authority: owner info(%s) échoué (%s)", owner_addr, exc)

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
    # Analyse technique (data-gated : peuplé uniquement si include_ta ET série OHLCV
    # disponible). Sans donnée, ces champs restent inertes → comportement inchangé.
    ta: TALevels | None = None
    ta_entry: EntryZone | None = None
    ta_candles: list[Candle] = field(default_factory=list)
    ta_timeframe: str | None = None
    # EMA/MACD (indicators.py) + setup golden pocket/divergence RSI (entry_signals.py) --
    # câblés le 10/07 (décision opérateur), même garde ``include_ta`` que ci-dessus.
    # Dernières valeurs seulement (le LLM raisonne sur l'état courant, pas la série).
    ta_ema_fast: float | None = None
    ta_ema_slow: float | None = None
    ta_macd_line: float | None = None
    ta_macd_signal: float | None = None
    ta_macd_histogram: float | None = None
    ta_golden_pocket_signal: EntrySignal | None = None
    # Patterns de bougies (candlestick_patterns.py, module testé mais jamais câblé
    # avant ce correctif) -- même garde `include_ta`, mêmes vraies bougies OHLC.
    # Seuls les patterns les plus récents sont gardés (le LLM raisonne sur l'état
    # courant, pas tout l'historique).
    ta_candle_patterns: list[CandlePattern] = field(default_factory=list)
    # Barrières de sécurité structurées (peuplées au scan si la donnée on-chain
    # existe ; None sinon). Exposent en clair ce que le score agrège, pour un
    # filtre binaire strict (cf. skills/safety_screen.py). Concentration calculée
    # HORS pool LP et adresses de burn (sinon tout token échoue à tort).
    contract_verified: bool | None = None
    has_mint: bool | None = None
    has_blacklist: bool | None = None
    has_disable_transfers: bool | None = None
    top_holder_pct: float | None = None
    top10_holder_pct: float | None = None
    holders_counted: int | None = None
    # Fondamentaux CoinGecko (peuplés seulement si include_fundamentals ET donnée
    # disponible). Exposés en clair pour nourrir la projection ROI par comparables
    # (Voûte 3, skills/roi_comparables.py) sans re-fetch. None → section omise.
    market_cap_usd: float | None = None
    fully_diluted_valuation_usd: float | None = None
    categories: list[str] = field(default_factory=list)
    # Autorité du contrat (résolue seulement si has_mint : un mint externe existe).
    # Distingue un mint contrôlé par un dev (danger) d'un mint légitime (renoncé,
    # launchpad connu, ou piloté par un contrat). Voir skills/mint_authority.py.
    mint_authority: str | None = None  # na/renounced/launchpad/contract/eoa/unknown
    mint_authority_detail: str = ""
    launchpad: str | None = None  # label du launchpad si le déployeur est reconnu
    # Profondeur de liquidité (liquidité / market cap). Peuplé si les deux sont connus
    # (donc chemin analyse VC avec fondamentaux). Marché mince = fragile. Au cas par cas.
    liq_mcap_ratio: float | None = None
    # Comportement du wallet du dev (peuplé si include_dev_behavior). Signal pondéré
    # (aligned/neutral/concern/unknown) + observations factuelles. Nourrit le jugement,
    # ne rejette pas d'office.
    dev_signal: str | None = None
    dev_points: list[str] = field(default_factory=list)
    # Sécurité dynamique GoPlus (peuplé si include_honeypot ET donnée dispo). Ce que
    # l'ABI statique de Blockscout ne voit pas : la revente est-elle RÉELLEMENT possible,
    # taxes réelles d'achat/vente, pouvoirs cachés. None = non scanné ou indisponible →
    # comportement strictement inchangé (additif, data-gated).
    is_honeypot: bool | None = None
    cannot_sell: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    hidden_owner: bool | None = None
    can_take_back_ownership: bool | None = None
    # Niche Virtuals bonding (peuplé UNIQUEMENT si aucune paire DexScreener n'existe ET
    # que le contrat est réellement indexé par Virtuals en statut pré-graduation — voir
    # services/virtuals.py). Absence de paire DEX est NORMALE à ce stade (la liquidité
    # DEX n'existe qu'après graduation) : sans ce champ, `_score_and_verdict` traitait ça
    # comme un défaut de sécurité générique et pouvait produire un AVOID mal fondé.
    bonding_phase: bool = False
    bonding_progress: float | None = None  # 0.0-1.0, part du seuil de graduation atteint
    bonding_holder_count: int | None = None
    bonding_mcap_virtual: float | None = None  # dénominé en VIRTUAL, pas converti en USD
    # Diligence produit Virtuals (audit 11/07, cf. skills/vc_analysis.py). Peuplé DÈS
    # qu'un token est trouvé sur Virtuals via `_resolve_bonding_phase` (bonding ou non --
    # zéro coût réseau supplémentaire, même appel que ci-dessus) ; pour un token DÉJÀ
    # gradué (une paire DEX existe, donc `_resolve_bonding_phase` n'est jamais appelée),
    # `vc_analysis._fetch_virtuals_product_diligence` fait un repli best-effort via le
    # même client singleton. Texte DÉCLARATIF (l'équipe parle d'elle-même sur sa propre
    # fiche virtuals.io) -- jamais vérifié on-chain, même doctrine que website_snapshot.
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
    """Délègue à ``services.dexscreener`` (14/07, #157) -- ce client existait en
    dur ici ; extrait pour être réutilisable (wallet-scoring, triangulation avec
    GeckoTerminal) sans dupliquer un second appel DexScreener. Comportement du
    scan `/vc` strictement inchangé (même parsing, même dataclass)."""
    return await _dexscreener_fetch_token_pairs(contract, chain=_dex_chain_id())


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
            # Exclut le pool LP ET les adresses de burn : elles détiennent
            # légitimement de grosses parts (une supply brûlée n'est pas une whale).
            # Cohérent avec _holder_concentration (sinon un token déflationniste est
            # pénalisé/rejeté à tort).
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
        if ctx.bonding_phase:
            # Token Virtuals encore en courbe de bonding : l'absence de paire DEX est
            # NORMALE à ce stade (la liquidité DEX n'existe qu'après graduation), pas un
            # défaut de sécurité générique. Score sur les signaux natifs disponibles.
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


# Seuil informatif de taxe honeypot (GoPlus) : au-delà, la taxe est signalée comme
# extractive dans les risk_flags. La barrière DURE (rejet du pool) vit dans safety_screen.
_HONEYPOT_TAX_FLAG = 0.10  # 10 %


def _apply_honeypot_signals(ctx: "TokenScanContext", sec) -> None:
    """Absorbe la lecture GoPlus dans le contexte — additif, jamais bloquant ici.

    Peuple les champs de décision + risk_flags et ajuste le score sur les seuls signaux
    POSITIVEMENT confirmés. Une indisponibilité (None / available=False) ne dégrade rien :
    elle est signalée comme absence de donnée, pas comme risque (doctrine : une panne
    réseau ne bannit pas un bon token).
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

    if delta:
        ctx.security_score = max(5, min(95, ctx.security_score + delta))
    # Un honeypot / revente impossible confirmé = danger sans ambiguïté : on aligne le
    # verdict lisible pour que l'analyse ET le filtre soient cohérents.
    if sec.is_honeypot is True or sec.cannot_sell_all is True:
        ctx.lite_verdict = "DANGER"


async def _resolve_bonding_phase(ctx: "TokenScanContext", contract: str) -> None:
    """Best-effort, appelé UNIQUEMENT quand aucune paire DexScreener n'a été trouvée : un
    contrat sans pool peut légitimement être un token Virtuals encore en courbe de bonding
    (pas de liquidité DEX avant graduation — normal, pas un défaut). Toute panne Virtuals
    laisse `ctx.bonding_phase = False` (comportement inchangé, verdict générique "aucune
    paire") — jamais bloquant, jamais de donnée inventée.

    Capture AUSSI (audit 11/07) `ctx.virtuals_description`/`_tokenomics`/
    `_additional_details` dès qu'un token Virtuals est trouvé -- bonding ou non --
    puisque le même appel réseau a déjà ramené ce payload : zéro coût supplémentaire
    pour nourrir la diligence produit (`vc_analysis._fetch_product_diligence`).

    Repli on-chain (audit 11/07, gate OFF par défaut `ARIA_ONCHAIN_GRADUATION_ENABLED`) :
    quand l'heuristique API (`graduation_progress`) renvoie `None`, tente une lecture
    on-chain réelle (`services/base_onchain.py`) -- couverture PARTIELLE et honnête (une
    seule instance connue du contrat Bonding V5), jamais bloquant (thread séparé via
    `asyncio.to_thread`, mêmes conventions que `mailer.py`/`x_twitter.py` pour les appels
    synchrones), jamais de valeur inventée si la lecture échoue ou ne couvre pas ce token."""
    try:
        from aria_core.services.base_onchain import onchain_graduation_enabled, onchain_graduation_progress
        from aria_core.services.virtuals import graduation_progress, is_in_bonding, virtuals_client

        token = await virtuals_client.fetch_by_address(contract)
        if token is None:
            return
        ctx.virtuals_description = token.description
        ctx.virtuals_tokenomics = token.tokenomics
        ctx.virtuals_additional_details = token.additional_details
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
    except Exception:  # noqa: BLE001 — best-effort, ne casse jamais le scan
        pass


async def scan_base_token(
    contract: str,
    *,
    include_smart_money: bool = False,
    include_fundamentals: bool = False,
    include_ta: bool = False,
    include_dev_behavior: bool = False,
    include_honeypot: bool = False,
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
    Cable le 10/07 (decision operateur) : EMA/MACD (ctx.ta_ema_*/ctx.ta_macd_*)
    et le setup golden pocket + divergence RSI (ctx.ta_golden_pocket_signal),
    memes champs facts-only, meme garde `include_ta`.
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
    else:
        await _resolve_bonding_phase(ctx, ca)
    _score_and_verdict(ctx, ctx.best_pair, contract_flags=contract_flags, holders=holders)

    # Expose en clair les barrières de sécurité (le filtre binaire les lit).
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

    # Autorité du mint : uniquement si un mint EXTERNE existe (rare depuis le fix ABI).
    # Un mint légitime (renoncé, launchpad connu, contrat) ne doit pas faire rejeter un
    # bon token ; seul un mint contrôlé par un wallet de dev est un danger. Best-effort :
    # toute indisponibilité -> 'unknown' (prudent en aval), jamais bloquant.
    if ctx.has_mint is True:
        await _resolve_mint_authority(ctx, ca)

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
            # Profondeur de liquidité : marché mince par rapport à la valorisation ?
            # Neutralisé sur une courbe de bonding (liquidité exponentielle, mince au
            # départ -> le ratio n'est pas un signal de fragilité).
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

    # Comportement du wallet du dev : builder engagé vs farmer (jugement contextuel,
    # jamais un rejet d'office). Best-effort ; toute indisponibilité -> 'unknown'.
    if include_dev_behavior:
        await _resolve_dev_behavior(ctx, ca)

    # Sécurité dynamique (honeypot / taxes réelles / pouvoirs cachés) via GoPlus. Désactivé
    # par défaut (un appel réseau de plus) ; activé sur le chemin d'analyse VC où une vraie
    # décision se prend. Additif : sans donnée, ctx inchangé.
    if include_honeypot:
        from aria_core.services.goplus import goplus_client

        sec = await goplus_client.get_token_security(ca)
        _apply_honeypot_signals(ctx, sec)

    return ctx


async def _resolve_dev_behavior(ctx: "TokenScanContext", token_address: str) -> None:
    """Récolte + juge le comportement du wallet du déployeur. Défensif, jamais bloquant."""
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
    except Exception as exc:  # noqa: BLE001 — le comportement dev est un bonus, jamais bloquant
        logger.info("dev_behavior: analyse %s échouée (%s)", token_address, exc)
        ctx.dev_signal = "unknown"
        ctx.dev_points = []


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