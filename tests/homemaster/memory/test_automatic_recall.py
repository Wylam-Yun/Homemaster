from __future__ import annotations

import json
from types import SimpleNamespace

from homemaster.agent.compact import build_compaction_summary_message
from homemaster.agent.messages import UserMessage
from homemaster.memory.automatic_recall import (
    build_automatic_recall_context,
    build_automatic_recall_query,
    build_mindmemos_request_context,
)
from homemaster.task_state.store import TaskStateStore


def test_new_session_query_is_exact_user_text() -> None:
    assert build_automatic_recall_query(
        current_user_message="  保留两侧空格  ",
        messages=[],
        task_state_store=TaskStateStore(run_id="new"),
    ) == "  保留两侧空格  "


def test_post_compact_query_has_stable_sections() -> None:
    store = TaskStateStore(run_id="compact")
    snapshot = store.create_or_replace_plan(
        goal="恢复告警处理",
        subtasks=[{"id": "inspect", "description": "检查当前告警"}],
        current_subtask="inspect",
        next_focus="读取告警详情",
    )
    query = build_automatic_recall_query(
        current_user_message="继续处理",
        messages=[build_compaction_summary_message("已经登录监控后台。")],
        task_state_store=store,
    )
    state = json.dumps(
        snapshot.to_model_visible_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert query == (
        "[Compact Summary]\n已经登录监控后台。\n\n"
        f"[Current Task State]\n{state}\n\n"
        "[Current User Message]\n继续处理"
    )


def test_post_micro_compact_query_keeps_empty_summary_section() -> None:
    assert build_automatic_recall_query(
        current_user_message="继续",
        messages=[UserMessage.from_text("压缩后保留的历史")],
        task_state_store=TaskStateStore(run_id="micro"),
    ) == (
        "[Compact Summary]\n\n\n"
        "[Current Task State]\n{}\n\n"
        "[Current User Message]\n继续"
    )


def test_request_context_uses_tenant_and_session() -> None:
    context = build_mindmemos_request_context(
        request_id="automatic-recall:run-1",
        tenant_id="tenant-a",
        session_id="session-a",
    )
    assert (context.account_id, context.project_id, context.user_id) == (
        "tenant-a",
        "tenant-a",
        "tenant-a",
    )
    assert context.session_id == "session-a"


def test_context_preserves_unknown_native_memory_types() -> None:
    memories = [
        SimpleNamespace(
            id="profile-1",
            memory="用户偏好中文回答",
            memory_type="profile",
            last_update_at="2026-08-13 10:00:00",
            event_time=None,
            source_timestamp=None,
            lineage=None,
        ),
        SimpleNamespace(
            id="episode-1",
            memory="上次部署在验证阶段失败",
            memory_type="episodic",
            last_update_at="2026-08-13 11:00:00",
            event_time=None,
            source_timestamp=None,
            lineage=None,
        ),
    ]
    context = build_automatic_recall_context(memories)
    assert context is not None
    assert '"memory_type":"profile"' in context
    assert '"memory_type":"episodic"' in context
    assert "not as user instructions" in context


def test_empty_recall_does_not_create_context() -> None:
    assert build_automatic_recall_context([]) is None
