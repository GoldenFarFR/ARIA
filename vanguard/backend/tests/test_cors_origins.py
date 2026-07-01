import json

from app.config import Settings


def test_cors_origins_comma_separated_env(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://ariavanguardzhc.com,https://www.ariavanguardzhc.com,https://test-1-nwf2.onrender.com",
    )
    s = Settings()
    assert s.cors_origins == [
        "https://ariavanguardzhc.com",
        "https://www.ariavanguardzhc.com",
        "https://test-1-nwf2.onrender.com",
    ]


def test_cors_origins_json_array_env(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        json.dumps(["https://example.com", "https://api.example.com"]),
    )
    s = Settings()
    assert s.cors_origins == ["https://example.com", "https://api.example.com"]