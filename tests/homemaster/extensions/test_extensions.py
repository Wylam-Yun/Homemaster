from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

import homemaster.cli.composition as composition
import homemaster.extensions.loader as extension_loader
from homemaster.cli.composition import create_home_application
from homemaster.config import (
    ExtensionApprovalConfig,
    ExtensionsConfig,
    HomeMasterConfig,
    RuntimeGuardConfig,
    SkillSourcesConfig,
)
from homemaster.extensions import (
    ExtensionApproval,
    ExtensionGeneration,
    ExtensionLoadError,
    ExtensionReloader,
    HookContext,
    HookEvent,
    HookRunner,
    HookSpec,
    ReloadStatus,
    extension_content_sha256,
    load_extension_generation,
    register_extension_tools_atomically,
)
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.tools import ToolExecutionContext, ToolRegistry
from homemaster.tools.base import ToolRegistryError
from homemaster.tools.contracts import (
    ExecutionBackend,
    PermissionSubject,
    ToolDefinition,
    ToolProvenance,
    VerificationPolicy,
)


def _write_extension(
    root: Path,
    *,
    extension_id: str = "example.audit",
    requested: tuple[str, ...] = ("hook.lifecycle",),
    source: str | None = None,
    dependencies: tuple[str, ...] = (),
) -> Path:
    root.mkdir()
    (root / "extension.py").write_text(
        source
        or """
from homemaster.extensions import ExtensionContributions, HookEvent, HookSpec

async def on_run(context):
    return {"ok": True, "output": context.payload.get("prompt", "")}

def build_extension(context):
    return ExtensionContributions(hooks=(HookSpec(
        extension_id=context.extension_id,
        hook_id="run_audit",
        event=HookEvent.RUN_START,
        callback=on_run,
        required_capability="hook.lifecycle",
        priority=10,
    ),))
""".lstrip(),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "extension_id": extension_id,
        "version": "1.0.0",
        "requested_capabilities": list(requested),
        "entrypoint": "extension.py",
        "dependencies": list(dependencies),
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _approval(
    manifest: Path,
    *,
    extension_id: str = "example.audit",
    grants: tuple[str, ...] = ("hook.lifecycle",),
    enabled_tool_ids: tuple[str, ...] = (),
) -> ExtensionApproval:
    return ExtensionApproval(
        manifest_path=manifest,
        extension_id=extension_id,
        version="1.0.0",
        expected_sha256=extension_content_sha256(manifest),
        granted_capabilities=grants,
        enabled_tool_ids=enabled_tool_ids,
    )


def test_loader_verifies_canonical_bytes_and_executes_the_same_source(tmp_path) -> None:
    manifest = _write_extension(tmp_path / "extension")
    approval = _approval(manifest)

    generation = load_extension_generation((approval,), generation=4)

    assert generation.generation == 4
    assert generation.extensions[0].content_sha256 == approval.expected_sha256
    assert generation.hooks[0].hook_id == "run_audit"
    assert generation.tools == ()

    (manifest.parent / "extension.py").write_text("raise RuntimeError('changed')\n")
    with pytest.raises(ExtensionLoadError, match="failed validation") as raised:
        load_extension_generation((approval,))
    assert "content SHA-256" in raised.value.diagnostics[0]


@pytest.mark.asyncio
async def test_loader_compiles_the_verified_bytes_even_if_path_is_replaced(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _write_extension(tmp_path / "extension")
    approval = _approval(manifest)
    original_read = extension_loader._read_regular_file_nofollow

    def read_then_replace(path: Path, **kwargs) -> bytes:
        content = original_read(path, **kwargs)
        if Path(path).name == "extension.py":
            replacement_path = Path(path)
            if not replacement_path.is_absolute():
                replacement_path = manifest.parent / replacement_path
            replacement_path.write_text("raise RuntimeError('replacement executed')\n")
        return content

    monkeypatch.setattr(
        extension_loader,
        "_read_regular_file_nofollow",
        read_then_replace,
    )
    generation = load_extension_generation((approval,))
    result = await HookRunner(generation).execute(
        HookEvent.RUN_START,
        {"prompt": "verified bytes"},
        principal_capabilities=("hook.lifecycle",),
    )

    assert result.results[0].success is True
    assert result.results[0].output == "verified bytes"


@pytest.mark.asyncio
async def test_declared_dependency_is_digested_and_executed_from_verified_bytes(
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "dependency"
    manifest = _write_extension(
        root,
        dependencies=("helper.py",),
        source="""
import helper
from homemaster.extensions import ExtensionContributions, HookEvent, HookSpec

async def callback(context):
    return helper.VALUE

def build_extension(context):
    return ExtensionContributions(hooks=(HookSpec(
        context.extension_id, "dependency", HookEvent.RUN_START, callback, "hook.lifecycle"
    ),))
""".lstrip(),
    )
    helper = root / "helper.py"
    helper.write_text("VALUE = 'verified dependency'\n", encoding="utf-8")
    approval = _approval(manifest)
    original_read = extension_loader._read_regular_file_nofollow

    def read_then_replace(path: Path, **kwargs) -> bytes:
        content = original_read(path, **kwargs)
        if Path(path).name == "helper.py":
            helper.write_text("VALUE = 'replacement'\n", encoding="utf-8")
        return content

    monkeypatch.setattr(extension_loader, "_read_regular_file_nofollow", read_then_replace)
    generation = load_extension_generation((approval,))
    result = await HookRunner(generation).execute(
        HookEvent.RUN_START,
        {},
        principal_capabilities=("hook.lifecycle",),
    )

    assert result.results[0].output == "verified dependency"
    assert generation.extensions[0].content_sha256 == approval.expected_sha256


def test_dependency_change_invalidates_extension_digest(tmp_path) -> None:
    root = tmp_path / "digest-dependency"
    manifest = _write_extension(root, dependencies=("helper.py",))
    helper = root / "helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    first = extension_content_sha256(manifest)

    helper.write_text("VALUE = 2\n", encoding="utf-8")

    assert extension_content_sha256(manifest) != first


def test_undeclared_local_import_fails_before_extension_factory(tmp_path) -> None:
    root = tmp_path / "undeclared-dependency"
    manifest = _write_extension(
        root,
        source=(
            "import helper\n\ndef build_extension(context):\n"
            "    raise AssertionError('unreachable')\n"
        ),
    )
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ExtensionLoadError) as raised:
        load_extension_generation((_approval(manifest),))

    assert "undeclared local extension dependency" in raised.value.diagnostics[0]


@pytest.mark.asyncio
async def test_extension_file_name_does_not_expose_approved_directory(tmp_path) -> None:
    manifest = _write_extension(
        tmp_path / "hidden-root",
        source="""
from homemaster.extensions import ExtensionContributions, HookEvent, HookSpec
ENTRY_FILE = __file__

async def callback(context):
    return ENTRY_FILE

def build_extension(context):
    return ExtensionContributions(hooks=(HookSpec(
        context.extension_id, "file_name", HookEvent.RUN_START, callback, "hook.lifecycle"
    ),))
""".lstrip(),
    )
    result = await HookRunner(load_extension_generation((_approval(manifest),))).execute(
        HookEvent.RUN_START,
        {},
        principal_capabilities=("hook.lifecycle",),
    )

    assert result.results[0].output.startswith("<homemaster-extension:")
    assert str(tmp_path) not in result.results[0].output


def test_plugin_tool_is_content_bound_capability_typed_and_atomically_registered(
    tmp_path,
) -> None:
    manifest = _write_extension(
        tmp_path / "tool-extension",
        requested=("tool.register", "extension.audit.read"),
        source="""
from homemaster.extensions import ExtensionContributions
from homemaster.tools.contracts import (
    ExecutionBackend, RegisteredTool, ToolDefinition, ToolExecutionResult,
    ToolExecutionStatus, ToolProvenance, VerificationPolicy,
)

class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data={"ok": True})

def build_extension(context):
    definition = ToolDefinition(
        internal_id="plugin.audit.query.v1",
        model_alias="plugin_audit_query",
        description="Query extension audit state.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="plugin", reference=context.provenance_reference),
        version="1.0.0",
        execution_backend=ExecutionBackend.PLUGIN,
        required_capabilities=("extension.audit.read",),
    )
    return ExtensionContributions(tools=(RegisteredTool(definition, Executor()),))
""".lstrip(),
    )
    approval = _approval(
        manifest,
        grants=("tool.register", "extension.audit.read"),
        enabled_tool_ids=("plugin.audit.query.v1",),
    )

    generation = load_extension_generation((approval,))
    registry = ToolRegistry()
    registered = register_extension_tools_atomically(registry, generation)

    assert registered == ("plugin_audit_query",)
    registered_tool = registry.get(registered[0])
    assert registered_tool is not None
    definition = generation.tools[0].definition
    assert definition.execution_backend is ExecutionBackend.PLUGIN
    assert definition.required_capabilities == ("extension.audit.read",)
    assert definition.provenance.reference == (
        f"extension:example.audit@1.0.0#sha256:{approval.expected_sha256}"
    )

    missing_capability_source = (
        (manifest.parent / "extension.py")
        .read_text(encoding="utf-8")
        .replace(
            'required_capabilities=("extension.audit.read",)',
            "required_capabilities=()",
        )
    )
    invalid_manifest = _write_extension(
        tmp_path / "tool-without-capability",
        requested=("tool.register", "extension.audit.read"),
        source=missing_capability_source,
    )
    with pytest.raises(ExtensionLoadError) as raised:
        load_extension_generation(
            (
                _approval(
                    invalid_manifest,
                    grants=("tool.register", "extension.audit.read"),
                    enabled_tool_ids=("plugin.audit.query.v1",),
                ),
            )
        )
    assert "must declare required_capabilities" in raised.value.diagnostics[0]


@pytest.mark.asyncio
async def test_home_composition_enables_only_approved_extension_tools(tmp_path) -> None:
    manifest = _write_extension(
        tmp_path / "home-extension",
        requested=("tool.register", "extension.audit.read"),
        source="""
from homemaster.extensions import ExtensionContributions
from homemaster.tools.contracts import (
    ExecutionBackend, RegisteredTool, ToolDefinition, ToolExecutionResult,
    ToolExecutionStatus, ToolProvenance, VerificationPolicy,
)

class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data={"ok": True})

def build_extension(context):
    definition = ToolDefinition(
        internal_id="plugin.audit.query.v1",
        model_alias="plugin_audit_query",
        description="Query extension audit state.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="plugin", reference=context.provenance_reference),
        version="1.0.0",
        execution_backend=ExecutionBackend.PLUGIN,
        required_capabilities=("extension.audit.read",),
    )
    return ExtensionContributions(tools=(RegisteredTool(definition, Executor()),))
""".lstrip(),
    )
    digest = extension_content_sha256(manifest)
    config = HomeMasterConfig(
        runtime=RuntimeGuardConfig(runtime_root=tmp_path / "runs"),
        skills=SkillSourcesConfig(
            user_dirs=(),
            project_dirs=(),
            explicit_dirs=(),
            allow_project=False,
        ),
        extensions=ExtensionsConfig(
            approvals=(
                ExtensionApprovalConfig(
                    manifest_path=manifest,
                    extension_id="example.audit",
                    version="1.0.0",
                    expected_sha256=digest,
                    granted_capabilities=("tool.register", "extension.audit.read"),
                    enabled_tool_ids=("plugin.audit.query.v1",),
                ),
            )
        ),
    )

    bundle = create_home_application(config=config, run_label="extension-composition")
    try:
        assert bundle.extension_runner is not None
        assert bundle.extension_reloader is not None
        assert bundle.application.registry.get("plugin_audit_query") is not None
    finally:
        await bundle.application.aclose()


def test_loader_rejects_symlinks_duplicate_ids_and_undeclared_grants(tmp_path) -> None:
    manifest = _write_extension(tmp_path / "extension")
    linked = manifest.parent / "linked.py"
    linked.symlink_to(manifest.parent / "extension.py")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entrypoint"] = "linked.py"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="securely open"):
        extension_content_sha256(manifest)

    manifest = _write_extension(tmp_path / "second")
    approval = _approval(manifest)
    with pytest.raises(ExtensionLoadError, match="failed validation"):
        load_extension_generation((approval, approval))

    with pytest.raises(ExtensionLoadError) as raised:
        load_extension_generation(
            (replace(approval, granted_capabilities=("hook.lifecycle", "device.control")),)
        )
    assert "undeclared capabilities" in raised.value.diagnostics[0]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "schema_version"),
        ("version", "v1", "semantic"),
    ],
)
def test_loader_rejects_unsupported_manifest_schema_and_version(
    tmp_path,
    field,
    value,
    message,
) -> None:
    manifest = _write_extension(tmp_path / field)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[field] = value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        extension_content_sha256(manifest)


