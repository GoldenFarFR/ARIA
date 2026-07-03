import json
from pathlib import Path

import pytest

from aria_core.skills import acp_cli, acp_provider_skill
from aria_core.skills import acp_offering_skill, acp_product_launch_skill, acp_schema
from aria_core.skills.acp_client_skill import (
    execute_acp_marketplace,
    wants_acp_marketplace,
)


def test_wants_acp_marketplace():
    assert wants_acp_marketplace("acp status")
    assert wants_acp_marketplace("concernant acp quel plan")
    assert not wants_acp_marketplace("bonjour")


def test_extract_job_id():
    ev = {"type": "job.funded", "jobId": "job-123"}
    assert acp_provider_skill._extract_job_id(ev) == "job-123"
    ev2 = {"type": "x", "data": {"job_id": "abc"}}
    assert acp_provider_skill._extract_job_id(ev2) == "abc"


def test_heuristic_deliverable_lite():
    d = acp_provider_skill._heuristic_audit("0x" + "a" * 40, full=False)
    assert d["liteVerdict"] in ("SAFE", "CAUTION", "DANGER")
    assert d["riskAlerts"]


def test_heuristic_deliverable_full():
    d = acp_provider_skill._heuristic_audit("0x" + "b" * 40, full=True)
    assert "verdict" in d
    assert "auditReport" in d
    assert "securityScore" in d


@pytest.mark.asyncio
async def test_drain_events_file(tmp_path, monkeypatch):
    from aria_core.testing import AriaRuntimeSettings, configure_test_runtime

    data_dir = tmp_path / "data"
    configure_test_runtime(
        data_dir=data_dir,
        settings=AriaRuntimeSettings(aria_acp_provider_enabled=True),
    )
    events = tmp_path / "events.jsonl"
    events.write_text(
        '{"type":"job.funded","jobId":"j1"}\n'
        '{"type":"ping"}\n',
        encoding="utf-8",
    )
    batch, _ = acp_provider_skill.drain_events_file(str(events))
    assert len(batch) == 2
    batch2, _ = acp_provider_skill.drain_events_file(str(events))
    assert len(batch2) == 0


@pytest.mark.asyncio
async def test_run_provider_cycle_no_cli(monkeypatch):
    monkeypatch.setattr(acp_provider_skill, "is_acp_available", lambda: False)
    result = await acp_provider_skill.run_provider_cycle()
    assert result["ok"] is False
    assert result["errors"]


