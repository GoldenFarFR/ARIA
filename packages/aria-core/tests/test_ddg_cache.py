import time

import pytest

from aria_core.knowledge.ddg_cache import (
    cache_stats,
    clear_cache,
    get_cached,
    normalize_query,
    set_cached,
)
from aria_core.knowledge.web_verify import WebSource
from aria_core.testing import configure_test_runtime


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    clear_cache()
    yield
    clear_cache()


def test_normalize_query():
    assert normalize_query("  Top 14   AUJOURD'HUI ") == "top 14 aujourd'hui"


def test_cache_disabled_by_default(test_settings):
    test_settings.aria_ddg_search_cache = False
    set_cached("rugby today", [WebSource(text="Stade Toulousain 21h05", url="https://lnr.fr")])
    assert get_cached("rugby today") is None


def test_cache_roundtrip(test_settings):
    test_settings.aria_ddg_search_cache = True
    src = WebSource(text="Demi-finale Top 14 vendredi 21h05", url="https://lnr.fr")
    set_cached("stade toulousain horaire", [src])
    hit = get_cached("stade toulousain horaire")
    assert hit and len(hit) == 1
    assert "21h05" in hit[0].text
    assert hit[0].url == "https://lnr.fr"


def test_cache_ttl_expired(test_settings, monkeypatch):
    test_settings.aria_ddg_search_cache = True
    set_cached("bitcoin price", [WebSource(text="BTC trades near support levels today", url="")])
    store_path = cache_stats()["path"]
    import json
    from pathlib import Path

    data = json.loads(Path(store_path).read_text(encoding="utf-8"))
    key = next(iter(data))
    data[key]["cached_at"] = time.time() - 99999
    Path(store_path).write_text(json.dumps(data), encoding="utf-8")
    assert get_cached("bitcoin price") is None