def test_loader_rejects_symlinked_entrypoint_directory(tmp_path) -> None:
    root = tmp_path / "nested-symlink"
    manifest = _write_extension(root)
    nested = root / "nested"
    nested.mkdir()
    (nested / "extension.py").write_bytes((root / "extension.py").read_bytes())
    (root / "linked").symlink_to(nested, target_is_directory=True)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entrypoint"] = "linked/extension.py"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="securely open"):
        extension_content_sha256(manifest)


def test_failed_generation_and_catalog_collision_cleanup_loaded_resources(tmp_path) -> None:
    cleaned_valid = tmp_path / "valid-cleaned"
    valid = _write_extension(
        tmp_path / "valid",
        source=f"""
from pathlib import Path
from homemaster.extensions import ExtensionContributions

async def cleanup():
    Path({str(cleaned_valid)!r}).write_text("clean", encoding="utf-8")

def build_extension(context):
    return ExtensionContributions(cleanup=cleanup)
""".lstrip(),
    )
    invalid = _write_extension(tmp_path / "invalid")
    invalid_approval = replace(_approval(invalid), expected_sha256="0" * 64)

    with pytest.raises(ExtensionLoadError):
        load_extension_generation((_approval(valid), invalid_approval))
    assert cleaned_valid.read_text(encoding="utf-8") == "clean"

    cleaned_invalid = tmp_path / "invalid-contribution-cleaned"
    invalid_contribution = _write_extension(
        tmp_path / "invalid-contribution",
        source=f"""
from pathlib import Path
from homemaster.extensions import ExtensionContributions, HookEvent, HookSpec

async def callback(context):
    return True

async def cleanup():
    Path({str(cleaned_invalid)!r}).write_text("clean", encoding="utf-8")

def build_extension(context):
    return ExtensionContributions(
        hooks=(HookSpec(
            "other.extension",
            "audit",
            HookEvent.RUN_START,
            callback,
            "hook.lifecycle",
        ),),
        cleanup=cleanup,
    )
""".lstrip(),
    )
    with pytest.raises(ExtensionLoadError):
        load_extension_generation((_approval(invalid_contribution),))
    assert cleaned_invalid.read_text(encoding="utf-8") == "clean"

    cleaned_collision = tmp_path / "collision-cleaned"
    collision = _write_extension(
        tmp_path / "collision",
        requested=("tool.register", "extension.audit.read"),
        source=f"""
from pathlib import Path
from homemaster.extensions import ExtensionContributions
from homemaster.tools.contracts import (
    ExecutionBackend, RegisteredTool, ToolDefinition, ToolExecutionResult,
    ToolExecutionStatus, ToolProvenance, VerificationPolicy,
)

class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data={{"ok": True}})

async def cleanup():
    Path({str(cleaned_collision)!r}).write_text("clean", encoding="utf-8")

def build_extension(context):
    definition = ToolDefinition(
        internal_id="plugin.observe.v1",
        model_alias="observe",
        description="Collide with a built-in tool.",
        input_schema={{"type": "object"}},
        output_schema={{"type": "object"}},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="plugin", reference=context.provenance_reference),
        version="1.0.0",
        execution_backend=ExecutionBackend.PLUGIN,
        required_capabilities=("extension.audit.read",),
    )
    return ExtensionContributions(
        tools=(RegisteredTool(definition, Executor()),),
        cleanup=cleanup,
    )
""".lstrip(),
    )
    collision_config = HomeMasterConfig(
        runtime=RuntimeGuardConfig(runtime_root=tmp_path / "runs"),
        skills=SkillSourcesConfig(
            user_dirs=(),
            project_dirs=(),
            explicit_dirs=(),
            allow_project=False,
        ),
        extensions=ExtensionsConfig(
            approvals=(
                ExtensionApprovalConfig(
                    manifest_path=collision,
                    extension_id="example.audit",
                    version="1.0.0",
                    expected_sha256=extension_content_sha256(collision),
                    granted_capabilities=("tool.register", "extension.audit.read"),
                    enabled_tool_ids=("plugin.observe.v1",),
                ),
            )
        ),
    )
    with pytest.raises(ToolRegistryError, match="duplicate tool name 'observe'"):
        create_home_application(config=collision_config, run_label="collision")
    assert cleaned_collision.read_text(encoding="utf-8") == "clean"


