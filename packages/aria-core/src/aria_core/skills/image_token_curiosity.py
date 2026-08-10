"""Verified curiosity on a token mentioned in a Telegram image (26/07).

Real trigger: the operator sent a screenshot of an X post about a token
($Stonkbroker), commented "elle pourrait etre curieuse et verifier le
graphique" -- until now ``_handle_vision_photo`` (gateway/telegram_bot.py)
answered from the image ALONE (a pure visual read; ``vision_rule`` in
``brain.py::_llm_response`` already warns it is never an on-chain-verified
reading). This module adds a REAL verification step before the final reply:
extract a candidate ticker from the image (one narrow LLM call), search it on
DexScreener (``services/dexscreener.search_pairs``, multi-chain, free), and if
a clear match is found, run the same honeypot check the momentum pipeline
already trusts (GoPlus, ``_DEXSCREENER_TO_GOPLUS_CHAIN_ID`` reused from
``momentum_entry.py`` -- never a second mapping).

Degrades honestly at every step (no ticker readable, no search result,
several plausible candidates with no dominant liquidity): returns an empty
string, never a guessed contract. The caller (``_handle_vision_photo``) then
falls back to the pre-existing pure-visual reply, unchanged.

Deliberately NOT a hard gate: purely informational context handed to the LLM
via ``extra_system_context`` (same seam as the VC-followup/ledger context in
``brain.py``) -- the LLM stays free to say it won't buy off a screenshot.
This never triggers or blocks a real momentum/VC trade decision.
"""
from __future__ import annotations

import logging
import re

from aria_core.services.dexscreener import PairSnapshot, search_pairs

logger = logging.getLogger(__name__)

_NO_TICKER_MARKERS = ("AUCUN", "NONE", "N/A", "AUCUNE")
_TICKER_RE = re.compile(r"TICKER\s*:\s*([A-Za-z0-9_$.]{1,20})", re.IGNORECASE)

# A single exact-symbol match "dominates" when it holds at least this multiple
# of the runner-up's liquidity -- otherwise the ticker is too ambiguous
# (several real, unrelated tokens share the same symbol) to single one out
# without risking exactly the "guessed contract" this module must avoid.
_DOMINANT_LIQUIDITY_RATIO = 3.0


async def _extract_ticker(image_data_uri: str) -> str | None:
    """One narrow, cheap LLM vision call -- structured output only, kept
    fully separate from ``_llm_response`` (the actual conversational reply)."""
    from aria_core.llm import chat_with_context
    from aria_core.llm_economy import LlmDepth, anthropic_depth_override

    provider, model = anthropic_depth_override(LlmDepth.BRIEF)
    raw = await chat_with_context(
        "Regarde cette image. Un ticker ou nom de token crypto (ex. $STONK, "
        "PEPE, un contrat 0x...) y est-il mentionne ou visible (post, "
        "graphique, tableau) ?",
        "Reponds STRICTEMENT sous une de ces deux formes, rien avant, rien "
        "apres :\nTICKER: <symbole exact, sans le $>\nAUCUN",
        None,
        temperature=0.0,
        max_tokens=20,
        image_data_uri=image_data_uri,
        provider=provider,
        model=model,
    )
    if not raw:
        return None
    raw = raw.strip()
    if raw.upper() in _NO_TICKER_MARKERS:
        return None
    m = _TICKER_RE.search(raw)
    if not m:
        return None
    ticker = m.group(1).strip().lstrip("$")
    return ticker or None


def _pick_dominant_match(
    ticker: str, pairs: list[PairSnapshot], *, chains: tuple[str, ...] | None = None,
) -> PairSnapshot | None:
    """``chains`` (Item #236 follow-up, 30/07, real incident: GITLAWB/KellyClaude,
    both real liquid Base tokens the operator pointed at, were misreported as
    "ambiguous" because a same-ticker pair on "robinhood"/"ethereum" -- a chain
    this pipeline never trades, verified via DexScreener's own search API --
    happened to sit within 3x liquidity of the real Base match, tripping the
    dominance check below against a candidate that could never be bought
    anyway. When given, restricts comparison to these chains BEFORE the
    dominance check, so an out-of-scope chain can no longer poison the
    decision for the one chain that matters. ``None`` (default, used by
    ``verify_token_mention``'s pure informational curiosity check) keeps the
    original unscoped behavior -- that path isn't gated by what this pipeline
    can actually trade."""
    exact = [p for p in pairs if p.base_symbol.upper() == ticker.upper() and p.base_address]
    if chains is not None:
        allowed = {c.lower() for c in chains}
        exact = [p for p in exact if (p.chain_id or "").lower() in allowed]
    if not exact:
        return None

    # Item #238, 30/07, real incident: BRETT (a single real Base contract,
    # 19 pools across Uniswap/Aerodrome/Pancakeswap/etc.) was misreported as
    # "ambiguous" because comparing raw POOL liquidity treated its own 2nd
    # pool ($598K, Aerodrome) as if it were a DIFFERENT competing token vs
    # its main pool ($903K, Uniswap) -- ratio 1.51x, under the 3x threshold,
    # tripping the guard against itself. Group by base_address FIRST and
    # compare AGGREGATE (summed) liquidity across DISTINCT addresses --
    # never a single token's own pools mistaken for two competing tokens.
    by_address: dict[str, list[PairSnapshot]] = {}
    for p in exact:
        by_address.setdefault(p.base_address, []).append(p)
    ranked = sorted(by_address.values(), key=lambda ps: sum(p.liquidity_usd for p in ps), reverse=True)
    if len(ranked) == 1:
        return max(ranked[0], key=lambda p: p.liquidity_usd)
    top_liq = sum(p.liquidity_usd for p in ranked[0])
    runner_up_liq = sum(p.liquidity_usd for p in ranked[1])
    if runner_up_liq <= 0 or top_liq >= runner_up_liq * _DOMINANT_LIQUIDITY_RATIO:
        return max(ranked[0], key=lambda p: p.liquidity_usd)
    return None  # too ambiguous -- several unrelated tokens share this ticker


