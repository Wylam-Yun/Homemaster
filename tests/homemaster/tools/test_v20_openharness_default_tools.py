"""Red baseline for the OpenHarness default-tool port in the Home profile."""

from tests.homemaster.tools.universal_harness import registry

OPENHARNESS_DEFAULT_TOOL_NAMES = (
    "bash",
    "ask_user_question",
    "read_file",
    "write_file",
    "edit_file",
    "notebook_edit",
    "lsp",
    "mcp_auth",
    "glob",
    "grep",
    "image_to_text",
    "image_generation",
    "skill",
    "tool_search",
    "web_fetch",
    "web_search",
    "config",
    "brief",
    "sleep",
    "enter_worktree",
    "exit_worktree",
    "todo_write",
    "enter_plan_mode",
    "exit_plan_mode",
    "cron_create",
    "cron_list",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
    "task_create",
    "task_get",
    "task_list",
    "task_stop",
    "task_output",
    "task_update",
    "agent",
    "send_message",
    "team_create",
    "team_delete",
)


def test_universal_registry_exposes_every_locked_default_tool() -> None:
    available = set(registry().all_names())

    assert set(OPENHARNESS_DEFAULT_TOOL_NAMES) <= available
