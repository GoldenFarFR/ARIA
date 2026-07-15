"""Pipeline momentum multi-chaînes pour le test paper-trading 1M$ (#194, 15/07).

Remplace le filtre VC-thesis (``safety_screen``/``screened_pool``, réservé à la poche
85% « builders précoces », NON touché ici) par un critère technique/momentum pour CE
TEST SPÉCIFIQUEMENT : la vitrine trending DexScreener montrée par l'opérateur (des
dizaines de tokens réels, liquides, déjà en mouvement) n'a pas besoin d'un filtre
pensé pour repérer un builder caché — c'est un pari technique différent.

Doctrine de ce module (gravée dans CLAUDE.md, section « Pivot critère d'entrée pour
le test 1M$ (#194) », à lire avant toute modification) :
  - **Seul garde-fou dur** : honeypot GoPlus. Rejet immédiat, sans exception.
  - **R/R positif obligatoire** (cible/invalidation dérivés de niveaux RÉELS via
    ``entry_signals.detect_entry`` -- golden pocket + divergence RSI) : sans lui,
    HOLD. Jamais un objectif fabriqué quand l'OHLCV est indisponible.
  - **Alignement technique** (EMA/MACD/Bollinger/patterns de bougies) : signaux
    SUPPLÉMENTAIRES qui renforcent la confiance, jamais des portes bloquantes
    individuelles -- exiger l'accord simultané de tous rendrait le pipeline aussi
    restrictif que ce qu'on remplace (contradiction avec « pipeline permissif »).
  - **Buzz (bonus, jamais bloquant)** : présence dans les boosts/profils DexScreener
    récents -- pas de branchement sur ``radar_x``/``market_sentiment`` (ce sont des
    systèmes asynchrones à état, pas des fonctions de requête par contrat ; un futur
    chantier pourrait les intégrer, hors scope ici).
  - **Vitesse** : scan déterministe (honeypot + TA + R/R) en premier, LLM réservé à
    la confirmation d'un signal AMBIGU (R/R positif mais faible, ou alignement
    technique partagé) -- jamais un ``/vc`` complet par candidat.
  - **Multi-chaînes limité aux chaînes VÉRIFIÉES ce soir** (``DEFAULT_CHAINS``) :
    accepter n'importe quelle chaîne renvoyée par DexScreener casserait le seul
    garde-fou dur sur toute chaîne que GoPlus ne couvre pas -- jamais une entrée sans
    honeypot check actif. Étendre la liste seulement après vérification GoPlus réelle
    (même doctrine que ce soir, curl direct avant d'accepter).
  - **Bonding (Virtuals pré-graduation) : hors scope**, différé par décision
    opérateur explicite -- ce module ne touche que les tokens standards.
"""
from __future__ import annotations

import logging

from aria_core.services.dexscreener import (
    PairSnapshot,
    fetch_token_pairs,
    token_boosts_latest,
    token_boosts_top,
    token_profiles_latest,
)
from aria_core.skills.candlestick_patterns import detect_patterns
from aria_core.skills.entry_signals import detect_entry
from aria_core.skills.indicators import bollinger_bands, ema_series, macd_series
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

# Chaînes vérifiées en direct ce soir (15/07) : GoPlus honeypot check confirmé
# fonctionnel (curl réel, HTTP 200) ET DexScreener couvre nativement (chainId
# accepté tel quel). Base = priorité #1 (tout existe déjà, OHLCV confirmé via
# GeckoTerminal). Solana = couverture GoPlus/DexScreener confirmée, OHLCV
# best-effort (GeckoTerminal ne liste pas "solana" dans GECKO_NETWORK_SLUGS --
# tenté quand même, dégradation honnête si indisponible). Robinhood = best-effort
# total (chain récente, couverture OHLCV incertaine).
DEFAULT_CHAINS: tuple[str, ...] = ("base", "solana", "robinhood")

# DexScreener utilise des slugs lisibles ("base", "solana", "robinhood") ; GoPlus
# attend son propre identifiant de chaîne (numérique pour la plupart des EVM, ou
# un mot-clé spécial pour Solana) -- vérifié en direct ce soir pour ces 3 chaînes.
_DEXSCREENER_TO_GOPLUS_CHAIN_ID: dict[str, str] = {
    "base": "8453",
    "solana": "solana",
    "robinhood": "4663",
}

_SOURCE_LIMIT_PER_CHANNEL = 30
_MIN_LIQUIDITY_USD = 5_000.0  # plancher bas -- pipeline permissif, pas un filtre VC
_RR_MIN_FOR_DIRECT_BUY = 1.5  # R/R franc -> décision déterministe sans appel LLM
_RR_AMBIGUOUS_FLOOR = 1.0     # sous ce seuil, R/R positif mais faible -> LLM tranche


