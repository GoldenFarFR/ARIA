import pytest

from aria_core.memory.vector.health import vector_health_report
from aria_core.testing import configure_test_runtime


@pytest.mark.asyncio
async def test_vector_health_flag_off(tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    report = await vector_health_report()
    assert report["flag_enabled"] is False
    assert report["ready"] is False
    assert "aria_vector_memory" in report["reason"]


@pytest.mark.asyncio
async def test_vector_health_flag_on_no_chroma(tmp_path, monkeypatch):
    from aria_core.runtime import settings

    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(settings, "aria_vector_memory", True)
    report = await vector_health_report()
    assert report["flag_enabled"] is True
    if not report["chromadb_installed"]:
        assert report["ready"] is False
        assert "chromadb" in report["reason"]