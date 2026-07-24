"""External terminal-state gates for application-owned OpenHarness services."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest

from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.config import HomeMasterConfig, load_config
from homemaster.events import JsonlTraceSink, RuntimeEvent
from homemaster.mcp.client import McpClientManager, McpConnection
from homemaster.permissions import PermissionMode
from homemaster.tools.contracts import ToolExecutionStatus
from homemaster.tools.runtime_services import HomeToolServices
from tests.homemaster.tools.universal_harness import execute, registry


async def _execute(profile, root, services, name, arguments, *, extra_services=None):
    service_map = {
        "tool_services": services,
        "plan_mode": services.plan_mode,
        **(extra_services or {}),
    }
    return await execute(
        profile,
        root,
        name,
        arguments,
        capabilities=(
            "tool.read",
            "tool.mutate",
            "tool.auto",
            "filesystem.read",
            "filesystem.write",
            "process.exec",
            "network.http",
            "scheduler.manage",
            "config.mutate",
            "mcp.manage",
            "process.spawn",
        ),
        services=service_map,
    )


@pytest.mark.asyncio
async def test_lsp_and_ask_user_use_real_workspace_and_entry_callback(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "def target(value: int) -> int:\n    return value + 1\n\nresult = target(2)\n",
        encoding="utf-8",
    )
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")

    async def prompt(question: str) -> str:
        assert question == "Continue?"
        return "yes"

    try:
        symbols = await _execute(
            profile,
            tmp_path,
            services,
            "lsp",
            {"operation": "go_to_definition", "file_path": "sample.py", "symbol": "target"},
        )
        answer = await _execute(
            profile,
            tmp_path,
            services,
            "ask_user_question",
            {"question": "Continue?"},
            extra_services={"ask_user_prompt": prompt},
        )

        assert symbols.status is ToolExecutionStatus.SUCCESS
        assert "sample.py:1" in symbols.text
        assert answer.status is ToolExecutionStatus.SUCCESS
        assert answer.text == "yes"
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_ask_user_without_entry_callback_returns_durable_wait_marker(
    tmp_path: Path,
) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")

    try:
        result = await _execute(
            profile,
            tmp_path,
            services,
            "ask_user_question",
            {"question": "Which room?"},
        )

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.text == "Which room?"
        assert {key: result.data[key] for key in ("waiting_user", "question", "tool_call_id")} == {
            "waiting_user": True,
            "question": "Which room?",
            "tool_call_id": "call-ask_user_question",
        }
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_image_tools_use_configured_provider_boundary_and_publish_verified_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from homemaster.tools.image_generation import ImageGenerationTool
    from homemaster.tools.image_to_text import ImageToTextTool

    source_bytes = b"\x89PNG\r\n\x1a\nsource"
    generated_bytes = b"\x89PNG\r\n\x1a\ngenerated"
    source = tmp_path / "source.png"
    source.write_bytes(source_bytes)
    seen: dict[str, object] = {}

    async def describe(**kwargs):
        seen["vision_image"] = base64.b64decode(kwargs["image_data"])
        seen["vision_model"] = kwargs["model"]
        return "fixture description"

    async def generate(self, arguments, config):
        del self, arguments
        seen["generation_key"] = config["api_key"]
        return [base64.b64encode(generated_bytes).decode("ascii")]

    monkeypatch.setattr(ImageToTextTool, "_call_vision_model", staticmethod(describe))
    monkeypatch.setattr(ImageGenerationTool, "_generate_with_openai", generate)
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    provider = {"model": "fixture-vision", "api_key": "fixture-key", "base_url": ""}
    try:
        described = await _execute(
            profile,
            tmp_path,
            services,
            "image_to_text",
            {"image_path": "source.png"},
            extra_services={"vision_model_config": provider},
        )
        generated = await _execute(
            profile,
            tmp_path,
            services,
            "image_generation",
            {"prompt": "fixture", "output_path": "generated.png"},
            extra_services={"image_generation_config": provider},
        )

        assert described.status is ToolExecutionStatus.SUCCESS
        assert "fixture description" in described.text
        assert seen["vision_image"] == source_bytes
        assert seen["vision_model"] == "fixture-vision"
        assert generated.status is ToolExecutionStatus.SUCCESS
        assert generated.verification.status.value == "passed"
        assert seen["generation_key"] == "fixture-key"
        assert (tmp_path / "generated.png").read_bytes() == generated_bytes
        assert len(generated.images) == 1

        store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60)
        artifacts = ArtifactPublisher(store).publish(
            generated.raw,
            tenant_id="tenant",
            session_id="service-session",
            run_id="service-run",
        )
        artifact = artifacts[0]
        assert len(generated.images) == 1
        assert (
            store.read(
                artifact["artifact_handle"],
                tenant_id="tenant",
                session_id="service-session",
                run_id="service-run",
            )
            == generated_bytes
        )
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_config_and_mcp_auth_persist_home_yaml_and_reconnect(tmp_path: Path) -> None:
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        "permissions:\n"
        "  mode: default\n"
        "mcp:\n"
        "  servers:\n"
        "    demo:\n"
        "      transport: stdio\n"
        "      command: fixture\n"
        "      env:\n"
        "        TOKEN: old-token\n",
        encoding="utf-8",
    )
    os.chmod(config_path, 0o600)
    config = load_config(config_path)
    profile = registry()
    services = HomeToolServices(config, state_root=tmp_path / "home")
    seen_tokens: list[str] = []
    closed = 0

    class Session:
        async def initialize(self):
            return None

        async def list_tools(self):
            return []

        async def list_resources(self):
            return []

    async def connector(name, server_config):
        del name
        seen_tokens.append(server_config.env["TOKEN"])

        async def close():
            nonlocal closed
            closed += 1

        return McpConnection(Session(), close)

    manager = McpClientManager(config.mcp.servers, connector=connector)
    try:
        await manager.connect_all()
        changed = await _execute(
            profile,
            tmp_path,
            services,
            "config",
            {"action": "set", "key": "permissions.mode", "value": "full_auto"},
        )
        authenticated = await _execute(
            profile,
            tmp_path,
            services,
            "mcp_auth",
            {"server_name": "demo", "mode": "env", "key": "TOKEN", "value": "new-token"},
            extra_services={"mcp_manager": manager},
        )

        reloaded = load_config(config_path)
        assert changed.status is ToolExecutionStatus.SUCCESS
        assert reloaded.permissions.mode is PermissionMode.FULL_AUTO
        assert reloaded.mcp.servers["demo"].env["TOKEN"] == "new-token"
        assert authenticated.status is ToolExecutionStatus.SUCCESS
        assert authenticated.data["state"] == "connected"
        assert seen_tokens == ["old-token", "new-token"]
        assert closed == 1
        assert config_path.stat().st_mode & 0o777 == 0o600
    finally:
        await manager.aclose()
        await services.aclose()


@pytest.mark.asyncio
async def test_config_show_preserves_provider_and_mcp_credentials(tmp_path: Path) -> None:
    provider_secret = "provider-SUPERSECRET"
    mcp_env_secret = "mcp-env-SUPERSECRET"
    mcp_header_secret = "mcp-header-SUPERSECRET"
    feishu_secret = "feishu-SUPERSECRET"
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        "providers:\n"
        "  default: private\n"
        "  items:\n"
        "    - name: private\n"
        "      kind: chat\n"
        "      api_format: anthropic\n"
        "      transport: anthropic_sdk\n"
        "      base_url: https://user:password@provider.example/v1\n"
        "      model: private-model\n"
        f"      api_keys: [{provider_secret}]\n"
        "mcp:\n"
        "  servers:\n"
        "    private:\n"
        "      transport: http\n"
        "      url: https://mcp-user:mcp-password@mcp.example/api\n"
        f"      headers: {{X-Custom-Credential: {mcp_header_secret}}}\n"
        "    private_stdio:\n"
        "      transport: stdio\n"
        "      command: fixture\n"
        f"      env: {{ARBITRARY_NAME: {mcp_env_secret}}}\n"
        "gateway:\n"
        "  feishu:\n"
        "    app_id: cli_identifier\n"
        f"    app_secret: {feishu_secret}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    profile = registry()
    services = HomeToolServices(config, state_root=tmp_path / "home")
    try:
        shown = await _execute(profile, tmp_path, services, "config", {"action": "show"})
        serialized = json.dumps(shown.to_dict(), sort_keys=True)
        message = shown.raw
        trace = JsonlTraceSink(tmp_path / "trace")
        trace.emit(
            RuntimeEvent(
                type="tool.call_completed",
                session_id="service-session",
                run_id="service-run",
                turn_index=0,
                tool_call_id="show-config",
                name="config",
                payload={"result": shown.to_dict()},
            )
        )
        trace.close()
        persisted_trace = (tmp_path / "trace" / "runtime_events.jsonl").read_text(encoding="utf-8")

        assert shown.status is ToolExecutionStatus.SUCCESS
        for secret in (
            provider_secret,
            mcp_env_secret,
            mcp_header_secret,
            feishu_secret,
            "user:password",
            "mcp-user:mcp-password",
        ):
            assert secret in serialized
            assert secret in str(message)
            assert secret in persisted_trace
    finally:
        await services.aclose()


@pytest.mark.parametrize(
    ("tool_name", "capability"),
    [
        ("cron_create", "scheduler.manage"),
        ("cron_list", "scheduler.manage"),
        ("remote_trigger", "scheduler.manage"),
        ("config", "config.mutate"),
        ("mcp_auth", "mcp.manage"),
        ("task_create", "process.spawn"),
        ("task_stop", "process.spawn"),
        ("agent", "process.spawn"),
        ("send_message", "process.spawn"),
        ("team_create", "process.spawn"),
    ],
)
def test_service_tools_declare_independent_management_capabilities(
    tool_name: str, capability: str
) -> None:
    profile = registry()
    tool = profile.get(tool_name)

    assert tool is not None
    assert capability in tool.required_capabilities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "capability"),
    [
        ("cron_list", {}, "scheduler.manage"),
        ("config", {"action": "show"}, "config.mutate"),
        (
            "mcp_auth",
            {"server_name": "missing", "mode": "env", "key": "TOKEN", "value": "x"},
            "mcp.manage",
        ),
        ("task_list", {}, "process.spawn"),
        ("agent", {"description": "test", "prompt": "test"}, "process.spawn"),
    ],
)
async def test_service_tools_reject_subject_missing_management_capability(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
    capability: str,
) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    try:
        result = await execute(
            profile,
            tmp_path,
            tool_name,
            arguments,
            capabilities=("tool.read", "tool.mutate", "tool.auto"),
            services={"tool_services": services, "plan_mode": services.plan_mode},
            call_id=f"deny-{tool_name}",
        )
        assert result.status is ToolExecutionStatus.DENIED
        assert result.error is not None
        assert capability in result.error.message
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_cron_tools_persist_toggle_trigger_and_delete_real_state(tmp_path: Path) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    terminal = tmp_path / "cron-terminal.txt"
    try:
        created = await _execute(
            profile,
            tmp_path,
            services,
            "cron_create",
            {
                "name": "terminal-gate",
                "schedule": "*/5 * * * *",
                "command": f"printf verified > {terminal}",
            },
        )
        listed = await _execute(profile, tmp_path, services, "cron_list", {})
        toggled = await _execute(
            profile,
            tmp_path,
            services,
            "cron_toggle",
            {"name": "terminal-gate", "enabled": False},
        )
        triggered = await _execute(
            profile,
            tmp_path,
            services,
            "remote_trigger",
            {"name": "terminal-gate", "timeout_seconds": 5},
        )

        cron_registry = json.loads(services.cron.registry_path.read_text(encoding="utf-8"))
        assert created.status is ToolExecutionStatus.SUCCESS
        assert listed.status is ToolExecutionStatus.SUCCESS
        assert toggled.status is ToolExecutionStatus.SUCCESS
        assert cron_registry[0]["enabled"] is False
        assert triggered.status is ToolExecutionStatus.SUCCESS
        assert triggered.data["return_code"] == 0
        assert terminal.read_text(encoding="utf-8") == "verified"

        deleted = await _execute(
            profile,
            tmp_path,
            services,
            "cron_delete",
            {"name": "terminal-gate"},
        )
        assert deleted.status is ToolExecutionStatus.SUCCESS
        assert json.loads(services.cron.registry_path.read_text(encoding="utf-8")) == []
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_task_tools_observe_output_metadata_and_real_process_stop(tmp_path: Path) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    try:
        created = await _execute(
            profile,
            tmp_path,
            services,
            "task_create",
            {
                "type": "local_bash",
                "description": "output gate",
                "command": "printf task-output",
            },
        )
        task_id = str(created.data["task_id"])
        task = services.tasks.get_task(task_id)
        assert task is not None
        for _ in range(200):
            if task.status != "running":
                break
            await asyncio.sleep(0.01)
        output = await _execute(
            profile,
            tmp_path,
            services,
            "task_output",
            {"task_id": task_id},
        )
        updated = await _execute(
            profile,
            tmp_path,
            services,
            "task_update",
            {"task_id": task_id, "progress": 100, "status_note": "verified"},
        )
        listed = await _execute(profile, tmp_path, services, "task_list", {})

        assert task.status == "completed"
        assert task.return_code == 0
        assert output.text == "task-output"
        assert updated.data["metadata"] == {"progress": "100", "status_note": "verified"}
        assert any(item["task_id"] == task_id for item in listed.data["tasks"])
        records = json.loads((services.tasks.tasks_dir / "tasks.json").read_text(encoding="utf-8"))
        assert records[0]["return_code"] == 0

        child_pid_path = tmp_path / "task-child.pid"
        running = await _execute(
            profile,
            tmp_path,
            services,
            "task_create",
            {
                "type": "local_bash",
                "description": "stop gate",
                "command": (
                    "sleep 30 & child=$!; "
                    f"printf '%s' \"$child\" > {child_pid_path}; "
                    "wait \"$child\""
                ),
            },
        )
        running_id = str(running.data["task_id"])
        process = services.tasks._processes[running_id]
        pid = process.pid
        for _ in range(200):
            if child_pid_path.exists() and child_pid_path.read_text(encoding="utf-8"):
                break
            await asyncio.sleep(0.01)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        stopped = await _execute(
            profile,
            tmp_path,
            services,
            "task_stop",
            {"task_id": running_id},
        )
        assert stopped.status is ToolExecutionStatus.SUCCESS
        assert stopped.data["domain_status"] == "killed"
        for stopped_pid in (pid, child_pid):
            with pytest.raises(ProcessLookupError):
                os.kill(stopped_pid, 0)
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_agent_and_send_message_reach_same_interactive_child_process(tmp_path: Path) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    command = (
        f'{Path(os.sys.executable)} -u -c "import sys; '
        '[print(line.strip(), flush=True) for line in sys.stdin]"'
    )
    try:
        spawned = await _execute(
            profile,
            tmp_path,
            services,
            "agent",
            {
                "description": "message gate",
                "prompt": "initial-message",
                "subagent_type": "worker",
                "team": "alpha",
                "command": command,
            },
        )
        sent = await _execute(
            profile,
            tmp_path,
            services,
            "send_message",
            {"task_id": spawned.data["agent_id"], "message": "follow-up-message"},
        )
        task_id = str(spawned.data["task_id"])
        task = services.tasks.get_task(task_id)
        assert task is not None
        for _ in range(200):
            output = task.output_file.read_text(encoding="utf-8")
            if "initial-message" in output and "follow-up-message" in output:
                break
            await asyncio.sleep(0.01)

        assert spawned.status is ToolExecutionStatus.SUCCESS
        assert sent.status is ToolExecutionStatus.SUCCESS
        assert "initial-message" in output
        assert "follow-up-message" in output
        teams = json.loads(services.teams.path.read_text(encoding="utf-8"))
        assert teams[0]["agents"] == [task_id]
        stopped = await services.tasks.stop_task(task_id)
        assert stopped.status == "killed"
    finally:
        await services.aclose()


@pytest.mark.asyncio
async def test_team_registry_and_plan_mode_change_authoritative_home_state(tmp_path: Path) -> None:
    profile = registry()
    services = HomeToolServices(HomeMasterConfig(), state_root=tmp_path / "home")
    try:
        created = await _execute(
            profile,
            tmp_path,
            services,
            "team_create",
            {"name": "alpha", "description": "gate"},
        )
        teams_path = services.teams.path
        assert created.status is ToolExecutionStatus.SUCCESS
        assert json.loads(teams_path.read_text(encoding="utf-8"))[0]["name"] == "alpha"
        deleted = await _execute(
            profile,
            tmp_path,
            services,
            "team_delete",
            {"name": "alpha"},
        )
        assert deleted.status is ToolExecutionStatus.SUCCESS
        assert json.loads(teams_path.read_text(encoding="utf-8")) == []

        entered = await _execute(profile, tmp_path, services, "enter_plan_mode", {})
        denied = await _execute(
            profile,
            tmp_path,
            services,
            "write_file",
            {"path": "plan-mode.txt", "content": "blocked"},
        )
        assert denied.status is ToolExecutionStatus.DENIED
        assert not (tmp_path / "plan-mode.txt").exists()
        exited = await _execute(profile, tmp_path, services, "exit_plan_mode", {})
        allowed = await _execute(
            profile,
            tmp_path,
            services,
            "write_file",
            {"path": "plan-mode.txt", "content": "allowed"},
        )

        assert entered.status is ToolExecutionStatus.SUCCESS
        assert exited.status is ToolExecutionStatus.SUCCESS
        assert allowed.status is ToolExecutionStatus.SUCCESS
        assert (tmp_path / "plan-mode.txt").read_text(encoding="utf-8") == "allowed"
    finally:
        await services.aclose()
