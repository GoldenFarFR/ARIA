"""x402 SELLER service layer (#39, 23/07) -- gating, receiving wallet, pricing
catalog, ResourceConfig assembly, scrubbed delivery. No real money moves here;
the verify/settle wiring against a live facilitator is validated on testnet
separately (operator self-payment test)."""
from __future__ import annotations

import pytest

from aria_core import x402_seller as s


# ── gating (defense in depth: both OFF by default) ──────────────────────────


def test_seller_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_X402_SELLER_ENABLED", raising=False)
    assert s.seller_enabled() is False


def test_seller_mainnet_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_X402_SELLER_MAINNET", raising=False)
    assert s.seller_mainnet_enabled() is False


def test_network_defaults_to_testnet(monkeypatch):
    monkeypatch.delenv("ARIA_X402_SELLER_MAINNET", raising=False)
    assert s.resolve_network() == "base-sepolia"


def test_network_mainnet_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_X402_SELLER_MAINNET", "true")
    assert s.resolve_network() == "base"


def test_seller_enabled_alone_still_defaults_to_testnet(monkeypatch):
    """The two gates are independent: enabling the seller must NOT by itself
    take real mainnet money."""
    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    monkeypatch.delenv("ARIA_X402_SELLER_MAINNET", raising=False)
    assert s.seller_enabled() is True
    assert s.resolve_network() == "base-sepolia"


# ── pricing catalog ──────────────────────────────────────────────────────────


def test_price_for_known_products():
    assert s.price_for("wallet_score") == "$0.02"
    assert s.price_for("token_analysis_cached") == "$0.10"
    assert s.price_for("token_analysis_fresh") == "$0.50"


def test_price_for_unknown_product_is_none():
    assert s.price_for("nonexistent") is None


def test_fresh_scan_priced_above_cached(monkeypatch):
    """COGS ordering sanity: a fresh scan (real network cost) must cost more
    than serving from cache."""
    def _usd(p):
        return float(p.lstrip("$"))
    assert _usd(s.PRICING_CATALOG["token_analysis_fresh"]) > _usd(s.PRICING_CATALOG["token_analysis_cached"])
    assert _usd(s.PRICING_CATALOG["token_analysis_cached"]) > _usd(s.PRICING_CATALOG["wallet_score"])


# ── build_resource_config ────────────────────────────────────────────────────


def test_build_resource_config_none_when_seller_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_X402_SELLER_ENABLED", raising=False)
    assert s.build_resource_config("wallet_score") is None


def test_build_resource_config_none_for_unknown_product(monkeypatch):
    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    assert s.build_resource_config("nonexistent") is None


def test_build_resource_config_testnet_by_default(monkeypatch):
    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    monkeypatch.delenv("ARIA_X402_SELLER_MAINNET", raising=False)
    rc = s.build_resource_config("wallet_score")
    assert rc is not None
    assert rc.pay_to == s.ARIA_X402_RECEIVING_ADDRESS
    assert rc.price == "$0.02"
    assert rc.network == "base-sepolia"
    assert rc.scheme == "exact"


def test_build_resource_config_mainnet_when_both_gates_on(monkeypatch):
    monkeypatch.setenv("ARIA_X402_SELLER_ENABLED", "true")
    monkeypatch.setenv("ARIA_X402_SELLER_MAINNET", "true")
    rc = s.build_resource_config("token_analysis_fresh")
    assert rc.network == "base"
    assert rc.price == "$0.50"
    assert rc.pay_to == s.ARIA_X402_RECEIVING_ADDRESS


# ── deliver_scrubbed ─────────────────────────────────────────────────────────


def test_deliver_scrubbed_removes_sources_and_tags_product():
    analysis = {
        "action": "BUY", "symbol": "TOK",
        "these": "honeypot clear (GoPlus); volume DexScreener 50k$",
        "reasons": ["holders Blockscout OK"],
        "price": 1.0, "target": 2.0, "invalidation": 0.5, "rr": 2.0,
        "entry_security_json": '{"raw": "..."}',
    }
    out = s.deliver_scrubbed("token_analysis_fresh", analysis)
    assert out["product"] == "token_analysis_fresh"
    assert out["verdict"] == "BUY"
    for provider in ("GoPlus", "DexScreener", "Blockscout"):
        assert provider not in out["thesis"]
    assert "entry_security_json" not in out  # raw pass-through never resold
    assert "disclaimer" in out
