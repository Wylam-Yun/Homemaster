"""Execute the locked grep in a dedicated tmux/Bash/bubblewrap process."""

from __future__ import annotations

import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from case02_openenv.artifacts import append_jsonl
from case02_openenv.episode_store import EpisodeStore
from case02_openenv.terminal.policy import CommandPolicy


class TerminalExecutor:
    def __init__(
        self,
        store: EpisodeStore,
        *,
        tmux_executable: str = "/usr/bin/tmux",
        bash_executable: str = "/usr/bin/bash",
        bubblewrap_executable: str = "/usr/bin/bwrap",
        timeout_s: float = 30.0,
    ) -> None:
        self.store = store
        self.tmux_executable = tmux_executable
        self.bash_executable = bash_executable
        self.bubblewrap_executable = bubblewrap_executable
        self.timeout_s = timeout_s

    def execute(
        self,
        run_id: str,
        *,
        action_id: str,
        page_state_version: int,
        command: str,
    ) -> dict[str, Any]:
        episode = self.store.episode(run_id)
        variables = episode.state.variables
        parsed = CommandPolicy(variables["TenantId"], variables["ItemCode"]).parse(command)
        self.store.require_terminal_wait(run_id)
        command_id = f"cmd-{uuid.uuid4().hex[:12]}"
        evidence_id = f"terminal-{command_id}"
        command_dir = episode.run_root / "terminal" / command_id
        command_dir.mkdir(parents=True, exist_ok=False)
        stdout_path = command_dir / "stdout"
        stderr_path = command_dir / "stderr"
        exit_path = command_dir / "exit"
        runner_path = command_dir / "runner.sh"
        tmux_session = f"coworker-{run_id[-16:]}-{uuid.uuid4().hex[:8]}"
        sandbox_command = self._bubblewrap_command(episode.episode_root, parsed.original)
        runner = (
            "#!/usr/bin/bash\nset +e\n"
            f"{shlex.join(sandbox_command)} > {shlex.quote(str(stdout_path))} "
            f"2> {shlex.quote(str(stderr_path))}\n"
            "status=$?\n"
            f"printf '%s\\n' \"$status\" > {shlex.quote(str(exit_path))}.tmp\n"
            f"mv {shlex.quote(str(exit_path))}.tmp {shlex.quote(str(exit_path))}\n"
        )
        runner_path.write_text(runner, encoding="utf-8")
        runner_path.chmod(0o700)
        returns: dict[str, int] = {}
        start = time.monotonic()
        try:
            returns["new_session"] = self._run(
                [self.tmux_executable, "new-session", "-d", "-s", tmux_session]
            ).returncode
            returns["remain_on_exit"] = self._run(
                [self.tmux_executable, "set-option", "-t", tmux_session, "remain-on-exit", "on"]
            ).returncode
            returns["send_keys"] = self._run(
                [
                    self.tmux_executable,
                    "send-keys",
                    "-t",
                    tmux_session,
                    shlex.join([self.bash_executable, str(runner_path)]),
                    "Enter",
                ]
            ).returncode
            if any(code != 0 for code in returns.values()):
                raise RuntimeError(f"tmux setup failed: {returns}")
            deadline = start + self.timeout_s
            while not exit_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not exit_path.is_file():
                raise TimeoutError("terminal command did not publish an exit status")
            exit_text = exit_path.read_text(encoding="utf-8")
            if not exit_text.endswith("\n") or not exit_text.strip().isdigit():
                raise RuntimeError("invalid terminal exit status file")
            exit_code = int(exit_text.strip())
            stdout = stdout_path.read_text(encoding="utf-8")
            stderr = stderr_path.read_text(encoding="utf-8")
        finally:
            killed = self._run([self.tmux_executable, "kill-session", "-t", tmux_session])
            returns["kill_session"] = killed.returncode
        duration_s = time.monotonic() - start
        record = {
            "schema_version": 1,
            "run_id": run_id,
            "command_id": command_id,
            "action_id": action_id,
            "evidence_id": evidence_id,
            "original_command": parsed.original,
            "tokens": list(parsed.tokens),
            "tmux_session": tmux_session,
            "tmux_returncodes": returns,
            "exit_code": exit_code,
            "stdout_path": str(stdout_path.relative_to(episode.run_root)),
            "stderr_path": str(stderr_path.relative_to(episode.run_root)),
            "duration_s": duration_s,
        }
        append_jsonl(episode.run_root / "terminal/commands.jsonl", record)
        event = self.store.terminal_completed(
            run_id,
            action_id=action_id,
            page_state_version=page_state_version,
            command=parsed.original,
            exit_code=exit_code,
            stdout=stdout,
            evidence_id=evidence_id,
        )
        record.update(
            {
                "stdout": stdout,
                "stderr": stderr,
                "event_id": event.event_id,
                "page_state_version": event.state_version,
            }
        )
        return record

    def _bubblewrap_command(self, episode_root: Path, command: str) -> list[str]:
        return [
            self.bubblewrap_executable,
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
            str(episode_root),
            "/opt/app",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--",
            self.bash_executable,
            "-lc",
            command,
        ]

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, text=True, capture_output=True, check=False)
