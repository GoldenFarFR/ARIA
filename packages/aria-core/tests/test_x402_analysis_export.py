"""Source-scrubbed export of ARIA's analysis for the x402-seller product (#39,
23/07). Pure text transform -- no network, no DB, no real capital."""
from __future__ import annotations

import pytest

from aria_core.skills.x402_analysis_export import build_sellable_analysis, scrub_sources

# Every upstream provider name that appears in ARIA's real thesis/reason text.
_PROVIDERS = [
    "GoPlus", "Blockscout", "DexScreener", "GeckoTerminal", "CoinGecko",
    "CoinMarketCap", "Tavily", "twit.sh", "TwitterAPI.io", "CabalSpy", "Dune",
    "Alchemy", "Moralis", "RugCheck", "Birdeye", "Mobula", "Farcaster",
    "Polymarket", "Webacy", "Cybercentry", "Nansen", "Arkham", "Clanker",
    "Virtuals",
]


def test_empty_and_none():
    assert scrub_sources(None) == ""
    assert scrub_sources("") == ""


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_every_known_provider_name_is_removed(provider):
    text = f"analyse : {provider} confirme le signal"
    scrubbed = scrub_sources(text)
    assert provider.lower() not in scrubbed.lower(), f"{provider} still present in: {scrubbed!r}"


def test_case_insensitive():
    assert "goplus" not in scrub_sources("honeypot clear (GOPLUS)").lower()
    assert "goplus" not in scrub_sources("honeypot clear (goplus)").lower()


def test_parenthetical_form_scrubbed():
    out = scrub_sources("honeypot clear (GoPlus)")
    assert "GoPlus" not in out
    assert "on-chain security scan" in out


def test_never_rewrites_a_provider_name_inside_a_larger_word():
    # a made-up word that merely CONTAINS a provider substring must be left alone
    out = scrub_sources("le mot DuneBuggy et Duneland ne sont pas la source Dune")
    assert "DuneBuggy" in out
    assert "Duneland" in out
    # the standalone "Dune" (the source) IS scrubbed
    assert out.count("Dune") == 2  # only the two inside larger words remain


def test_dotted_name_twit_sh_scrubbed():
    out = scrub_sources("lecture X via twit.sh")
    assert "twit.sh" not in out
    assert "social data" in out


def test_non_source_text_is_untouched():
    text = "R/R franc 3.9, divergence RSI haussière, golden pocket 0.618-0.786"
    assert scrub_sources(text) == text


def test_urls_are_masked():
    """A raw source URL leaks the upstream even without a provider name -- it
    must be replaced, never sold verbatim."""
    out = scrub_sources("site officiel : https://coinfactory.app/en/blog/how-to-x suivant")
    assert "coinfactory.app" not in out
    assert "http" not in out
    assert "[lien masqué]" in out


def test_repeated_word_from_replacement_is_collapsed():
    # "web Tavily" -> "web web research" -> collapsed to "web research"
    out = scrub_sources("Recherche web Tavily tentée")
    assert "web web" not in out
    assert "web research tentée" in out


# ── build_sellable_analysis ──────────────────────────────────────────────────


def test_build_sellable_keeps_only_synthesized_fields_scrubbed():
    sig = {
        "action": "BUY", "symbol": "TOK",
        "these": "honeypot clear (GoPlus); volume DexScreener 50k$",
        "reasons": ["holders Blockscout OK", "R/R franc 3.9"],
        "price": 1.5, "target": 3.0, "invalidation": 1.0, "rr": 3.0,
        # raw provider pass-through fields that must NEVER be resold:
        "entry_security_json": '{"goplus_raw": "..."}',
        "liquidity_usd": 100000.0,
    }
    out = build_sellable_analysis(sig)
    assert out["verdict"] == "BUY"
    assert out["symbol"] == "TOK"
    # every text field scrubbed
    for provider in ("GoPlus", "DexScreener", "Blockscout"):
        assert provider not in out["thesis"]
        assert all(provider not in r for r in out["reasons"])
    # raw provider pass-through fields are NOT in the sellable payload
    assert "entry_security_json" not in out
    assert "liquidity_usd" not in out
    # synthesized levels kept
    assert out["entry"] == 1.5 and out["target"] == 3.0 and out["invalidation"] == 1.0
    assert out["risk_reward"] == 3.0
    assert "disclaimer" in out


def test_build_sellable_omits_missing_fields_never_fabricates():
    out = build_sellable_analysis({"action": "AVOID"})
    assert out["verdict"] == "AVOID"
    assert "thesis" not in out
    assert "entry" not in out
    assert "reasons" not in out
    assert "disclaimer" in out  # always present


def test_build_sellable_accepts_french_keys():
    out = build_sellable_analysis({
        "verdict": "WATCH", "symbole": "ABC",
        "thesis": "signal via Tavily", "raisons": ["listing CoinGecko"],
    })
    assert out["verdict"] == "WATCH"
    assert out["symbol"] == "ABC"
    assert "Tavily" not in out["thesis"]
    assert all("CoinGecko" not in r for r in out["reasons"])


def test_disclaimer_states_not_investment_advice_and_no_raw_resale():
    out = build_sellable_analysis({"action": "BUY"})
    d = out["disclaimer"].lower()
    assert "conseil en investissement" in d
    assert "brute" in d  # "aucune donnée fournisseur brute redistribuée"