def test_composition_rolls_back_extension_when_later_skill_build_fails(
    monkeypatch,
    tmp_path,
) -> None:
    cleaned = tmp_path / "composition-cleaned"
    manifest = _write_extension(
        tmp_path / "composition-rollback",
        source=f"""
from pathlib import Path
from homemaster.extensions import ExtensionContributions

async def cleanup():
    Path({str(cleaned)!r}).write_text("clean", encoding="utf-8")

def build_extension(context):
    return ExtensionContributions(cleanup=cleanup)
""".lstrip(),
    )
    config = HomeMasterConfig(
        runtime=RuntimeGuardConfig(runtime_root=tmp_path / "runs"),
        skills=SkillSourcesConfig(
            user_dirs=(), project_dirs=(), explicit_dirs=(), allow_project=False
        ),
        extensions=ExtensionsConfig(
            approvals=(
                ExtensionApprovalConfig(
                    manifest_path=manifest,
                    extension_id="example.audit",
                    version="1.0.0",
                    expected_sha256=extension_content_sha256(manifest),
                ),
            )
        ),
    )

    def fail_skills(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("skill build failed")

    monkeypatch.setattr(composition, "load_home_skills", fail_skills)
    with pytest.raises(RuntimeError, match="skill build failed"):
        create_home_application(config=config, run_label="composition-rollback")

    assert cleaned.read_text(encoding="utf-8") == "clean"


@pytest.mark.parametrize(
    "control_error",
    [KeyboardInterrupt(), SystemExit(), asyncio.CancelledError()],
)
def test_loader_does_not_swallow_process_control_exceptions(
    monkeypatch,
    tmp_path,
    control_error,
) -> None:
    manifest = _write_extension(tmp_path / "control")

    def interrupt(_approval):
        raise control_error

    monkeypatch.setattr(extension_loader, "_load_one", interrupt)
    with pytest.raises(type(control_error)):
        load_extension_generation((_approval(manifest),))


def test_hook_spec_rejects_sync_callbacks() -> None:
    def sync_callback(context: HookContext) -> None:
        del context

    with pytest.raises(TypeError, match="async"):
        HookSpec(
            extension_id="example.audit",
            hook_id="sync",
            event=HookEvent.RUN_START,
            callback=sync_callback,  # type: ignore[arg-type]
            required_capability="hook.lifecycle",
        )


@pytest.mark.asyncio
async def test_runner_orders_matches_blocks_and_checks_run_capabilities() -> None:
    calls: list[str] = []

    async def low(context: HookContext) -> bool:
        calls.append("low")
        return True

    async def high(context: HookContext) -> bool:
        calls.append("high")
        return False

    hooks = (
        HookSpec(
            "example.audit",
            "low",
            HookEvent.RUN_START,
            low,
            "hook.lifecycle",
            priority=1,
            matcher="home*",
        ),
        HookSpec(
            "example.audit",
            "high",
            HookEvent.RUN_START,
            high,
            "hook.lifecycle",
            priority=10,
            matcher="home*",
            block_on_failure=True,
        ),
    )
    runner = HookRunner(_generation(hooks))

    denied = await runner.execute(
        HookEvent.RUN_START,
        {"prompt": "home request"},
        principal_capabilities=(),
    )
    assert len(denied.results) == 2
    assert denied.blocked is True
    assert "lacks required hook capability" in denied.reason

    result = await runner.execute(
        HookEvent.RUN_START,
        {"prompt": "home request"},
        principal_capabilities=("hook.lifecycle",),
    )
    assert calls == ["high", "low"]
    assert result.blocked is True
    assert result.results[0].success is False


@pytest.mark.asyncio
async def test_hook_result_free_text_is_redacted() -> None:
    async def leaking(context: HookContext) -> dict[str, object]:
        del context
        return {
            "ok": False,
            "reason": "token=secret-value /hpc2hdd/home/private/file",
            "output": "https://example.test/path?api_key=secret-value",
        }

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "leaking",
                    HookEvent.RUN_START,
                    leaking,
                    "hook.lifecycle",
                ),
            )
        )
    )
    result = await runner.execute(
        HookEvent.RUN_START,
        {"event": "run_start"},
        principal_capabilities=("hook.lifecycle",),
    )
    encoded = repr(result.results[0])
    assert "token=secret-value /hpc2hdd/home/private/file" in encoded
    assert "https://example.test/path?api_key=secret-value" in encoded


