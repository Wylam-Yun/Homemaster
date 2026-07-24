"""Red baseline for the OpenHarness default-tool port in the Home profile."""

from homemaster.adapters.profiles import build_home_profile

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


def test_home_profile_exposes_every_locked_openharness_default_tool() -> None:
    available = set(build_home_profile().model_tool_names)

    assert set(OPENHARNESS_DEFAULT_TOOL_NAMES) <= available
