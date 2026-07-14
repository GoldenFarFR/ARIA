"""Lecture profonde d'une page web réelle (13/07) — aucun réseau réel, httpx et la
résolution DNS sont monkeypatchés. Couvre : extraction HTML->texte, garde SSRF
(IP privées/loopback/lien-local refusées), Content-Type non-HTML, 403/timeout
jamais fabriqués, troncature du budget de texte."""
from __future__ import annotations

import socket

import pytest

from aria_core.services import page_reader as pr


class _FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


class _FakeAsyncClient:
    _response = None
    _raise: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if type(self)._raise:
            raise type(self)._raise
        return type(self)._response


@pytest.fixture
def _fresh_client(monkeypatch):
    _FakeAsyncClient._response = None
    _FakeAsyncClient._raise = None
    monkeypatch.setattr(pr.httpx, "AsyncClient", _FakeAsyncClient)
    # Garde SSRF neutralisée par défaut (aucune IP -- privée ou publique -- n'a sa
    # place en clair dans ce fichier, cf. test_coherence.py::test_no_public_ip_in_
    # source_or_tests) -- les tests dédiés à la garde SSRF la testent séparément, en
    # patchant socket.getaddrinfo avec une IP privée (autorisée par ce garde-fou).
    async def _no_guard(_hostname):
        return "127.0.0.1", None

    monkeypatch.setattr(pr, "_resolve_and_guard", _no_guard)
    return _FakeAsyncClient


# ── extraction HTML -> texte ────────────────────────────────────────────────────────

def test_extract_page_text_strips_script_style_and_tags():
    html = (
        "<html><head><title>My Page</title>"
        '<meta name="description" content="A real description"></head>'
        "<body><script>evil()</script><style>.x{}</style><noscript>fallback</noscript>"
        "<p>Real visible content here.</p></body></html>"
    )
    title, text = pr._extract_page_text(html)
    assert title == "My Page"
    assert "A real description" in text
    assert "Real visible content here." in text
    assert "evil()" not in text
    assert "fallback" not in text
    assert "<p>" not in text


def test_extract_page_text_truncates_to_budget():
    html = f"<html><body>{'x' * 20000}</body></html>"
    _, text = pr._extract_page_text(html)
    assert len(text) <= pr._MAX_PAGE_TEXT_CHARS


# ── garde SSRF ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "::1", "0.0.0.0"],
)
def test_blocked_ip_ranges(ip):
    assert pr._is_blocked_ip(ip) is True


def test_address_with_no_blocked_property_is_not_blocked(monkeypatch):
    # Teste la logique OR de _is_blocked_ip sans IP publique en clair (interdit par
    # test_coherence.py::test_no_public_ip_in_source_or_tests -- et de toute façon
    # tout littéral IPv4 qu'il autorise est privé/réservé, donc bloqué par
    # construction : impossible d'écrire un exemple "public, non bloqué" en dur).
    class _FakeAddr:
        is_private = False
        is_loopback = False
        is_link_local = False
        is_reserved = False
        is_multicast = False
        is_unspecified = False

    monkeypatch.setattr(pr.ipaddress, "ip_address", lambda _s: _FakeAddr())
    assert pr._is_blocked_ip("irrelevant-with-fake-parser") is False


def test_unparseable_ip_is_blocked_by_default():
    assert pr._is_blocked_ip("not-an-ip") is True


@pytest.mark.asyncio
async def test_fetch_page_text_refuses_private_ip_target(monkeypatch):
    # Pas de fixture _fresh_client ici (elle neutralise _resolve_and_guard) -- on
    # veut exercer la vraie résolution + garde, jusqu'au blocage avant tout appel
    # HTTP. 127.0.0.1 est autorisé en clair par test_coherence.py (boucle locale).
    monkeypatch.setattr(
        pr.socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))]
    )
    result = await pr.fetch_page_text("http://internal.local/admin")
    assert result.available is False
    assert "interne" in result.error or "privée" in result.error


@pytest.mark.asyncio
async def test_fetch_page_text_dns_failure_returns_unavailable(monkeypatch):
    def _raise_gaierror(host, port):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(pr.socket, "getaddrinfo", _raise_gaierror)
    result = await pr.fetch_page_text("http://nonexistent.invalid/")
    assert result.available is False