@pytest.mark.asyncio
async def test_runner_timeout_is_cooperative_and_reload_is_busy(tmp_path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def waiting(context: HookContext) -> None:
        entered.set()
        await release.wait()

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "waiting",
                    HookEvent.RUN_START,
                    waiting,
                    "hook.lifecycle",
                    timeout_s=1,
                ),
            )
        )
    )
    task = asyncio.create_task(
        runner.execute(
            HookEvent.RUN_START,
            {"event": "run_start"},
            principal_capabilities=("hook.lifecycle",),
        )
    )
    await entered.wait()
    manifest = _write_extension(tmp_path / "extension")
    busy = await ExtensionReloader(runner).reload((_approval(manifest),))
    assert busy.status is ReloadStatus.BUSY
    assert runner.generation.generation == 1
    release.set()
    await task

    async def slow(context: HookContext) -> None:
        await asyncio.sleep(1)

    timeout_runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "slow",
                    HookEvent.RUN_START,
                    slow,
                    "hook.lifecycle",
                    timeout_s=0.001,
                    block_on_failure=True,
                ),
            )
        )
    )
    result = await timeout_runner.execute(
        HookEvent.RUN_START,
        {"event": "run_start"},
        principal_capabilities=("hook.lifecycle",),
    )
    assert result.results[0].timed_out is True
    assert result.blocked is True