async def discover_momentum_candidates(
    *, chains: tuple[str, ...] = DEFAULT_CHAINS, limit_per_chain: int = _SOURCE_LIMIT_PER_CHANNEL,
) -> list[dict]:
    """Sourcing multi-chaînes large (#194) -- privilégie la FRAÎCHEUR (nouveaux
    pools/boosts/profils récents) plutôt qu'un mouvement déjà bien avancé.
    Dédoublonné par (contract, chain). Jamais de filtre de sécurité ici -- c'est le
    rôle de ``evaluate_momentum_entry`` (honeypot + TA), pas du sourcing."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []

    def _add(contract: str, chain: str) -> None:
        contract = (contract or "").strip().lower()
        chain = (chain or "").strip().lower()
        if not contract or not chain or chain not in chains:
            return
        key = (contract, chain)
        if key in seen:
            return
        seen.add(key)
        out.append({"contract": contract, "chain": chain})

    if "base" in chains:
        try:
            from aria_core.base_crawler import discover_base_tokens

            base_contracts = await discover_base_tokens(limit=limit_per_chain)
        except Exception as exc:  # noqa: BLE001 — une source qui échoue n'arrête pas le sourcing
            logger.info("discover_momentum_candidates: base_crawler échoué (%s)", exc)
            base_contracts = []
        for addr in base_contracts:
            _add(addr, "base")

    # Fraîcheur d'abord (profils/boosts récents), classement "top" en dernier --
    # cohérent avec la préférence opérateur pour des signaux qui COMMENCENT à se
    # former plutôt qu'un mouvement déjà bien vu de tous.
    for fetch in (token_profiles_latest, token_boosts_latest, token_boosts_top):
        try:
            listings = await fetch()
        except Exception as exc:  # noqa: BLE001
            logger.info("discover_momentum_candidates: %s échoué (%s)", fetch.__name__, exc)
            listings = []
        for listing in listings[:limit_per_chain]:
            _add(listing.token_address, listing.chain_id)

    return out


def _best_pair(pairs: list[PairSnapshot]) -> PairSnapshot | None:
    liquid = [p for p in pairs if p.liquidity_usd >= _MIN_LIQUIDITY_USD]
    pool = liquid or pairs
    if not pool:
        return None
    return max(pool, key=lambda p: p.liquidity_usd)


async def _check_honeypot(contract: str, chain: str) -> tuple[bool, str]:
    """Seul garde-fou DUR de ce pipeline. ``(clear, reason)`` -- ``clear=False``
    doit TOUJOURS rejeter, y compris si GoPlus est indisponible (fail-closed sur
    LE garde-fou, contrairement au reste du pipeline qui dégrade en douceur)."""
    goplus_chain = _DEXSCREENER_TO_GOPLUS_CHAIN_ID.get(chain)
    if not goplus_chain:
        return False, f"chaîne {chain} non couverte par le garde-fou honeypot -- rejet par prudence"

    from aria_core.services.goplus import goplus_client

    security = await goplus_client.get_token_security(contract, chain_id=goplus_chain)
    if not security.available:
        return False, f"GoPlus indisponible ({security.error}) -- rejet par prudence, jamais un pari sans garde-fou"
    if security.is_honeypot:
        return False, "honeypot confirmé (GoPlus)"
    if security.cannot_sell_all:
        return False, "revente totale bloquée (GoPlus)"
    return True, "honeypot clear (GoPlus)"


async def _fetch_candles(pool_address: str, chain: str) -> list[Candle]:
    """OHLCV best-effort -- Base confirmé (GeckoTerminal), autres chaînes tentées
    telles quelles (jamais une donnée inventée si indisponible, cf. doctrine module)."""
    from aria_core.services.geckoterminal import geckoterminal_client

    try:
        result = await geckoterminal_client.get_ohlcv(pool_address, network=chain)
    except Exception as exc:  # noqa: BLE001
        logger.info("_fetch_candles: GeckoTerminal %s/%s échoué (%s)", chain, pool_address[:10], exc)
        return []
    if not result.available:
        return []
    return result.candles


def _technical_alignment(candles: list[Candle]) -> tuple[int, list[str]]:
    """Score d'alignement technique (0-3) : EMA rapide>lente, MACD>signal, pattern
    de bougie bullish sur la dernière bougie. Signaux SUPPLÉMENTAIRES (jamais des
    portes individuelles) -- ``None`` (période de chauffe) ne compte ni pour ni
    contre, jamais traité comme baissier par défaut."""
    closes = [c.close for c in candles]
    reasons: list[str] = []
    score = 0

    ema_fast = ema_series(closes, 12)
    ema_slow = ema_series(closes, 26)
    if ema_fast and ema_slow and ema_fast[-1] is not None and ema_slow[-1] is not None:
        if ema_fast[-1] > ema_slow[-1]:
            score += 1
            reasons.append("EMA12 > EMA26 (tendance courte au-dessus de la longue)")

    macd_line, macd_signal, _hist = macd_series(closes)
    if macd_line and macd_signal and macd_line[-1] is not None and macd_signal[-1] is not None:
        if macd_line[-1] > macd_signal[-1]:
            score += 1
            reasons.append("MACD au-dessus de sa ligne de signal")

    patterns = detect_patterns(candles[-3:]) if len(candles) >= 3 else []
    if any(p.direction == "bullish" for p in patterns):
        score += 1
        names = ", ".join(p.name for p in patterns if p.direction == "bullish")
        reasons.append(f"pattern de bougie bullish récent ({names})")

    _mid, upper, _lower = bollinger_bands(closes)
    if upper and upper[-1] is not None and closes[-1] >= upper[-1]:
        reasons.append("prix déjà au-dessus de la bande de Bollinger haute (extension avancée)")

    return score, reasons


async def _llm_confirm(contract: str, symbol: str, chain: str, rr: float, reasons: list[str]) -> bool:
    """Confirmation LÉGÈRE (pas un `/vc` complet) réservée aux signaux AMBIGUS
    (R/R positif mais faible). Indisponible/erreur -> HOLD par défaut, jamais un
    BUY inventé faute de réponse."""
    from aria_core.llm import chat_with_context

    system = (
        "Tu juges UNIQUEMENT si un signal technique momentum déjà positif mérite d'être "
        "confirmé pour un test papier diagnostique (pas de capital réel). Réponds par un "
        "seul mot : BUY ou HOLD."
    )
    user = (
        f"Token {symbol or contract[:10]} ({chain}), R/R {rr:.1f} (faible mais positif). "
        f"Signaux : {'; '.join(reasons) or 'aucun signal technique additionnel'}. "
        "BUY ou HOLD ?"
    )
    try:
        reply = await chat_with_context(user, system, max_tokens=10)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant, dégrade vers HOLD
        logger.info("_llm_confirm: appel LLM échoué (%s)", exc)
        return False
    if not reply:
        return False
    return "BUY" in reply.strip().upper()[:20]


async def evaluate_momentum_entry(contract: str, chain: str) -> dict | None:
    """Décision d'entrée momentum (#194) pour ``contract`` sur ``chain``.

    Ordre, du plus rapide/bloquant au plus lent/optionnel :
      1. Honeypot (GoPlus) -- rejet immédiat si non clear.
      2. Prix + meilleure paire (DexScreener) -- rejet si aucune paire liquide.
      3. R/R (golden pocket + divergence RSI, ``entry_signals.detect_entry``) --
         HOLD si absent (jamais un objectif fabriqué).
      4. Alignement technique (bonus, jamais bloquant) -- renforce la confiance.
      5. R/R franc (>= 1.5) + au moins 1 signal technique -> BUY déterministe.
         R/R faible (1.0-1.5) -> confirmation LLM légère. Sinon HOLD.
    Retourne un dict compatible avec ``paper_trader.run_paper_cycle``'s ``analyzer``
    (``action``/``symbol``/``price``/``target``/``invalidation``/``chain``), ou
    ``None`` si aucune donnée de prix exploitable (jamais un signal fabriqué)."""
    contract = (contract or "").strip().lower()
    chain = (chain or "").strip().lower()

    clear, honeypot_reason = await _check_honeypot(contract, chain)
    if not clear:
        return {"action": "HOLD", "chain": chain, "reasons": [honeypot_reason]}

    pairs = await fetch_token_pairs(contract, chain=chain)
    best = _best_pair(pairs)
    if best is None or not best.price_usd or best.price_usd <= 0:
        return None

    reasons: list[str] = [honeypot_reason]
    candles = await _fetch_candles(best.pair_address, chain)
    if not candles:
        reasons.append("OHLCV indisponible sur cette chaîne -- R/R non calculable, pas d'entrée")
        return {
            "action": "HOLD", "chain": chain, "symbol": best.base_symbol,
            "price": best.price_usd, "reasons": reasons,
        }

    signal = detect_entry(candles)
    reasons.extend(signal.reasons)
    if not signal.present or signal.rr is None or signal.rr <= 0:
        reasons.append("pas de setup golden pocket + divergence RSI avec R/R positif")
        return {
            "action": "HOLD", "chain": chain, "symbol": best.base_symbol,
            "price": best.price_usd, "reasons": reasons,
        }

    align_score, align_reasons = _technical_alignment(candles)
    reasons.extend(align_reasons)

    action = "HOLD"
    if signal.rr >= _RR_MIN_FOR_DIRECT_BUY and align_score >= 1:
        action = "BUY"
        reasons.append(f"R/R franc ({signal.rr:.1f}) + alignement technique -- décision directe")
    elif signal.rr >= _RR_AMBIGUOUS_FLOOR:
        confirmed = await _llm_confirm(contract, best.base_symbol, chain, signal.rr, reasons)
        if confirmed:
            action = "BUY"
            reasons.append(f"R/R faible ({signal.rr:.1f}) mais confirmé par le LLM")
        else:
            reasons.append(f"R/R faible ({signal.rr:.1f}), non confirmé -- HOLD")
    else:
        reasons.append(f"R/R positif mais sous le seuil ambigu ({signal.rr:.1f} < {_RR_AMBIGUOUS_FLOOR})")

    return {
        "action": action,
        "chain": chain,
        "symbol": best.base_symbol,
        "price": best.price_usd,
        "target": signal.target,
        "invalidation": signal.invalidation,
        "rr": signal.rr,
        "reasons": reasons,
    }
