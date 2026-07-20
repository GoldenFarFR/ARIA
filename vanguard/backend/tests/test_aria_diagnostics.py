import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_pool_status_requires_diagnostic_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")
    async with await _client() as client:
        no_header = await client.get("/api/aria/diagnostics/pool-status")
        wrong_header = await client.get(
            "/api/aria/diagnostics/pool-status", headers={"X-Diagnostic-Access": "wrong"}
        )
    assert no_header.status_code == 403
    assert wrong_header.status_code == 403


@pytest.mark.asyncio
async def test_pool_status_returns_counts_and_closest_candidates(tmp_path, monkeypatch):
    from aria_core import screened_pool

    monkeypatch.setattr(screened_pool, "DB_PATH", str(tmp_path / "pool.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    await screened_pool.upsert_screened(
        contract="0xactive", symbol="ACT", liquidity_usd=100_000.0,
        security_score=90, verdict="SAFE",
    )
    await screened_pool.record_pending(
        contract="0xclose", reason="holders inconnus",
        liquidity_usd=28_000.0, security_score=68, verdict="CAUTION",
    )
    await screened_pool.record_rejected(contract="0xrej", reason="honeypot")

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/pool-status", headers={"X-Diagnostic-Access": "diagsecret"}
        )

    assert res.status_code == 200
    body = res.json()
    assert body["base"]["counts"] == {"active": 1, "pending": 1, "rejected": 1}
    assert body["base"]["closest_to_passing"][0]["contract"] == "0xclose"
    assert body["base-bonding"]["counts"] == {"active": 0, "pending": 0, "rejected": 0}


@pytest.mark.asyncio
async def test_agent_wallet_ledger_requires_diagnostic_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")
    async with await _client() as client:
        res = await client.get("/api/aria/diagnostics/agent-wallet-ledger")
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_agent_wallet_ledger_empty_seam_until_a_pilot_is_wired(tmp_path, monkeypatch):
    """Seam : reste vide tant qu'aucun produit agent-wallet n'est choisi/câblé."""
    from aria_core import agent_wallet_log

    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "wallet.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/agent-wallet-ledger",
            headers={"X-Diagnostic-Access": "diagsecret"},
        )
    assert res.status_code == 200
    assert res.json() == {"transactions": []}


@pytest.mark.asyncio
async def test_agent_wallet_ledger_returns_recorded_transactions(tmp_path, monkeypatch):
    from aria_core import agent_wallet_log

    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "wallet.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    await agent_wallet_log.record_transaction(
        wallet_product="metamask_agent_wallet", action_type="swap",
        status="ok", tx_hash="0xdeadbeef",
    )

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/agent-wallet-ledger",
            headers={"X-Diagnostic-Access": "diagsecret"},
        )
    assert res.status_code == 200
    txs = res.json()["transactions"]
    assert len(txs) == 1
    assert txs[0]["tx_hash"] == "0xdeadbeef"


@pytest.mark.asyncio
async def test_paper_ledger_requires_diagnostic_token(monkeypatch):
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")
    async with await _client() as client:
        no_header = await client.get("/api/aria/diagnostics/paper-ledger")
        wrong_header = await client.get(
            "/api/aria/diagnostics/paper-ledger", headers={"X-Diagnostic-Access": "wrong"}
        )
    assert no_header.status_code == 403
    assert wrong_header.status_code == 403


@pytest.mark.asyncio
async def test_paper_ledger_returns_open_and_closed_positions_with_entry_exit_plan(
    tmp_path, monkeypatch
):
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    await paper_trader.open_position(
        "0xopen", "OPEN", 1.0,
        target_price=2.0, invalidation_price=0.8, chain="base", thesis="momentum + R/R 2.4",
        entry_atr_pct=0.09,
    )
    await paper_trader.open_position(
        "0xclosed", "CLOSED", 1.0,
        target_price=1.5, invalidation_price=0.9, chain="base", thesis="golden pocket",
    )
    await paper_trader.close_position(
        "0xclosed", 1.5, reason="cible atteinte", notes="Cible atteinte : +50% vs entrée.",
    )

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/paper-ledger", headers={"X-Diagnostic-Access": "diagsecret"}
        )

    assert res.status_code == 200
    body = res.json()
    assert body["starting_capital"] == paper_trader.STARTING_CAPITAL_USD
    assert len(body["open_positions"]) == 1
    assert body["open_positions"][0]["contract"] == "0xopen"
    assert body["open_positions"][0]["thesis"] == "momentum + R/R 2.4"
    assert body["open_positions"][0]["invalidation_price"] == 0.8
    # 19/07 -- revue croisée Gemini : ATR persisté, exposé par cet endpoint diagnostic.
    assert body["open_positions"][0]["entry_atr_pct"] == pytest.approx(0.09)
    assert len(body["closed_positions"]) == 1
    assert body["closed_positions"][0]["contract"] == "0xclosed"
    assert body["closed_positions"][0]["close_reason"] == "cible atteinte"
    assert body["closed_positions"][0]["thesis"] == "golden pocket"
    # 19/07 -- régression : ces deux champs manquaient totalement de la sérialisation,
    # rendant la vraie justification de sortie invisible pour tout appelant de cet
    # endpoint (watchdog inclus -- fausse alerte "close_notes vide" alors que la ligne
    # DB était bien remplie).
    assert body["closed_positions"][0]["close_notes"] == "Cible atteinte : +50% vs entrée."
    assert body["closed_positions"][0]["realized_pnl_partial"] == 0.0


@pytest.mark.asyncio
async def test_paper_ledger_empty_seam_before_any_trade(tmp_path, monkeypatch):
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper_empty.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/paper-ledger", headers={"X-Diagnostic-Access": "diagsecret"}
        )
    assert res.status_code == 200
    body = res.json()
    assert body["open_positions"] == []
    assert body["closed_positions"] == []


@pytest.mark.asyncio
async def test_paper_ledger_marks_itself_as_simulated(tmp_path, monkeypatch):
    """20/07 -- extraction directe de la thèse écrite par ARIA elle-même
    (aria-brain, chapitre 1) : elle craint de confondre un résultat simulé avec un
    résultat réel parce que les deux se ressemblent dans un log/payload. Ce payload
    ne portait aucun marqueur DANS la donnée elle-même (seulement l'URL + un header
    opaque) -- corrigé, verrouillé ici."""
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper_marker.db"))
    monkeypatch.setenv("ARIA_DIAGNOSTIC_TOKEN", "diagsecret")

    async with await _client() as client:
        res = await client.get(
            "/api/aria/diagnostics/paper-ledger", headers={"X-Diagnostic-Access": "diagsecret"}
        )
    body = res.json()
    assert body["simulated"] is True
    assert "fictif" in body["disclaimer"].lower()