@pytest.mark.asyncio
async def test_dns_rebinding_toctou_only_one_resolution_and_pinned_ip_used(monkeypatch):
    """Régression du correctif TOCTOU/DNS rebinding (relecture pré-merge, 13/07).

    AVANT ce correctif : _resolve_and_guard validait un premier lookup DNS, puis
    httpx refaisait sa PROPRE résolution indépendante au moment de la connexion
    réelle -- un attaquant contrôlant le DNS du domaine cible pouvait renvoyer
    une IP publique légitime au 1er lookup (celui vérifié) puis une IP privée au
    2e (celui réellement utilisé pour se connecter), contournant la garde SSRF
    entièrement.

    Ce test simule exactement ce scénario : le 1er appel à getaddrinfo renvoie
    une adresse "sûre" ; TOUT appel suivant lèverait une AssertionError (preuve
    qu'aucune deuxième résolution n'a lieu). Il vérifie aussi que la connexion
    RÉELLE (au niveau du network backend, sous httpx) cible bien exactement
    l'adresse du 1er lookup -- jamais une adresse re-résolue."""
    calls = {"n": 0}

    def fake_getaddrinfo(host, port):
        calls["n"] += 1
        if calls["n"] == 1:
            return [(None, None, None, None, ("safe-first-lookup", 0))]
        raise AssertionError(
            "une seconde résolution DNS indépendante a eu lieu -- TOCTOU réintroduit"
        )

    monkeypatch.setattr(pr.socket, "getaddrinfo", fake_getaddrinfo)

    # "safe-first-lookup" n'est pas une vraie IP -- ipaddress.ip_address mocké
    # pour ce test précis afin de la faire passer comme "non bloquée" (même
    # patron que test_address_with_no_blocked_property_is_not_blocked : aucune
    # IP publique en clair n'est permise dans ce fichier, cf. test_coherence.py).
    class _FakeAddr:
        is_private = False
        is_loopback = False
        is_link_local = False
        is_reserved = False
        is_multicast = False
        is_unspecified = False

    monkeypatch.setattr(pr.ipaddress, "ip_address", lambda _s: _FakeAddr())

    connect_attempts: list[str] = []

    class _FakeRealBackend:
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            connect_attempts.append(host)
            # Interrompt AVANT tout vrai réseau -- l'attaque de connexion a été
            # enregistrée, c'est tout ce que ce test doit prouver.
            raise RuntimeError("stopped before real network (test)")

        async def connect_unix_socket(self, *a, **k):  # pragma: nocover
            raise NotImplementedError

        async def sleep(self, seconds):  # pragma: nocover
            pass

    monkeypatch.setattr(pr.httpcore, "AnyIOBackend", lambda: _FakeRealBackend())

    result = await pr.fetch_page_text("https://attacker-controlled.example/")

    assert calls["n"] == 1, "getaddrinfo appelé plus d'une fois -- résolution DNS indépendante réapparue"
    assert connect_attempts == ["safe-first-lookup"], (
        "la connexion réelle doit cibler EXACTEMENT l'adresse du 1er lookup déjà "
        "vérifié, jamais une adresse issue d'une résolution ultérieure"
    )
    assert result.available is False  # connexion volontairement interrompue par ce test, pas un vrai succès


# ── fetch_page_text bout-en-bout ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_page_text_invalid_url_no_network_call():
    for bad in ("", "ftp://example.com", "not-a-url", None):
        result = await pr.fetch_page_text(bad)
        assert result.available is False


@pytest.mark.asyncio
async def test_fetch_page_text_success(_fresh_client):
    _fresh_client._response = _FakeResponse(
        200, "<html><head><title>Luma</title></head><body>Real product description.</body></html>"
    )
    result = await pr.fetch_page_text("https://withluma.app")
    assert result.available is True
    assert result.title == "Luma"
    assert "Real product description." in result.text


@pytest.mark.asyncio
async def test_fetch_page_text_403_is_honest_not_fabricated(_fresh_client):
    _fresh_client._response = _FakeResponse(403, "blocked")
    result = await pr.fetch_page_text("https://blocked.example")
    assert result.available is False
    assert "403" in result.error or "anti-bot" in result.error
    assert result.text == ""


@pytest.mark.asyncio
async def test_fetch_page_text_non_html_returns_unavailable(_fresh_client):
    _fresh_client._response = _FakeResponse(200, '{"ok": true}', headers={"content-type": "application/json"})
    result = await pr.fetch_page_text("https://api.example.com")
    assert result.available is False


@pytest.mark.asyncio
async def test_fetch_page_text_network_error_never_raises(_fresh_client):
    _fresh_client._raise = RuntimeError("connection reset")
    result = await pr.fetch_page_text("https://flaky.example")
    assert result.available is False


@pytest.mark.asyncio
async def test_fetch_page_text_empty_after_cleaning_returns_unavailable(_fresh_client):
    _fresh_client._response = _FakeResponse(200, "<html><body><script>only script, no text</script></body></html>")
    result = await pr.fetch_page_text("https://empty.example")
    assert result.available is False
