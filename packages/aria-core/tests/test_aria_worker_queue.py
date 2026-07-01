import pytest

from aria_core.aria_worker_queue import (
    WorkerTask,
    _append_task_to_markdown,
    _has_pending_task,
    count_pending_tasks,
    mark_task_done_in_markdown,
)


def test_worker_task_markdown():
    task = WorkerTask(
        task_id="cap-gap-x_oauth",
        title="Cles X OAuth",
        source="capability_gap",
        problem="OAuth absent",
        action="Configurer sur Render",
        repos=("aria-sandbox",),
        acceptance=("is_x_post_configured() True",),
    )
    md = task.to_markdown()
    assert "[pending] cap-gap-x_oauth" in md
    assert "OAuth absent" in md
    assert "is_x_post_configured()" in md


def test_append_and_dedup():
    task = WorkerTask(
        task_id="cap-gap-test-v1",
        title="Patch fail",
        source="capability_gap",
        problem="anchor missing",
        action="Fix anchor",
    )
    body = _append_task_to_markdown("", task)
    assert count_pending_tasks(body) == 1
    body2 = _append_task_to_markdown(body, task)
    assert body2.strip() == body.strip()
    assert _has_pending_task(body, "cap-gap-test-v1")


def test_mark_done():
    task = WorkerTask(
        task_id="test-task",
        title="T",
        source="test",
        problem="p",
        action="a",
    )
    body = _append_task_to_markdown("", task)
    done = mark_task_done_in_markdown(body, "test-task")
    assert "[done] test-task" in done
    assert "[pending] test-task" not in done
    assert count_pending_tasks(done) == 0


@pytest.mark.asyncio
async def test_enqueue_local_only(monkeypatch, tmp_path):
    from aria_core import aria_worker_queue as mod

    monkeypatch.setattr(mod, "_queue_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_local_jsonl", lambda: tmp_path / "tasks.jsonl")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    monkeypatch.setattr(mod, "append_memory", lambda *a, **k: None)

    async def noop_notify(*a, **k):
        return None

    monkeypatch.setattr(mod, "_notify_worker_task", noop_notify)

    out = await mod.enqueue_worker_task(
        task_id="test-local",
        title="Test",
        source="test",
        problem="blocked",
        action="fix",
    )
    assert out["status"] == "local_only"
    assert (tmp_path / "tasks.jsonl").is_file()