@pytest.mark.asyncio
async def test_cancellation_resistant_timeout_is_fenced_and_keeps_reload_busy(tmp_path) -> None:
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def resistant(context: HookContext) -> bool:
        del context
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return True

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "resistant",
                    HookEvent.RUN_START,
                    resistant,
                    "hook.lifecycle",
                    timeout_s=0.001,
                ),
            )
        )
    )

    result = await asyncio.wait_for(
        runner.execute(
            HookEvent.RUN_START,
            {"event": "run_start"},
            principal_capabilities=("hook.lifecycle",),
        ),
        timeout=0.1,
    )
    await cancelled.wait()
    manifest = _write_extension(tmp_path / "reload")
    busy = await ExtensionReloader(runner).reload((_approval(manifest),))

    assert result.results[0].timed_out is True
    assert result.results[0].success is False
    assert runner.active_callbacks == 1
    assert busy.status is ReloadStatus.BUSY
    release.set()
    await asyncio.sleep(0)
    assert runner.active_callbacks == 0


@pytest.mark.asyncio
async def test_hook_cancellation_propagates_and_releases_active_count() -> None:
    entered = asyncio.Event()

    async def waiting(context: HookContext) -> None:
        del context
        entered.set()
        await asyncio.Event().wait()

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "waiting",
                    HookEvent.RUN_START,
                    waiting,
                    "hook.lifecycle",
                ),
            )
        )
    )
    task = asyncio.create_task(
        runner.execute(
            HookEvent.RUN_START,
            {"event": "run_start"},
            principal_capabilities=("hook.lifecycle",),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner.active_callbacks == 0

    async def cancelled(context: HookContext) -> None:
        del context
        raise asyncio.CancelledError

    cleanup_runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "cancelled",
                    HookEvent.RUN_END,
                    cancelled,
                    "hook.lifecycle",
                ),
            )
        )
    )
    result = await cleanup_runner.execute(
        HookEvent.RUN_END,
        {"event": "run_end"},
        principal_capabilities=("hook.lifecycle",),
        best_effort=True,
    )
    assert result.results[0].success is False
    assert "best-effort" in result.results[0].reason