async def _honeypot_line(match: PairSnapshot) -> str | None:
    """Best-effort GoPlus scan -- reuses the momentum pipeline's chain mapping
    (never a second one). Degrades to ``None`` (line simply omitted) if the
    chain isn't covered or GoPlus has nothing -- never a fabricated verdict."""
    from aria_core.momentum_entry import _DEXSCREENER_TO_GOPLUS_CHAIN_ID

    goplus_chain = _DEXSCREENER_TO_GOPLUS_CHAIN_ID.get(match.chain_id)
    if not goplus_chain:
        return None
    try:
        from aria_core.services.goplus import goplus_client

        security = await goplus_client.get_token_security(match.base_address, chain_id=goplus_chain)
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the reply
        logger.info("image_token_curiosity: goplus scan failed (%s)", exc)
        return None
    if not security.available:
        return None
    if security.is_honeypot:
        return "- ⚠️ GoPlus : HONEYPOT CONFIRME (revente bloquee) -- danger reel, pas une simple prudence."
    tax_bits = []
    if security.buy_tax is not None:
        tax_bits.append(f"achat {security.buy_tax * 100:.1f}%")
    if security.sell_tax is not None:
        tax_bits.append(f"vente {security.sell_tax * 100:.1f}%")
    tax_txt = f", taxes {' / '.join(tax_bits)}" if tax_bits else ""
    return f"- GoPlus : pas de honeypot detecte{tax_txt}."


