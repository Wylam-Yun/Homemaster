#!/usr/bin/env python3
"""Run the V2.0 Skill installation external terminal-state gates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from homemaster.adapters.profiles import build_home_profile
from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.permissions import HomePermissionPolicy, PermissionMode, PermissionSettingsConfig
from homemaster.skills.loader import load_skill_registry
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationStatus,
)
from homemaster.tools.legacy_adapter import LegacyToolExecutionContext
from homemaster.tools.pipeline import ToolExecutionPipeline

COMMIT = "9b2efd795c6aa09f88b0c257d269a9e518da6ae7"
REPOSITORY = "https://github.com/HKUDS/OpenHarness.git"
REMOTE_SKILL = (
    "https://raw.githubusercontent.com/HKUDS/OpenHarness/"
    f"{COMMIT}/src/openharness/skills/bundled/content/commit.md"
)


class Gate:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home
        self.skill_root = home / ".homemaster" / "skills"
        self.profile = build_home_profile()
        self.pipeline = ToolExecutionPipeline(
            self.profile.catalog,
            permission_policy=HomePermissionPolicy(
                PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
            ),
        )
        self.registry = self._discover()
        self.registry.set_refresher(self._discover)
        self.evidence: dict[str, object] = {}

    def _discover(self):
        return load_skill_registry(
            user_dirs=(self.skill_root,),
            allow_project=False,
            allowed_builtin_overrides=("skill-creator", "commit"),
        )

    async def execute(self, name: str, arguments: dict[str, object]) -> ToolExecutionResult:
        lookup = self.profile.view.lookup(name)
        assert lookup.tool is not None, name
        run_context = RunContext(
            session_id="v20-install",
            run_id="v20-install",
            turn_index=0,
            settings=object(),
            event_sink=None,
            deps={"skill_registry": self.registry},
        )
        context = ToolExecutionContext(
            session_id="v20-install",
            run_id="v20-install",
            turn_index=0,
            tool_call_id=f"call-{name}",
            internal_tool_id=lookup.tool.definition.internal_id,
            tool_view=self.profile.view,
            permission_subject=PermissionSubject(
                subject_id="v20-verifier",
                channel="cli",
                roles=("local_operator",),
                capabilities=(
                    "tool.read",
                    "tool.mutate",
                    "tool.auto",
                    "filesystem.read",
                    "filesystem.write",
                    "network.http",
                    "process.exec",
                ),
            ),
            backend=LegacyToolExecutionContext(
                run_context=run_context,
                tool_call_id=f"call-{name}",
                internal_tool_id=lookup.tool.definition.internal_id,
            ),
            deadline=None,
            cancellation=None,
            domain_observer=None,
            working_directory=self.root,
            services={"skill_registry": self.registry},
        )
        return await self.pipeline.execute(
            ToolCall(id=f"call-{name}", name=name, arguments=arguments),
            context,
        )

    async def bash(self, command: str, *, timeout: int = 600) -> ToolExecutionResult:
        result = await self.execute(
            "bash",
            {"command": command, "cwd": str(self.root), "timeout_seconds": timeout},
        )
        assert result.status is ToolExecutionStatus.SUCCESS, result.to_dict()
        assert result.data["returncode"] == 0
        return result


async def verify(source_skill: Path, *, keep: bool) -> dict[str, object]:
    temporary = tempfile.TemporaryDirectory(prefix="homemaster-v20-install-")
    root = Path(temporary.name).resolve()
    home = root / "home"
    home.mkdir()
    gate = Gate(root, home)
    try:
        await _migrate_skill_creator(gate, source_skill)
        await _clone_and_create(gate)
        await _archives_and_scripts(gate)
        await _dependency_installs(gate)
        await _https_and_second_process(gate)
        gate.evidence["root"] = str(root)
        gate.evidence["status"] = "PASS"
        if keep:
            retained = Path(tempfile.gettempdir()) / f"homemaster-v20-evidence-{os.getpid()}"
            shutil.copytree(root, retained)
            gate.evidence["retained_root"] = str(retained)
        return gate.evidence
    finally:
        temporary.cleanup()


async def _migrate_skill_creator(gate: Gate, source: Path) -> None:
    assert source.is_dir()
    source_content = (source / "SKILL.md").read_bytes()
    before = gate.registry.get("skill-creator")
    assert before is not None
    assert Path(before.base_dir).resolve() != source.resolve()
    assert before.content.encode() != source_content

    target = gate.skill_root / "skill-creator"
    await gate.bash(
        f"mkdir -p {shlex.quote(str(gate.skill_root))} && "
        f"cp -a {shlex.quote(str(source))} {shlex.quote(str(target))}"
    )
    source_hashes = _tree_hashes(source)
    target_hashes = _tree_hashes(target)
    assert target_hashes == source_hashes

    skill = await gate.execute("skill", {"name": "skill-creator"})
    view = await gate.execute("skill_view", {"skill_name": "skill-creator"})
    reference = await gate.execute(
        "read_file",
        {"path": str(target / "references" / "openai_yaml.md"), "limit": 20},
    )
    for result in (skill, view, reference):
        assert result.status is ToolExecutionStatus.SUCCESS, result.to_dict()
    assert skill.data["content"].encode() == source_content
    assert view.data["content"] == skill.data["content"]
    assert Path(skill.data["base_dir"]).resolve() == target.resolve()
    assert "openai" in reference.text.lower()
    gate.evidence["skill_creator_files"] = len(target_hashes)
    gate.evidence["skill_creator_sha256"] = _sha(target / "SKILL.md")


async def _clone_and_create(gate: Gate) -> None:
    checkout = gate.root / "OpenHarness"
    await gate.bash(f"git clone --quiet {REPOSITORY} {shlex.quote(str(checkout))}")
    await gate.bash(f"git -C {shlex.quote(str(checkout))} checkout --quiet {COMMIT}")
    observed = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert observed.stdout.strip() == COMMIT

    creator = gate.skill_root / "skill-creator" / "scripts"
    generated = gate.skill_root / "generated-skill"
    await gate.bash(
        f"{shlex.quote(sys.executable)} "
        f"{shlex.quote(str(creator / 'init_skill.py'))} generated-skill "
        f"--path {shlex.quote(str(gate.skill_root))}"
    )
    assert generated.joinpath("SKILL.md").is_file()
    generated_text = generated.joinpath("SKILL.md").read_text(encoding="utf-8")
    description_line = next(
        line for line in generated_text.splitlines() if line.startswith("description: ")
    )
    edited = await gate.execute(
        "edit_file",
        {
            "path": str(generated / "SKILL.md"),
            "old_str": description_line,
            "new_str": (
                "description: Verify HomeMaster can create, validate, and rediscover a "
                "complete generated Skill."
            ),
        },
    )
    assert edited.status is ToolExecutionStatus.SUCCESS
    assert edited.verification.status is VerificationStatus.PASSED
    await gate.bash(
        f"{shlex.quote(sys.executable)} "
        f"{shlex.quote(str(creator / 'quick_validate.py'))} {shlex.quote(str(generated))}"
    )
    generated_skill = await gate.execute("skill", {"name": "generated-skill"})
    assert generated_skill.status is ToolExecutionStatus.SUCCESS
    assert generated_skill.data["content"].encode() == generated.joinpath("SKILL.md").read_bytes()
    gate.evidence["git_head"] = observed.stdout.strip()
    gate.evidence["generated_skill_sha256"] = _sha(generated / "SKILL.md")


async def _archives_and_scripts(gate: Gate) -> None:
    fixture = gate.root / "archive-source"
    fixture.mkdir()
    fixture.joinpath("one.txt").write_text("one\n", encoding="utf-8")
    fixture.joinpath("two.txt").write_text("two\n", encoding="utf-8")
    zip_path = gate.root / "fixture.zip"
    tar_path = gate.root / "fixture.tar"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(fixture / "one.txt", "payload/one.txt")
        archive.write(fixture / "two.txt", "payload/two.txt")
    with tarfile.open(tar_path, "w") as archive:
        archive.add(fixture / "one.txt", arcname="payload/one.txt")
        archive.add(fixture / "two.txt", arcname="payload/two.txt")
    zip_out = gate.root / "zip-out"
    tar_out = gate.root / "tar-out"
    await gate.bash(
        f"mkdir {shlex.quote(str(zip_out))} {shlex.quote(str(tar_out))} && "
        f"unzip -q {shlex.quote(str(zip_path))} -d {shlex.quote(str(zip_out))} && "
        f"tar -xf {shlex.quote(str(tar_path))} -C {shlex.quote(str(tar_out))}"
    )
    expected = {"one.txt": b"one\n", "two.txt": b"two\n"}
    for name, content in expected.items():
        assert zip_out.joinpath("payload", name).read_bytes() == content
        assert tar_out.joinpath("payload", name).read_bytes() == content

    malicious = gate.root / "traversal.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escaped.txt", "forbidden")
    malicious_out = gate.root / "traversal-out"
    malicious_out.mkdir()
    rejected = await gate.execute(
        "bash",
        {
            "command": (
                f"unzip -q {shlex.quote(str(malicious))} -d {shlex.quote(str(malicious_out))}"
            ),
            "cwd": str(gate.root),
        },
    )
    assert not gate.root.joinpath("escaped.txt").exists()
    assert rejected.data.get("returncode") in {0, 1}

    python_script = gate.root / "sentinel.py"
    shell_script = gate.root / "sentinel.sh"
    python_script.write_text(
        "from pathlib import Path\nPath('python.sentinel').write_text('python-ok')\n",
        encoding="utf-8",
    )
    shell_script.write_text("printf shell-ok > shell.sentinel\n", encoding="utf-8")
    await gate.bash(
        f"{shlex.quote(sys.executable)} {shlex.quote(str(python_script))} && "
        f"bash {shlex.quote(str(shell_script))}"
    )
    assert gate.root.joinpath("python.sentinel").read_text() == "python-ok"
    assert gate.root.joinpath("shell.sentinel").read_text() == "shell-ok"
    failed = await gate.execute("bash", {"command": "exit 19", "cwd": str(gate.root)})
    assert failed.status is ToolExecutionStatus.FAILURE
    assert failed.data["returncode"] == 19
    gate.evidence["archives"] = {"zip": len(expected), "tar": len(expected)}


async def _dependency_installs(gate: Gate) -> None:
    venv = gate.root / "dependency-venv"
    await gate.bash(
        f"uv venv --python {shlex.quote(sys.executable)} {shlex.quote(str(venv))} && "
        f"uv pip install --python {shlex.quote(str(venv / 'bin' / 'python'))} packaging==24.2"
    )
    python_probe = subprocess.run(
        [str(venv / "bin" / "python"), "-c", "import packaging; print(packaging.__version__)"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert python_probe.stdout.strip() == "24.2"
    gate.evidence["python_dependency"] = "packaging==24.2"

    npm = shutil.which("npm")
    node = shutil.which("node")
    if npm is None or node is None:
        gate.evidence["npm_dependency"] = "unsupported"
        return
    npm_root = gate.root / "npm-project"
    npm_root.mkdir()
    await gate.bash(
        f"cd {shlex.quote(str(npm_root))} && npm init --yes >/dev/null && "
        "npm install --ignore-scripts --save-exact --fetch-timeout=30000 "
        "--fetch-retries=1 --registry=https://registry.npmmirror.com is-number@7.0.0",
        timeout=120,
    )
    node_probe = subprocess.run(
        [node, "-e", "console.log(require('is-number/package.json').version)"],
        cwd=npm_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert node_probe.stdout.strip() == "7.0.0"
    gate.evidence["npm_dependency"] = "is-number@7.0.0"


async def _https_and_second_process(gate: Gate) -> None:
    fetched = await gate.execute("web_fetch", {"url": REMOTE_SKILL, "max_chars": 50_000})
    assert fetched.status is ToolExecutionStatus.SUCCESS, fetched.to_dict()
    metadata = fetched.data["metadata"]
    assert metadata["status_code"] == 200
    content = fetched.data["content"]
    assert isinstance(content, str) and content.strip()

    curl_path = gate.root / "remote.bin"
    curl = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "5",
            "--header",
            "Accept-Encoding: identity",
            REMOTE_SKILL,
            "--output",
            str(curl_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert curl.returncode == 0, curl.stderr
    curl_sha = _sha(curl_path)
    assert curl_sha == metadata["raw_sha256"]
    assert curl_path.read_bytes() == content.encode()

    installed = gate.skill_root / "commit" / "SKILL.md"
    written = await gate.execute(
        "write_file",
        {"path": str(installed), "content": content, "create_directories": True},
    )
    assert written.status is ToolExecutionStatus.SUCCESS
    assert written.verification.status is VerificationStatus.PASSED
    assert installed.read_bytes() == curl_path.read_bytes()
    loaded = await gate.execute("skill", {"name": "commit"})
    assert loaded.status is ToolExecutionStatus.SUCCESS
    assert loaded.data["content"] == content

    environment = dict(os.environ)
    environment["HOME"] = str(gate.home)
    cli = subprocess.run(
        [
            "uv",
            "run",
            "homemaster",
            "--dry-run",
            "-p",
            "list skills",
            "--output-format",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 0, cli.stderr
    document = json.loads(cli.stdout)
    skills = {item["name"]: item for item in document["skills"]}
    assert skills["generated-skill"]["source"] == "user"
    assert skills["skill-creator"]["source"] == "bundled"
    assert skills["commit"]["source"] == "bundled"
    gate.evidence["https_sha256"] = curl_sha
    gate.evidence["second_process_skills"] = {
        name: skills[name]["source"] for name in ("generated-skill", "skill-creator", "commit")
    }


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-skill",
        type=Path,
        default=Path.home() / ".codex" / "skills" / ".system" / "skill-creator",
    )
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    evidence = asyncio.run(verify(args.source_skill.expanduser().resolve(), keep=args.keep))
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