@pytest.mark.asyncio
async def test_hooks_only_reload_swaps_generation_and_failed_reload_rolls_back(tmp_path) -> None:
    manifest = _write_extension(tmp_path / "extension")
    approval = _approval(manifest)
    runner = HookRunner(load_extension_generation((approval,), generation=7))
    reloader = ExtensionReloader(runner)

    success = await reloader.reload((approval,))
    assert success.status is ReloadStatus.RELOADED
    assert runner.generation.generation == 8

    failed = await reloader.reload((replace(approval, expected_sha256="0" * 64),))
    assert failed.status is ReloadStatus.FAILED
    assert runner.generation.generation == 8


@pytest.mark.asyncio
async def test_reload_awaits_partial_candidate_cleanup_before_returning_failure(tmp_path) -> None:
    current_manifest = _write_extension(tmp_path / "reload-current")
    runner = HookRunner(load_extension_generation((_approval(current_manifest),), generation=1))
    cleaned = tmp_path / "reload-candidate-cleaned"
    invalid = _write_extension(
        tmp_path / "reload-invalid",
        source=f"""
from pathlib import Path
from homemaster.extensions import ExtensionContributions, HookEvent, HookSpec

async def callback(context):
    return True

async def cleanup():
    Path({str(cleaned)!r}).write_text("clean", encoding="utf-8")

def build_extension(context):
    return ExtensionContributions(
        hooks=(HookSpec(
            "other.extension", "invalid", HookEvent.RUN_START, callback, "hook.lifecycle"
        ),),
        cleanup=cleanup,
    )
""".lstrip(),
    )

    result = await ExtensionReloader(runner).reload((_approval(invalid),))

    assert result.status is ReloadStatus.FAILED
    assert cleaned.read_text(encoding="utf-8") == "clean"
    assert runner.generation.generation == 1


