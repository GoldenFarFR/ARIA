"""Liens officiels du projet (site, X, Telegram…) extraits de DexScreener.

Sourcés depuis `info.websites` / `info.socials` du payload DexScreener — jamais
générés par le LLM. Vérifie l'extraction, la normalisation des libellés, et
surtout le rejet de tout schéma non http(s) (défense contre une URL hostile
type `javascript:` glissée dans un payload de token malveillant).
"""
from __future__ import annotations

from aria_core.services.dexscreener import _extract_project_links, _parse_pair


def test_extract_project_links_websites_and_socials():
    raw = {
        "info": {
            "websites": [{"label": "Website", "url": "https://atlas.example"}],
            "socials": [
                {"type": "twitter", "url": "https://x.com/atlas"},
                {"type": "telegram", "url": "https://t.me/atlas"},
            ],
        }
    }
    links = _extract_project_links(raw)
    assert {"label": "Website", "url": "https://atlas.example"} in links
    assert {"label": "X (Twitter)", "url": "https://x.com/atlas"} in links
    assert {"label": "Telegram", "url": "https://t.me/atlas"} in links


def test_extract_project_links_no_info_is_empty():
    assert _extract_project_links({}) == []
    assert _extract_project_links({"info": None}) == []


def test_extract_project_links_rejects_non_http_scheme():
    """Une URL hostile (javascript:, data:) ne doit jamais survivre à l'extraction."""
    raw = {
        "info": {
            "websites": [{"label": "Website", "url": "javascript:alert(1)"}],
            "socials": [{"type": "twitter", "url": "data:text/html,<script>alert(1)</script>"}],
        }
    }
    assert _extract_project_links(raw) == []


def test_extract_project_links_unknown_social_type_capitalized():
    raw = {"info": {"socials": [{"type": "farcaster", "url": "https://warpcast.com/atlas"}]}}
    links = _extract_project_links(raw)
    assert links == [{"label": "Farcaster", "url": "https://warpcast.com/atlas"}]


def test_extract_project_links_missing_url_skipped():
    raw = {"info": {"websites": [{"label": "Website"}], "socials": [{"type": "twitter"}]}}
    assert _extract_project_links(raw) == []


def test_parse_pair_populates_project_links():
    raw = {
        "pairAddress": "0xpair",
        "info": {"websites": [{"label": "Docs", "url": "https://docs.atlas.example"}]},
    }
    pair = _parse_pair(raw)
    assert pair.project_links == [{"label": "Docs", "url": "https://docs.atlas.example"}]
