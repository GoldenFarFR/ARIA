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


def _pick_dominant_match(ticker: str, pairs: list[PairSnapshot]) -> PairSnapshot | None:
    exact = [p for p in pairs if p.base_symbol.upper() == ticker.upper() and p.base_address]
    if not exact:
        return None
    exact.sort(key=lambda p: p.liquidity_usd, reverse=True)
    if len(exact) == 1:
        return exact[0]
    top, runner_up = exact[0], exact[1]
    if runner_up.liquidity_usd <= 0 or top.liquidity_usd >= runner_up.liquidity_usd * _DOMINANT_LIQUIDITY_RATIO:
        return top
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