@pytest.mark.asyncio
async def test_reload_rejects_manifest_version_and_capability_changes(tmp_path) -> None:
    manifest = _write_extension(tmp_path / "extension")
    approval = _approval(manifest)
    runner = HookRunner(load_extension_generation((approval,), generation=1))

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["version"] = "2.0.0"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    version_approval = replace(
        approval,
        version="2.0.0",
        expected_sha256=extension_content_sha256(manifest),
    )
    version_result = await ExtensionReloader(runner).reload((version_approval,))
    assert version_result.status is ReloadStatus.RESTART_REQUIRED

    payload["version"] = "1.0.0"
    payload["requested_capabilities"].append("extension.audit.read")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    grant_approval = replace(
        approval,
        expected_sha256=extension_content_sha256(manifest),
        granted_capabilities=("hook.lifecycle", "extension.audit.read"),
    )
    grant_result = await ExtensionReloader(runner).reload((grant_approval,))
    assert grant_result.status is ReloadStatus.RESTART_REQUIRED
    assert runner.generation.generation == 1


@pytest.mark.asyncio
async def test_closed_runner_rejects_reload_and_cleanup_waits_for_active_callback(tmp_path) -> None:
    events: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def callback(context: HookContext) -> None:
        del context
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        finally:
            events.append("callback-ended")

    async def cleanup() -> None:
        events.append("cleanup")

    hook = HookSpec(
        "example.audit",
        "active",
        HookEvent.RUN_START,
        callback,
        "hook.lifecycle",
    )
    extension = extension_loader.LoadedExtension(
        manifest=extension_loader.ExtensionManifest(
            1,
            "example.audit",
            "1.0.0",
            ("hook.lifecycle",),
            "extension.py",
        ),
        root=tmp_path,
        content_sha256="0" * 64,
        granted_capabilities=("hook.lifecycle",),
        enabled_tool_ids=(),
        contributions=extension_loader.ExtensionContributions(
            hooks=(hook,),
            cleanup=cleanup,
        ),
    )
    runner = HookRunner(replace(_generation((hook,)), extensions=(extension,)))
    task = asyncio.create_task(
        runner.execute(
            HookEvent.RUN_START,
            {"event": "run_start"},
            principal_capabilities=("hook.lifecycle",),
        )
    )
    await entered.wait()
    diagnostics = await runner.aclose()
    assert diagnostics
    assert "cleanup" not in events
    release.set()
    await task
    await runner.aclose()
    assert events == ["callback-ended", "cleanup"]

    manifest = _write_extension(tmp_path / "closed-reload")
    result = await ExtensionReloader(runner).reload((_approval(manifest),))
    assert result.status is ReloadStatus.FAILED


@pytest.mark.asyncio
async def test_stale_hook_result_is_not_published() -> None:
    release = asyncio.Event()

    async def callback(context: HookContext) -> str:
        del context
        await release.wait()
        return "stale output"

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "stale",
                    HookEvent.RUN_START,
                    callback,
                    "hook.lifecycle",
                ),
            )
        )
    )
    task = asyncio.create_task(
        runner.execute(
            HookEvent.RUN_START,
            {"event": "run_start"},
            principal_capabilities=("hook.lifecycle",),
        )
    )
    await asyncio.sleep(0)
    runner._generation = replace(runner.generation, generation=2)  # type: ignore[attr-defined]
    release.set()
    result = await task

    assert result.results[0].stale_generation is True
    assert result.results[0].output == ""


@pytest.mark.asyncio
async def test_plugin_tool_byte_change_requires_application_restart(tmp_path) -> None:
    source = """
from homemaster.extensions import ExtensionContributions
from homemaster.tools.contracts import (
    ExecutionBackend, RegisteredTool, ToolDefinition, ToolExecutionResult,
    ToolExecutionStatus, ToolProvenance, VerificationPolicy,
)

class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, data={"ok": True})

def build_extension(context):
    definition = ToolDefinition(
        internal_id="plugin.audit.query.v1",
        model_alias="plugin_audit_query",
        description="Query extension audit state.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="plugin", reference=context.provenance_reference),
        version="1.0.0",
        execution_backend=ExecutionBackend.PLUGIN,
        required_capabilities=("extension.audit.read",),
    )
    return ExtensionContributions(tools=(RegisteredTool(definition, Executor()),))
""".lstrip()
    manifest = _write_extension(
        tmp_path / "tool-extension",
        requested=("tool.register", "extension.audit.read"),
        source=source,
    )
    approval = _approval(
        manifest,
        grants=("tool.register", "extension.audit.read"),
        enabled_tool_ids=("plugin.audit.query.v1",),
    )
    runner = HookRunner(load_extension_generation((approval,), generation=3))
    (manifest.parent / "extension.py").write_text(source + "\n", encoding="utf-8")
    changed_approval = replace(
        approval,
        expected_sha256=extension_content_sha256(manifest),
    )

    result = await ExtensionReloader(runner).reload((changed_approval,))

    assert result.status is ReloadStatus.RESTART_REQUIRED
    assert runner.generation.generation == 3


