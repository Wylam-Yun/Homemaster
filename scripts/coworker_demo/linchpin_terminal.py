"""tmux/Bash/bubblewrap linchpin helpers and executable gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_EXIT_STATUS = re.compile(r"(?:0|[1-9][0-9]{0,2})\n")


def parse_exit_status(text: str) -> int:
    """Parse one complete POSIX-style command exit-status file."""

    if _EXIT_STATUS.fullmatch(text) is None:
        raise ValueError("exit status file must contain one decimal line")
    status = int(text.strip())
    if status > 255:
        raise ValueError("exit status is outside the supported 0..255 range")
    return status


TENANT_ID = "tenanttenanttenant000198"
ITEM_CODE = "read"
SPEC_CODE = "ext.read.type1"
EXTENSION_NAME = "read-ext"
CONFIG_RELATIVE_PATH = Path("service_layer/component/config/extension_item_mapping.json")
SANDBOX_CONFIG_PATH = Path("/opt/app") / CONFIG_RELATIVE_PATH
EXACT_COMMAND = f'grep -A 3 "{TENANT_ID}:{ITEM_CODE}" {SANDBOX_CONFIG_PATH}'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_bubblewrap_command(
    *,
    episode_app: Path,
    bash_executable: str,
    bubblewrap_executable: str,
) -> list[str]:
    """Build the single sandbox layout used by terminal linchpin instances."""

    return [
        bubblewrap_executable,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/opt",
        "--dir",
        "/opt/app",
        "--bind",
        str(episode_app),
        "/opt/app",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--",
        bash_executable,
        "-lc",
        EXACT_COMMAND,
    ]


def _run_command_instance(
    *,
    artifact_dir: Path,
    episode_app: Path,
    label: str,
    timeout_s: float,
    tmux_executable: str,
    bash_executable: str,
    bubblewrap_executable: str,
) -> dict[str, Any]:
    command_dir = artifact_dir / label
    command_dir.mkdir(parents=True)
    stdout_path = command_dir / "stdout"
    stderr_path = command_dir / "stderr"
    exit_path = command_dir / "exit"
    runner_path = command_dir / "runner.sh"
    evidence_id = f"terminal-{label}-{uuid.uuid4().hex}"
    tmux_session = f"coworker-l3-{label}-{uuid.uuid4().hex[:12]}"
    bubblewrap_command = build_bubblewrap_command(
        episode_app=episode_app,
        bash_executable=bash_executable,
        bubblewrap_executable=bubblewrap_executable,
    )
    runner = f"""#!/usr/bin/bash