async def verify_token_mention(image_data_uri: str) -> str:
    """Returns a French verified-facts block for ``extra_system_context``, or
    ``""`` if nothing could be honestly verified. Never raises -- a failure at
    any step (LLM, DexScreener, GoPlus) degrades to the empty string."""
    try:
        ticker = await _extract_ticker(image_data_uri)
    except Exception as exc:  # noqa: BLE001 -- never blocks the vision reply
        logger.info("image_token_curiosity: ticker extraction failed (%s)", exc)
        return ""
    if not ticker:
        return ""

    try:
        pairs = await search_pairs(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.info("image_token_curiosity: dexscreener search failed (%s)", exc)
        return ""

    match = _pick_dominant_match(ticker, pairs)
    if match is None:
        if any(p.base_symbol.upper() == ticker.upper() for p in pairs):
            return (
                f"# Verification image (curiosite) : ticker '{ticker}' repere dans l'image\n"
                "Plusieurs tokens DISTINCTS partagent ce meme ticker sur DexScreener -- "
                "aucun ne domine clairement en liquidite, impossible d'identifier celui "
                "de l'image sans risquer de te tromper de contrat. Dis-le explicitement, "
                "ne devine jamais lequel c'est."
            )
        return (
            f"# Verification image (curiosite) : ticker '{ticker}' repere dans l'image\n"
            "Aucun resultat DexScreener pour ce ticker -- token introuvable ou trop "
            "recent/illiquide pour etre indexe. Dis-le honnetement, n'invente aucune donnee."
        )

    lines = [
        f"# Verification image (curiosite) : ticker '{ticker}' repere dans l'image, contrat identifie on-chain",
        f"- Contrat : {match.base_address} (chaine : {match.chain_id or 'inconnue'}, DEX : {match.dex_id or 'inconnu'})",
        f"- Prix : ${match.price_usd:.6g} -- Liquidite : ${match.liquidity_usd:,.0f} -- Volume 24h : ${match.volume_24h_usd:,.0f}",
        f"- Variation 24h : {match.price_change_24h:+.1f}%",
    ]

    goplus_line = await _honeypot_line(match)
    if goplus_line:
        lines.append(goplus_line)

    lines.append(
        "Cette verification est une lecture on-chain REELLE (pas une simple lecture "
        "visuelle) -- utilise-la pour repondre avec de vrais faits, mais precise que "
        "ce n'est pas une analyse /vc complete (pas de scan de securite complet, pas "
        "de diligence produit)."
    )
    return "\n".join(lines)


# Item #236 follow-up (30/07, operator request: "aria ajoute les tokens avec
# le screenshot que je lui envoie") -- a real screener page (DexScreener
# trending, etc.) shows DOZENS of tokens at once, unlike the single-mention
# case above (an X post about ONE token). Sane cap against a runaway/
# hallucinated list -- a real screener page rarely shows more than this many
# rows above the fold anyway.
_MAX_TICKERS_PER_IMAGE = 30


async def _extract_all_tickers(image_data_uri: str) -> list[str]:
    """Multi-ticker variant of ``_extract_ticker`` -- used ONLY by the
    explicit-caption queue path (``queue_tokens_from_screenshot``), never by
    the default single-ticker curiosity check above. Deduplicated,
    order-preserving, capped at ``_MAX_TICKERS_PER_IMAGE``."""
    from aria_core.llm import chat_with_context
    from aria_core.llm_economy import LlmDepth, anthropic_depth_override

    provider, model = anthropic_depth_override(LlmDepth.DEVELOP)
    raw = await chat_with_context(
        "Regarde cette image (probablement un tableau ou un ecran de tri de "
        "tokens crypto, ex. un screener DexScreener). Liste TOUS les tickers "
        "ou symboles de tokens visibles.",
        "Reponds STRICTEMENT avec un ticker par ligne, format 'TICKER: <symbole>', "
        "rien avant, rien apres. Si aucun ticker n'est visible, reponds 'AUCUN'.",
        None,
        temperature=0.0,
        max_tokens=400,
        image_data_uri=image_data_uri,
        provider=provider,
        model=model,
    )
    if not raw:
        return []
    raw = raw.strip()
    if raw.upper() in _NO_TICKER_MARKERS:
        return []
    tickers: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        m = _TICKER_RE.search(line)
        if not m:
            continue
        ticker = m.group(1).strip().lstrip("$").upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
        if len(tickers) >= _MAX_TICKERS_PER_IMAGE:
            break
    return tickers


async def queue_tokens_from_screenshot(image_data_uri: str) -> str:
    """Explicit-caption-only flow (``_caption_requests_manual_add`` in
    ``gateway/telegram_bot.py`` gates this -- an image sent without that
    signal never reaches here). Extracts every ticker visible, resolves each
    to a real contract via DexScreener (same ``_pick_dominant_match`` dominant-
    liquidity logic as the single-ticker path above -- never guesses between
    several unrelated tokens sharing a ticker), and queues every resolved one
    into ``manual_candidates`` -- the SAME downstream pool and hard gates
    (honeypot/liquidity/volume/wash-trading/holder concentration/R-R) as
    ``/add`` or an auto-discovered candidate, never a buy shortcut. Never
    silent: always reports counts, including tickers that could not be
    resolved, rather than a bare confirmation."""
    try:
        tickers = await _extract_all_tickers(image_data_uri)
    except Exception as exc:  # noqa: BLE001 -- never raises to the caller
        logger.info("image_token_curiosity: multi-ticker extraction failed (%s)", exc)
        return "Je n'ai pas reussi a lire les tickers de cette image, reessaie."
    if not tickers:
        return "Aucun ticker lisible dans cette image."

    from aria_core.manual_candidates import add_manual_candidate
    from aria_core.momentum_entry import DEFAULT_CHAINS

    queued: list[str] = []
    unresolved: list[str] = []
    for ticker in tickers:
        try:
            pairs = await search_pairs(ticker)
        except Exception as exc:  # noqa: BLE001
            logger.info("image_token_curiosity: dexscreener search failed for %s (%s)", ticker, exc)
            unresolved.append(ticker)
            continue
        match = _pick_dominant_match(ticker, pairs, chains=DEFAULT_CHAINS)
        if match is None or not match.base_address:
            unresolved.append(ticker)
            continue
        chain = match.chain_id or "base"
        await add_manual_candidate(
            match.base_address, chain,
            liquidity_usd=match.liquidity_usd, volume_24h_usd=match.volume_24h_usd,
        )
        queued.append(f"{ticker} ({chain})")

    lines = [f"📸 {len(tickers)} ticker(s) detecte(s) dans l'image."]
    if queued:
        lines.append(f"✅ {len(queued)} ajoute(s) a la file de decouverte : " + ", ".join(queued))
    if unresolved:
        lines.append(
            f"⚠️ {len(unresolved)} non resolu(s) (ambigu ou introuvable sur DexScreener) : "
            + ", ".join(unresolved)
        )
    lines.append(
        "Passeront par les memes garde-fous (honeypot/liquidite/volume/wash-trading/R-R) "
        "qu'un candidat trouve automatiquement -- jamais un achat force."
    )
    return "\n".join(lines)