def test_canonical_tool_required_capabilities_are_snapshotted_and_enforced() -> None:
    definition = ToolDefinition(
        internal_id="plugin.audit.query.v1",
        model_alias="plugin_audit_query",
        description="Query an approved extension.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="plugin", reference="extension:test"),
        version="1.0.0",
        execution_backend=ExecutionBackend.PLUGIN,
        required_capabilities=("extension.audit.read",),
    )
    assert definition.to_dict()["required_capabilities"] == ["extension.audit.read"]
    policy = PermissionChecker(PermissionSettingsConfig())
    context = ToolExecutionContext(
        Path.cwd(),
        metadata={
            "permission_subject": PermissionSubject(
                "principal",
                "gateway",
                capabilities=("tool.read",),
            ),
        },
    )
    denied = policy.evaluate_tool(
        tool_name=definition.model_alias,
        is_read_only=True,
        required_capabilities=("tool.read", "extension.audit.read"),
        arguments={},
        context=context,
    )
    assert denied.allowed is False
    assert "extension.audit.read" in denied.reason
    allowed_context = ToolExecutionContext(
        Path.cwd(),
        metadata={
            "permission_subject": replace(
                context.metadata["permission_subject"],
                capabilities=("tool.read", "extension.audit.read"),
            ),
        },
    )
    assert policy.evaluate_tool(
        tool_name=definition.model_alias,
        is_read_only=True,
        required_capabilities=("tool.read", "extension.audit.read"),
        arguments={},
        context=allowed_context,
    ).allowed is True

    exact_only_context = ToolExecutionContext(
        Path.cwd(),
        metadata={
            "permission_subject": replace(
                context.metadata["permission_subject"],
                capabilities=("tool:plugin.audit.query.v1",),
            ),
        },
    )
    exact_denied = policy.evaluate_tool(
        tool_name=definition.model_alias,
        is_read_only=True,
        required_capabilities=("tool.read", "extension.audit.read"),
        arguments={},
        context=exact_only_context,
    )
    assert exact_denied.allowed is False
    assert "extension.audit.read" in exact_denied.reason


@pytest.mark.asyncio
async def test_exact_hook_token_does_not_replace_required_capability() -> None:
    async def callback(context: HookContext) -> bool:
        del context
        return True

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "audit",
                    HookEvent.RUN_START,
                    callback,
                    "hook.lifecycle",
                    block_on_failure=True,
                ),
            )
        )
    )
    result = await runner.execute(
        HookEvent.RUN_START,
        {"event": "run_start"},
        principal_capabilities=("hook:example.audit.audit",),
    )
    assert result.blocked is True
    assert "hook.lifecycle" in result.reason


@pytest.mark.asyncio
async def test_hook_order_has_stable_ties_and_non_matches_are_skipped() -> None:
    calls: list[str] = []

    async def first(context: HookContext) -> None:
        del context
        calls.append("first")

    async def second(context: HookContext) -> None:
        del context
        calls.append("second")

    async def skipped(context: HookContext) -> None:
        del context
        calls.append("skipped")

    runner = HookRunner(
        _generation(
            (
                HookSpec(
                    "example.audit",
                    "first",
                    HookEvent.RUN_START,
                    first,
                    "hook.lifecycle",
                    priority=5,
                    matcher="home*",
                ),
                HookSpec(
                    "example.audit",
                    "second",
                    HookEvent.RUN_START,
                    second,
                    "hook.lifecycle",
                    priority=5,
                    matcher="home*",
                ),
                HookSpec(
                    "example.audit",
                    "skip",
                    HookEvent.RUN_START,
                    skipped,
                    "hook.lifecycle",
                    priority=10,
                    matcher="other*",
                ),
            )
        )
    )
    await runner.execute(
        HookEvent.RUN_START,
        {"prompt": "home request"},
        principal_capabilities=("hook.lifecycle",),
    )
    assert calls == ["first", "second"]


def _generation(hooks: tuple[HookSpec, ...]) -> ExtensionGeneration:
    return ExtensionGeneration(
        generation=1,
        extensions=(),
        hooks=hooks,
        tools=(),
        enabled_tool_ids=(),
        tool_plane_digest="0" * 64,
    )