set +e
{shlex.join(bubblewrap_command)} > {shlex.quote(str(stdout_path))} 2> {shlex.quote(str(stderr_path))}
status=$?
printf '%s\\n' "$status" > {shlex.quote(str(exit_path))}.tmp
mv {shlex.quote(str(exit_path))}.tmp {shlex.quote(str(exit_path))}
exit 0
"""
    runner_path.write_text(runner, encoding="utf-8")
    runner_path.chmod(0o700)

    new_session = subprocess.run(
        [tmux_executable, "new-session", "-d", "-s", tmux_session],
        text=True,
        capture_output=True,
        check=False,
    )
    set_remain = subprocess.run(
        [tmux_executable, "set-option", "-t", tmux_session, "remain-on-exit", "on"],
        text=True,
        capture_output=True,
        check=False,
    )
    send_keys = subprocess.run(
        [
            tmux_executable,
            "send-keys",
            "-t",
            tmux_session,
            shlex.join([bash_executable, str(runner_path)]),
            "Enter",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if new_session.returncode != 0 or set_remain.returncode != 0 or send_keys.returncode != 0:
        subprocess.run(
            [tmux_executable, "kill-session", "-t", tmux_session],
            text=True,
            capture_output=True,
            check=False,
        )
        raise RuntimeError(
            "tmux setup failed: "
            f"new={new_session.returncode}, remain={set_remain.returncode}, "
            f"send={send_keys.returncode}"
        )

    deadline = time.monotonic() + timeout_s
    while not exit_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    capture_pane = subprocess.run(
        [tmux_executable, "capture-pane", "-p", "-t", tmux_session],
        text=True,
        capture_output=True,
        check=False,
    )
    kill_session = subprocess.run(
        [tmux_executable, "kill-session", "-t", tmux_session],
        text=True,
        capture_output=True,
        check=False,
    )
    if not exit_path.exists():
        raise TimeoutError(f"terminal instance {label} did not publish an exit file")
    status = parse_exit_status(exit_path.read_text(encoding="utf-8"))
    stdout = stdout_path.read_text(encoding="utf-8")
    stderr = stderr_path.read_text(encoding="utf-8")
    return {
        "label": label,
        "evidence_id": evidence_id,
        "tmux_session": tmux_session,
        "original_command": EXACT_COMMAND,
        "bubblewrap_command": bubblewrap_command,
        "tmux_returncodes": {
            "new_session": new_session.returncode,
            "set_remain": set_remain.returncode,
            "send_keys": send_keys.returncode,
            "capture_pane": capture_pane.returncode,
            "kill_session": kill_session.returncode,
        },
        "pane": capture_pane.stdout,
        "exit_status": status,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
        "exit_sha256": _sha256(exit_path),
    }


def run_gate(args: argparse.Namespace) -> int:
    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    episode_app = artifact_dir / "environment/root/opt/app"
    config_path = episode_app / CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True)
    record_key = f"{TENANT_ID}:{ITEM_CODE}"
    initial_config = {
        record_key: {
            "SpecCode": SPEC_CODE,
            "ExtensionName": EXTENSION_NAME,
        }
    }
    _atomic_json(config_path, initial_config)
    initial_hash = _sha256(config_path)
    host_opt_app = Path("/opt/app")
    host_opt_before = host_opt_app.exists()
    result: dict[str, Any] = {
        "schema_version": 1,
        "pass": False,
        "artifact_dir": str(artifact_dir),
        "episode_config": str(config_path),
        "exact_command": EXACT_COMMAND,
        "initial_hash": initial_hash,
        "host_opt_app_before": host_opt_before,
    }
    try:
        add_grep = _run_command_instance(
            artifact_dir=artifact_dir,
            episode_app=episode_app,
            label="add-grep",
            timeout_s=args.timeout_s,
            tmux_executable=args.tmux_executable,
            bash_executable=args.bash_executable,
            bubblewrap_executable=args.bubblewrap_executable,
        )
        add_values = [TENANT_ID, ITEM_CODE, SPEC_CODE, EXTENSION_NAME]
        add_checks = {
            "exit_zero": add_grep["exit_status"] == 0,
            "all_values": all(value in add_grep["stdout"] for value in add_values),
            "tmux_returns": all(
                returncode == 0 for returncode in add_grep["tmux_returncodes"].values()
            ),
            "host_file_unchanged": _sha256(config_path) == initial_hash,
        }
        if not all(add_checks.values()):
            raise RuntimeError(f"add grep external checks failed: {add_checks}")

        host_payload = json.loads(config_path.read_text(encoding="utf-8"))
        removed = host_payload.pop(record_key)
        if removed != initial_config[record_key]:
            raise RuntimeError("host-side remove did not target the locked record")
        _atomic_json(config_path, host_payload)
        removed_hash = _sha256(config_path)
        if removed_hash == initial_hash:
            raise RuntimeError("host-side remove did not change the episode file")

        rollback_grep = _run_command_instance(
            artifact_dir=artifact_dir,
            episode_app=episode_app,
            label="rollback-grep",
            timeout_s=args.timeout_s,
            tmux_executable=args.tmux_executable,
            bash_executable=args.bash_executable,
            bubblewrap_executable=args.bubblewrap_executable,
        )
        final_payload = json.loads(config_path.read_text(encoding="utf-8"))
        rollback_checks = {
            "exit_one": rollback_grep["exit_status"] == 1,
            "stdout_empty": rollback_grep["stdout"] == "",
            "tmux_returns": all(
                returncode == 0 for returncode in rollback_grep["tmux_returncodes"].values()
            ),
            "host_file_readable": final_payload == {},
            "new_evidence_id": rollback_grep["evidence_id"] != add_grep["evidence_id"],
            "new_tmux_session": rollback_grep["tmux_session"] != add_grep["tmux_session"],
            "same_original_command": rollback_grep["original_command"]
            == add_grep["original_command"]
            == EXACT_COMMAND,
        }
        host_opt_after = host_opt_app.exists()
        checks = {
            "add_instance": all(add_checks.values()),
            "rollback_instance": all(rollback_checks.values()),
            "host_opt_unchanged": host_opt_after == host_opt_before,
            "episode_file_changed_only_after_remove": removed_hash != initial_hash,
        }
        result.update(
            {
                "add_grep": add_grep,
                "add_checks": add_checks,
                "removed_record": removed,
                "removed_hash": removed_hash,
                "rollback_grep": rollback_grep,
                "rollback_checks": rollback_checks,
                "final_payload": final_payload,
                "host_opt_app_after": host_opt_after,
                "checks": checks,
                "pass": all(checks.values()),
            }
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    _atomic_json(artifact_dir / "result.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["pass"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument("--tmux-executable", default="/usr/bin/tmux")
    parser.add_argument("--bash-executable", default="/usr/bin/bash")
    parser.add_argument("--bubblewrap-executable", default="/usr/bin/bwrap")
    return parser


def main() -> int:
    return run_gate(_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
