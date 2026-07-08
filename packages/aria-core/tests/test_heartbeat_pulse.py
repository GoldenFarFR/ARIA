"""Pouls heartbeat (endpoint public /api/pulse) — coarse, sûr, aucun candidat/secret exposé."""
from __future__ import annotations

from aria_core import heartbeat


def test_pulse_alive_and_whitelisted_cycles_only(monkeypatch):
    monkeypatch.setattr(heartbeat, "_load_heartbeat_state", lambda: {
        "vc_crawl": "2026-07-08T12:00:00Z",
        "vc_radar_x": "2026-07-08T11:00:00Z",
        "showcase_pr_watch": "2026-07-08T13:00:00Z",   # hors whitelist -> pas exposé
    })
    p = heartbeat.heartbeat_pulse()
    assert set(p.keys()) == {"alive", "last_tick", "cycles"}
    assert p["alive"] is True
    assert p["last_tick"] == "2026-07-08T13:00:00Z"      # tick le plus récent, tous cycles confondus
    assert "vc_crawl" in p["cycles"] and "vc_radar_x" in p["cycles"]
    # seuls les cycles whitelistés remontent (pas de fuite d'activité arbitraire)
    assert "showcase_pr_watch" not in p["cycles"]


def test_pulse_empty_state(monkeypatch):
    monkeypatch.setattr(heartbeat, "_load_heartbeat_state", lambda: {})
    p = heartbeat.heartbeat_pulse()
    assert p["alive"] is False and p["last_tick"] is None and p["cycles"] == {}
