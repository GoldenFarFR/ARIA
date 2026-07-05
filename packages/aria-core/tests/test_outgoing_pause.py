"""Kill-switch sortant — état persistant + blocages effectifs aux points de sortie."""
import json

import pytest

from aria_core import outgoing_pause
from aria_core.paths import configure_data_dir


# --- État / persistance / robustesse ---------------------------------------


def test_default_not_paused(tmp_path):
    configure_data_dir(tmp_path)
    assert outgoing_pause.is_paused() is False
    st = outgoing_pause.pause_status()
    assert st["paused"] is False
    assert st["since"] is None


def test_pause_then_resume(tmp_path):
    configure_data_dir(tmp_path)
    outgoing_pause.pause(by=12345, reason="stop test")
    assert outgoing_pause.is_paused() is True
    st = outgoing_pause.pause_status()
    assert st["paused"] is True
    assert st["by"] == 12345
    assert st["reason"] == "stop test"
    assert st["since"] is not None

    outgoing_pause.resume(by=12345)
    assert outgoing_pause.is_paused() is False
    assert outgoing_pause.pause_status()["since"] is None


def test_state_persists_on_disk(tmp_path):
    """L'état vit sur disque → survit à un redémarrage (aucun cache mémoire perdu au reboot)."""
    configure_data_dir(tmp_path)
    outgoing_pause.pause(by=1)
    state_file = tmp_path / "pause_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["paused"] is True
    # Relecture "fraîche" (simule un nouveau process) : la pause tient toujours.
    assert outgoing_pause.is_paused() is True


def test_corrupt_file_asymmetric(tmp_path):
    """Fichier corrompu = « le doute » : fail-open tweets/jobs, fail-closed argent."""
    configure_data_dir(tmp_path)
    (tmp_path / "pause_state.json").write_text("{ not valid json", encoding="utf-8")
    # tweets / réponses / likes / jobs → continuent (ARIA ne se brique pas)
    assert outgoing_pause.is_paused() is False
    # dépenses → gelées par sécurité
    assert outgoing_pause.is_paused(strict=True) is True
    reason = outgoing_pause.money_block_reason()
    assert reason is not None and "fail-closed" in reason.lower()
    # /status doit signaler l'état illisible
    assert outgoing_pause.pause_status()["readable"] is False


def test_missing_file_does_not_block_money(tmp_path):
    """Absence de fichier = état propre « jamais pausée » (pas un doute) → l'argent passe."""
    configure_data_dir(tmp_path)
    assert outgoing_pause.is_paused(strict=True) is False
    assert outgoing_pause.money_block_reason() is None
    assert outgoing_pause.pause_status()["readable"] is True


def test_normal_pause_blocks_money(tmp_path):
    configure_data_dir(tmp_path)
    outgoing_pause.pause(by=1)
    assert outgoing_pause.money_block_reason("Cette dépense") is not None


def test_blocked_notice_reminds_pause_and_since(tmp_path):
    """Le message de blocage rappelle que la pause est active ET depuis quand (choix opérateur)."""
    configure_data_dir(tmp_path)
    outgoing_pause.pause(by=1)
    notice = outgoing_pause.blocked_notice("La publication d'un tweet")
    assert "pause" in notice.lower()
    assert "UTC" in notice  # since_label expose l'heure de début
    assert "/start" in notice


# --- Blocages effectifs aux points de sortie -------------------------------


@pytest.mark.asyncio
async def test_post_tweet_blocked_when_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.gateway.x_twitter import post_tweet

    outgoing_pause.pause(by=1)
    result, note = await post_tweet("hello world")
    assert result is None
    assert "pause" in note.lower()


@pytest.mark.asyncio
async def test_profile_writes_blocked_when_paused(tmp_path):
    """Kill-switch total : les écritures de profil X manuelles sont gelées en pause."""
    configure_data_dir(tmp_path)
    from aria_core.gateway.x_twitter import (
        apply_profile_banner,
        apply_profile_image,
        apply_x_profile_fields,
    )

    outgoing_pause.pause(by=1)
    from pathlib import Path

    assert await apply_profile_image(Path("x.png")) is False
    assert await apply_x_profile_fields({"name": "X"}) is False
    assert await apply_profile_banner(Path("x.png")) is False


@pytest.mark.asyncio
async def test_reply_blocked_when_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.gateway.x_twitter import reply_to_tweet

    outgoing_pause.pause(by=1)
    reply_id, note = await reply_to_tweet("salut", in_reply_to_tweet_id="123")
    assert reply_id is None
    assert "pause" in note.lower()


@pytest.mark.asyncio
async def test_escalate_spend_blocked_when_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.wallet_guard import SpendEscalationError, escalate_spend

    outgoing_pause.pause(by=1)
    with pytest.raises(SpendEscalationError):
        await escalate_spend(
            "client_fund_job",
            amount="1 USDC",
            counterparty="job x",
            description="Financer job x",
            payload={"job_id": "x", "amount_usdc": 1.0},
        )


@pytest.mark.asyncio
async def test_resolve_spend_execution_blocked_when_paused(tmp_path):
    """Hard-stop argent : un « Oui » sur un vieux prompt ne dépense pas pendant la pause."""
    configure_data_dir(tmp_path)
    from aria_core.wallet_guard import resolve_spend

    outgoing_pause.pause(by=1)
    out = await resolve_spend("deadbeef", True, "1")
    assert "pause" in out.lower()


@pytest.mark.asyncio
async def test_resolve_spend_not_blocked_when_active(tmp_path):
    """Hors pause, le garde-fou n'interfère pas (l'entrée est juste introuvable ici)."""
    configure_data_dir(tmp_path)
    from aria_core.wallet_guard import resolve_spend

    out = await resolve_spend("no-such-entry", True, "1")
    assert "pause" not in out.lower()
    assert "fail-closed" not in out.lower()


@pytest.mark.asyncio
async def test_escalate_spend_blocked_on_corrupt_state(tmp_path):
    """Fail-closed : un état corrompu bloque l'escalade de dépense (pas ARIA en pause, mais le doute)."""
    configure_data_dir(tmp_path)
    (tmp_path / "pause_state.json").write_text("{corrupt", encoding="utf-8")
    from aria_core.wallet_guard import SpendEscalationError, escalate_spend

    with pytest.raises(SpendEscalationError):
        await escalate_spend(
            "client_fund_job",
            amount="1 USDC",
            counterparty="job x",
            description="d",
            payload={"job_id": "x", "amount_usdc": 1.0},
        )


@pytest.mark.asyncio
async def test_resolve_spend_blocked_on_corrupt_state(tmp_path):
    configure_data_dir(tmp_path)
    (tmp_path / "pause_state.json").write_text("{corrupt", encoding="utf-8")
    from aria_core.wallet_guard import resolve_spend

    out = await resolve_spend("deadbeef", True, "1")
    assert "fail-closed" in out.lower()


@pytest.mark.asyncio
async def test_tweet_not_blocked_on_corrupt_state(tmp_path):
    """Fail-open : un état corrompu ne bloque PAS un tweet (ARIA continue de tourner)."""
    configure_data_dir(tmp_path)
    (tmp_path / "pause_state.json").write_text("{corrupt", encoding="utf-8")
    from aria_core.gateway.x_twitter import post_tweet

    result, note = await post_tweet("hello monde")
    # Ne doit PAS être le message de pause : on est passé au-delà du garde-fou.
    assert "en pause" not in note.lower()
