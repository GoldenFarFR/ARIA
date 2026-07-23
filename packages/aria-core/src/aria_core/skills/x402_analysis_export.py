"""Source-scrubbed export of ARIA's own analysis, for the x402-seller product
(#39, operator decision 23/07: "branche l'analyse complète de aria qui ne
mentionne pas ses sources").

ARIA's thesis text names the upstream data providers inline ("honeypot clear
(GoPlus)", "Recherche web Tavily tentée...", "holders Blockscout"...). When the
SYNTHESIZED verdict is sold to a third party, those provider names are replaced
with GENERIC capability descriptions -- what ARIA sells is her own composite
JUDGMENT, not a pass-through of any single provider's branded output.

This module is PURE (no network, no DB, no real capital -- just a text
transform) and is the safe, self-contained CONTENT half of the seller product.
The payment plumbing (x402 resource server, the receiving wallet, testnet
verification) is a separate, deliberately-gated piece -- see
docs/x402-seller-scoping.md. Nothing here moves or receives money.

Honest caveat (documented, not hidden): scrubbing the source NAMES reduces how
recognizable the upstream is, but does NOT by itself resolve the provider-ToS
question (e.g. GoPlus's terms about commercial use of derived data) -- that is a
separate operator/legal decision tracked in the scoping doc, not something this
formatter settles."""
from __future__ import annotations

import re

# Provider name -> generic capability description. Longest/most-specific names
# first so a multi-word or dotted name is matched before a substring of it.
# Only ARIA's OWN synthesized verdict is sold; these replacements keep the
# thesis readable while removing every branded/attributable source name.
_SOURCE_REPLACEMENTS: list[tuple[str, str]] = [
    ("twitterapi.io", "social data"),
    ("twitterapi", "social data"),
    ("twit.sh", "social data"),
    ("coinmarketcap", "market-cap data"),
    ("coingecko", "market-cap data"),
    ("geckoterminal", "market data"),
    ("dexscreener", "market data"),
    ("cabalspy", "wallet intelligence"),
    ("blockscout", "on-chain contract data"),
    ("rugcheck", "second-opinion security scan"),
    ("cybercentry", "second-opinion security scan"),
    ("webacy", "second-opinion security scan"),
    ("goplus", "on-chain security scan"),
    ("polymarket", "prediction-market data"),
    ("farcaster", "social graph"),
    ("birdeye", "market data"),
    ("mobula", "market data"),
    ("alchemy", "on-chain data"),
    ("moralis", "on-chain data"),
    ("tavily", "web research"),
    ("otto ai", "market-alert data"),
    ("otto", "market-alert data"),
    ("dune", "on-chain analytics"),
    ("nansen", "wallet intelligence"),
    ("arkham", "wallet intelligence"),
    ("clanker", "launchpad data"),
    ("virtuals", "launchpad data"),
]

# Precompiled, case-insensitive, word-boundary-ish patterns. ``re.escape`` on the
# name; a leading "(" (as in "(GoPlus)") and surrounding whitespace are handled
# by the parenthetical pass below, this pass matches the bare name.
_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", re.IGNORECASE), repl)
    for name, repl in _SOURCE_REPLACEMENTS
]


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Collapse an immediately-repeated word (case-insensitive), e.g. "web web
# research" -> "web research" -- an artifact a replacement can create when a
# provider name mapped to a phrase starting with a word already present ("web
# Tavily" -> "web web research"). A genuinely doubled word in analysis text is
# itself almost always an artifact, so this cleanup is safe.
_DUP_WORD_RE = re.compile(r"\b(\w+)(?:\s+\1\b)+", re.IGNORECASE)


def scrub_sources(text: str | None) -> str:
    """Replaces every known upstream provider name in ``text`` with a generic
    capability description, AND removes raw artifacts that would leak the
    upstream even without a name: http(s) URLs (a specific source link, e.g. the
    exact page a web-research step landed on) are replaced with ``[lien
    masqué]``. Case-insensitive, boundary-aware (never rewrites a provider name
    embedded inside a larger word). ``None``/empty -> ``""``."""
    if not text:
        return ""
    out = text
    # Strip source URLs FIRST (before name replacement) -- a URL is a raw
    # artifact that reveals the upstream page, never part of ARIA's own verdict.
    out = _URL_RE.sub("[lien masqué]", out)
    for pattern, repl in _COMPILED:
        out = pattern.sub(repl, out)
    # Collapse a repeated word a replacement may have created ("web web research").
    out = _DUP_WORD_RE.sub(r"\1", out)
    # Clean any parenthetical that ended up empty/whitespace-only after a
    # replacement chain (defensive).
    out = re.sub(r"\(\s*\)", "", out)
    # Collapse doubled spaces a replacement may have introduced.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def build_sellable_analysis(analysis: dict) -> dict:
    """Turns ARIA's internal analysis dict (a ``/vc`` VCResult-shaped or a
    momentum ``sig``-shaped mapping) into a source-scrubbed payload safe to
    return to a paying x402 customer.

    Keeps ONLY the synthesized judgment fields (verdict/action, thesis,
    reasons, entry/target/invalidation levels, a synthesized confidence) --
    NEVER the raw per-provider fields (e.g. ``entry_security_json``, the raw
    GoPlus dict, raw holder lists) which would be a data pass-through. Every
    text field is run through ``scrub_sources``. Missing fields are simply
    omitted (never a fabricated value)."""
    out: dict = {}

    verdict = analysis.get("action") or analysis.get("verdict") or analysis.get("recommandation")
    if verdict:
        out["verdict"] = str(verdict)

    symbol = analysis.get("symbol") or analysis.get("symbole")
    if symbol:
        out["symbol"] = str(symbol)

    thesis = analysis.get("these") or analysis.get("thesis")
    if thesis:
        out["thesis"] = scrub_sources(str(thesis))

    reasons = analysis.get("reasons") or analysis.get("raisons")
    if isinstance(reasons, list) and reasons:
        out["reasons"] = [scrub_sources(str(r)) for r in reasons if r]

    for src_key, out_key in (
        ("price", "entry"), ("entry", "entry"),
        ("target", "target"), ("invalidation", "invalidation"),
        ("rr", "risk_reward"),
    ):
        val = analysis.get(src_key)
        if val is not None and out_key not in out:
            out[out_key] = val

    # A synthesized confidence label if present, scrubbed -- never a raw score
    # from a single provider.
    confidence = analysis.get("confiance") or analysis.get("confidence")
    if confidence:
        out["confidence"] = scrub_sources(str(confidence))

    out["disclaimer"] = (
        "Analyse synthétisée par ARIA à partir de multiples sources on-chain, "
        "sociales et de marché. Jugement propriétaire, jamais un conseil en "
        "investissement. Aucune donnée fournisseur brute redistribuée."
    )
    return out