@pytest.mark.asyncio
async def test_acp_status_command(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(
        acp_cli,
        "list_agents",
        lambda: ([{"name": "Aria Vanguard ZHC", "id": "x", "role": "HYBRID"}], None),
    )
    monkeypatch.setattr(
        acp_cli,
        "list_offerings",
        lambda: ([{"name": "analyse_lite_x1", "priceValue": 1.99}], None),
    )
    reply, data = await execute_acp_marketplace("acp status", lang="fr")
    assert "ACP STATUS" in reply
    assert data.get("acp") == "status"
    assert "analyse_lite" in reply


@pytest.mark.asyncio
async def test_acp_revenue_plan(monkeypatch):
    monkeypatch.setattr(acp_cli, "list_offerings", lambda: ([{"name": "analyse_lite_x1"}], None))
    reply, data = await execute_acp_marketplace(
        "concernant acp quel est ton plan pour generer des revenus",
        lang="fr",
    )
    assert data.get("acp") == "revenue_plan"
    assert "Plan revenus" in reply


def test_load_offering_templates():
    templates = acp_offering_skill.load_offering_templates()
    assert "analyse_lite_x1" in templates
    assert "analyse_full_x1" in templates
    assert templates["analyse_lite_x1"]["price_usd"] == 1.99


def test_build_offering_payload():
    tpl = acp_offering_skill.resolve_template("analyse_lite_x1")
    assert tpl is not None
    payload = acp_offering_skill.build_offering_payload(tpl, price_override=2.5)
    assert payload["name"] == "analyse_lite_x1"
    assert payload["price_value"] == 2.5
    assert payload["requirements"]["description"]
    assert payload["deliverable"]["properties"]["liteVerdict"]["description"]
    assert payload.get("subscription_ids") == "019f0664-640c-7cdc-807a-b09547461ad7"


def test_wants_acp_client_action():
    from aria_core.skills.acp_client_actions import wants_acp_client_action

    assert wants_acp_client_action("financer job acp 12345")
    assert wants_acp_client_action("trade acp swap 10 USDC contre ETH")
    assert not wants_acp_client_action("bonjour")


def test_resolve_subscription_ids_env_override(monkeypatch):
    monkeypatch.setenv("ARIA_ACP_SUBSCRIPTION_IDS", "sub-test-uuid")
    assert acp_offering_skill.resolve_subscription_ids() == "sub-test-uuid"


def test_enrich_json_schema():
    out = acp_schema.enrich_json_schema(
        {"type": "object", "properties": {"summary": {"type": "string"}}},
        title="t",
        description="d",
    )
    assert out["title"] == "t"
    assert out["properties"]["summary"]["description"]


def test_compose_product_tweet():
    tw = acp_product_launch_skill.compose_product_tweet(
        name="veille_zhc_x1",
        description="ZHC watch",
        price_usd=2.49,
        sla_minutes=15,
    )
    assert "veille_zhc_x1" in tw
    assert "Virtuals" in tw
    assert len(tw) <= 280
    assert "$" not in tw
    assert "financial advice" not in tw.lower()

    from aria_core.x_publication_policy import check_tweet_content

    ok, _ = check_tweet_content(tw)
    assert ok, tw


def test_parse_adhoc_workflow_dollars_centimes():
    msg = (
        "cree un workflow appeler test_1 a 25 dollars et 99 centimes sur acp "
        "qui propose des services d'analyse quantitatif"
    )
    spec = acp_offering_skill.parse_adhoc_workflow(msg)
    assert spec is not None
    assert abs(spec["price_usd"] - 25.99) < 0.01


def test_parse_adhoc_workflow():
    msg = (
        "cree un workflow appeler test_1 a 25$ et 99 centimes sur acp "
        "qui propose des services d'analyse quantitatif"
    )
    spec = acp_offering_skill.parse_adhoc_workflow(msg)
    assert spec is not None
    assert spec["name"] == "test_1"
    assert abs(spec["price_usd"] - 25.99) < 0.01
    assert "quantitatif" in spec["description"].lower()
    assert spec["sla_minutes"] == 60


@pytest.mark.asyncio
async def test_adhoc_workflow_create(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(acp_offering_skill, "list_offerings", lambda: ([], None))
    monkeypatch.setattr(
        acp_offering_skill,
        "create_offering",
        lambda **kwargs: (
            {"id": "new-1", "name": kwargs["name"], "priceValue": kwargs["price_value"]},
            None,
        ),
    )

    async def fake_promote(**kwargs):
        return {"x_posted": False, "tweet_text": "New ACP service live: test_1", "telegram_notified": True}

    monkeypatch.setattr(acp_product_launch_skill, "_promote_product", fake_promote)

    msg = "cree un workflow appeler test_1 a 25$ et 99 centimes sur acp qui propose analyse quantitative"
    reply, data = await execute_acp_marketplace(msg, lang="fr")
    assert data.get("acp") == "adhoc_create"
    assert "test_1" in reply
    assert "25.99" in reply or "25,99" in reply or "25" in reply
    assert "premium" in reply.lower()
    assert data.get("tweet_text")
    assert data.get("sample_request")


def test_build_offering_payload_injects_examples():
    tpl = acp_offering_skill.resolve_template("analyse_lite_x1")
    assert tpl is not None
    payload = acp_offering_skill.build_offering_payload(tpl)
    req = payload["requirements"]
    deliv = payload["deliverable"]
    assert req.get("examples") and req["examples"][0].get("contractAddress")
    assert deliv.get("examples") and deliv["examples"][0].get("liteVerdict")


def test_build_adhoc_payload_premium_x_account():
    spec = {
        "name": "test_1",
        "description": "analyse pertinence compte X",
        "price_usd": 25.99,
        "sla_minutes": 60,
    }
    payload = acp_offering_skill.build_adhoc_payload(spec)
    assert payload["service_kind"] == "x_account"
    assert "Premium" in payload["description"]
    assert "xHandle" in json.dumps(payload["requirements"])
    assert "relevanceScore" in json.dumps(payload["deliverable"])
    assert payload["requirements"].get("examples")
    assert payload["deliverable"].get("examples")


def test_wants_acp_offering_create():
    assert acp_offering_skill.wants_acp_offering_create("créer offre acp template analyse_lite_x1")
    assert not acp_offering_skill.wants_adhoc_acp_workflow("créer offre acp template analyse_lite_x1")
    assert not acp_offering_skill.wants_acp_offering_create("acp status")


def test_parse_delete_workflow_name():
    assert acp_offering_skill.parse_delete_workflow_name("supprime le workflow test 1 maintenant") == "test_1"
    assert acp_offering_skill.parse_delete_workflow_name("delete offering test_1") == "test_1"
    assert acp_offering_skill.parse_delete_workflow_name("supprime workflow veille_zhc_x1 sur acp") == "veille_zhc_x1"
    assert acp_offering_skill.parse_delete_workflow_name("acp status") is None


def test_wants_acp_offering_delete_all():
    assert acp_offering_skill.wants_acp_offering_delete_all("supprime tous les workflow sur acp")
    assert acp_offering_skill.wants_acp_offering_delete_all("delete all acp offerings")
    assert not acp_offering_skill.wants_acp_offering_delete_all("supprime le workflow test_1")


def test_wants_acp_marketplace_delete_without_acp_keyword():
    assert wants_acp_marketplace("supprime le workflow test 1 maintenant")


@pytest.mark.asyncio
async def test_acp_offering_delete(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(
        acp_offering_skill,
        "list_offerings",
        lambda: ([{"id": "oid-del", "name": "test_1"}], None),
    )
    monkeypatch.setattr(
        acp_offering_skill,
        "delete_offering",
        lambda offering_id, **kwargs: (offering_id == "oid-del", "deleted"),
    )
    reply, data = await execute_acp_marketplace("supprime le workflow test 1 maintenant", lang="fr")
    assert data.get("acp") == "offering_delete"
    assert data.get("offering_id") == "oid-del"
    assert "test_1" in reply
    assert "supprimé" in reply.lower()


@pytest.mark.asyncio
async def test_acp_offering_delete_all(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(
        acp_offering_skill,
        "list_offerings",
        lambda: (
            [
                {"id": "a1", "name": "test_1"},
                {"id": "a2", "name": "veille_zhc_x1"},
            ],
            None,
        ),
    )
    deleted: list[str] = []

    def fake_delete(offering_id, **kwargs):
        deleted.append(offering_id)
        return offering_id in ("a1", "a2"), "ok"

    monkeypatch.setattr(acp_offering_skill, "delete_offering", fake_delete)
    reply, data = await execute_acp_marketplace("supprime tous les workflow sur acp", lang="fr")
    assert data.get("acp") == "offering_delete_all"
    assert set(deleted) == {"a1", "a2"}
    assert "2 supprimé" in reply


@pytest.mark.asyncio
async def test_acp_offering_delete_not_found(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(acp_offering_skill, "list_offerings", lambda: ([], None))
    reply, data = await execute_acp_marketplace("supprime le workflow ghost_1", lang="fr")
    assert data.get("acp") == "offering_delete_not_found"


@pytest.mark.asyncio
async def test_acp_templates_command():
    reply, data = await execute_acp_marketplace("templates offres acp", lang="fr")
    assert data.get("acp") == "templates"
    assert "analyse_lite_x1" in reply


@pytest.mark.asyncio
async def test_acp_offering_create_missing_template(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    reply, data = await execute_acp_marketplace("créer offre acp", lang="fr")
    assert data.get("acp") == "offering_create_missing_template"


@pytest.mark.asyncio
async def test_acp_offering_create_new(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(acp_offering_skill, "list_offerings", lambda: ([], None))
    monkeypatch.setattr(
        acp_offering_skill,
        "create_offering",
        lambda **kwargs: (
            {"id": "off-1", "name": kwargs["name"], "priceValue": kwargs["price_value"]},
            None,
        ),
    )
    reply, data = await execute_acp_marketplace(
        "créer workflow acp template veille_zhc_x1 prix 2.99",
        lang="fr",
    )
    assert data.get("acp") == "offering_create"
    assert data.get("action") == "create"
    assert "veille_zhc_x1" in reply


@pytest.mark.asyncio
async def test_acp_offering_create_updates_existing(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    monkeypatch.setattr(
        acp_offering_skill,
        "list_offerings",
        lambda: ([{"id": "existing-id", "name": "analyse_lite_x1"}], None),
    )
    monkeypatch.setattr(
        acp_offering_skill,
        "update_offering",
        lambda offering_id, **kwargs: (
            {"id": offering_id, "name": "analyse_lite_x1", "priceValue": kwargs["price_value"]},
            None,
        ),
    )
    reply, data = await execute_acp_marketplace(
        "créer offre acp template analyse_lite_x1",
        lang="fr",
    )
    assert data.get("action") == "update"
    assert "update" in reply.lower() or "Workflow" in reply


@pytest.mark.asyncio
async def test_acp_product_launch(monkeypatch):
    monkeypatch.setattr(acp_cli, "is_acp_available", lambda: True)
    captured = {}

    async def fake_upsert(payload):
        captured.update(payload)
        return {"id": "oid-1", "name": "veille_zhc_x1"}, "update", None

    async def fake_promote(**kwargs):
        return {"x_posted": True, "tweet_text": "tweet", "telegram_notified": True}

    monkeypatch.setattr(acp_product_launch_skill, "_upsert_offering", fake_upsert)
    monkeypatch.setattr(acp_product_launch_skill, "_promote_product", fake_promote)
    reply, data = await execute_acp_marketplace(
        "lancer produit acp template veille_zhc_x1 et publier sur X",
        lang="fr",
    )
    assert data.get("acp") == "product_launch"
    assert captured["deliverable"]["description"]
    assert captured["requirements"]["description"]
    assert captured["name"] == "veille_zhc_x1"
    assert "Schémas" in reply or "Produit ACP" in reply


def test_create_offering_cli_args(monkeypatch):
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        return 0, json.dumps({"id": "x", "name": "test_off"}), ""

    monkeypatch.setattr(acp_cli, "run_acp", fake_run)
    row, err = acp_cli.create_offering(
        name="test_off",
        description="desc",
        price_value=3.5,
        requirements={"type": "object"},
        deliverable={"type": "object"},
    )
    assert err is None
    assert row["name"] == "test_off"
    assert "offering" in captured["args"]
    assert "create" in captured["args"]


def test_resolve_acp_command_windows(monkeypatch):
    monkeypatch.setenv("APPDATA", str(Path("C:/Users/X/AppData/Roaming")))
    fake = Path("C:/Users/X/AppData/Roaming/npm/acp.cmd")

    def fake_is_file(self):
        return str(self).endswith("acp.cmd")

    monkeypatch.setattr(Path, "is_file", fake_is_file, raising=False)
    cmd = acp_cli.resolve_acp_command()
    assert cmd[0] == "cmd.exe"
    assert "acp.cmd" in cmd[-1]