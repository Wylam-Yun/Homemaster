#!/usr/bin/env python3
"""Run every configured Gateway smoke trial through the live THOR worker.

This is intentionally a standalone black-box gate: it does not require pytest and
writes one evidence JSON per manifest entry, including worker lifecycle receipts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from homemaster.application.resources import RunResourceScope
from homemaster.config import AlfworldGatewayConfig
from homemaster.gateway.alfworld import create_alfworld_gateway_binding


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return value


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:120]


def _last_external_return_code(adapter: Any) -> int | None:
    for item in reversed(adapter.request_history):
        if item.get("operation") == "act":
            value = item.get("external_return_code")
            return int(value) if isinstance(value, int) else None
    return None


async def _run_trial(
    *,
    repo: Path,
    asset_root: Path,
    python_executable: Path,
    manifest: Path,
    index: int,
    display: str,
    output_root: Path,
) -> tuple[dict[str, Any], bool]:
    scope = RunResourceScope()
    binding = None
    close_error: str | None = None
    actions: list[dict[str, Any]] = []
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        binding, _owner = await create_alfworld_gateway_binding(
            AlfworldGatewayConfig(
                asset_root=asset_root,
                data_root=asset_root / "data",
                config_path=asset_root / "configs" / "base_config.yaml",
                python_executable=python_executable,
                trial_manifest=manifest,
                trial_index=index,
                display=display,
                manage_xvfb=True,
                allow_offscreen_object_navigation=False,
            ),
            run_dir=output_root,
            resource_scope=scope,
        )
        adapter = binding.adapter
        initial_state = adapter.raw_state
        actions.append({"name": "reset", "state": initial_state, "step": adapter.current_state.step_index})
        # The configured smoke trial starts with the target inside an initially
        # hidden Drawer. Navigation makes the target actionable without using
        # runtime object enumeration or off-screen navigation.
        navigation_before = adapter.raw_state
        navigation = adapter.go_to_target(
            "Drawer", tool_name="robot_go_to", tool_args={"target": "Drawer"}
        )
        actions.append(
            {
                "name": "navigate_drawer",
                "before": navigation_before,
                "after": adapter.raw_state,
                "result": _jsonable(navigation),
                "external_return_code": _last_external_return_code(adapter),
            }
        )
        manipulation_before = adapter.raw_state
        manipulation = adapter.manipulate_with_thor(
            action="take",
            tool_name="robot_manipulate",
            tool_args={"action": "take", "object": "SaltShaker", "source_receptacle": "Drawer"},
        )
        actions.append(
            {
                "name": "take_salt_shaker",
                "before": manipulation_before,
                "after": adapter.raw_state,
                "result": _jsonable(manipulation),
                "external_return_code": _last_external_return_code(adapter),
            }
        )
        selection = binding.selection
        trial_id = selection.trial_id
        evidence = {
            "schema": "homemaster-v35-gateway-smoke-live-v1",
            "trial_index": index,
            "trial_id": trial_id,
            "manifest_identity": _jsonable(selection),
            "worker_identity": _jsonable(adapter.runtime_identity),
            "worker_pid": adapter.worker_pid,
            "actions": actions,
            "terminal": {
                "won": adapter.current_state.won,
                "terminal_owner": "alfworld_thor",
                "raw_state": adapter.raw_state,
            },
            "request_history": list(adapter.request_history),
            "stderr_path": str(output_root / "alfworld" / "worker.log"),
        }
        success = (
            navigation.success
            and navigation.execution_feedback.state_read_status == "ok"
            and actions[-2]["external_return_code"] == 0
            and manipulation.success
            and manipulation.execution_feedback.state_read_status == "ok"
            and actions[-1]["external_return_code"] == 0
        )
    except Exception as exc:
        evidence = {
            "schema": "homemaster-v35-gateway-smoke-live-v1",
            "trial_index": index,
            "error": f"{type(exc).__name__}: {exc}",
            "actions": actions,
        }
        success = False
    finally:
        try:
            await scope.aclose()
        except Exception as exc:
            close_error = f"{type(exc).__name__}: {exc}"
            success = False
    if binding is not None:
        process = binding.adapter._process
        log_path = output_root / "alfworld" / "worker.log"
        stderr = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        close_request = next(
            (item for item in reversed(binding.adapter.request_history) if item.get("operation") == "close"),
            None,
        )
        evidence.update(
            {
                "close": _jsonable(close_request),
                "close_error": close_error,
                "worker_exit_code": process.returncode,
                "worker_cleanup": {"exited": process.poll() is not None},
                "stderr": stderr,
            }
        )
        success = success and close_error is None and process.poll() is not None
        if "Traceback (most recent call last)" in stderr:
            success = False
        trial_id = evidence.get("trial_id", f"trial-{index}")
    else:
        evidence.update({"close_error": close_error, "worker_cleanup": {"exited": False}})
        trial_id = f"trial-{index}"
    evidence_path = output_root / f"{_slug(str(trial_id))}.json"
    evidence_path.write_text(json.dumps(_jsonable(evidence), ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    evidence["evidence_path"] = str(evidence_path)
    return evidence, success


async def _main(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    asset_root = Path(args.asset_root).resolve()
    python_executable = Path(args.python).resolve()
    manifest = Path(args.manifest).resolve()
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = manifest_data.get("entries") if isinstance(manifest_data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise SystemExit("gateway smoke manifest has no entries")
    output = Path(args.output).resolve()
    results = []
    passed = True
    for index in range(len(entries)):
        trial_output = output / f"trial-{index:03d}"
        evidence, result = await _run_trial(
            repo=repo,
            asset_root=asset_root,
            python_executable=python_executable,
            manifest=manifest,
            index=index,
            display=args.display,
            output_root=trial_output,
        )
        passed = passed and result
        results.append({"trial_index": index, "trial_id": evidence.get("trial_id"), "passed": result})
    summary = {"schema": "homemaster-v35-gateway-smoke-summary-v1", "entries": results, "passed": passed}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("HOMEMASTER_LIVE_REPO", Path.cwd()))
    parser.add_argument("--asset-root", default=os.environ.get("HOMEMASTER_LIVE_ALFWORLD_ROOT", ".runtime/alfworld"))
    parser.add_argument("--python", default=os.environ.get("HOMEMASTER_LIVE_ALFWORLD_PYTHON", ".runtime/alfworld-venv/bin/python"))
    parser.add_argument("--manifest", default=os.environ.get("HOMEMASTER_LIVE_TRIAL_MANIFEST", "tests/fixtures/alfworld/gateway_smoke_trials.json"))
    parser.add_argument("--output", default=os.environ.get("HOMEMASTER_GATEWAY_SMOKE_OUTPUT", "plan/V3.5/evidence/phase-5/gateway-smoke-live"))
    parser.add_argument("--display", default=os.environ.get("HOMEMASTER_LIVE_DISPLAY", ":107"))
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
