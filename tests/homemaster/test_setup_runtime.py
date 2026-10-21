from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest
import yaml

from homemaster import setup_runtime as setup


def _fake_uname(monkeypatch: pytest.MonkeyPatch, sysname: str, machine: str) -> None:
    monkeypatch.setattr(setup.platform, "system", lambda: sysname)
    monkeypatch.setattr(setup.platform, "machine", lambda: machine)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    import os

    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    env.update(extra)
    return env


def _stub_python(venv: Path) -> Path:
    python = venv / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    return python


def _fake_installer(calls: list[Path]):
    def install(venv: Path) -> int:
        calls.append(venv)
        _stub_python(venv)
        return 0

    return install


def _ok_doctor(calls: list[tuple[Path, Path]]):
    def runner(config: Path, python: Path) -> int:
        calls.append((config, python))
        return 0

    return runner


def test_platform_gate_rejects_intel_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_uname(monkeypatch, "Darwin", "x86_64")
    with pytest.raises(setup.SetupError, match="Apple Silicon"):
        setup.setup_runtime(environ=_env(tmp_path))


def test_platform_gate_rejects_other_os(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_uname(monkeypatch, "FreeBSD", "amd64")
    with pytest.raises(setup.SetupError, match="Linux x86_64 or macOS arm64"):
        setup.setup_runtime(environ=_env(tmp_path))


def test_runtime_root_defaults_to_homemaster_home(tmp_path: Path) -> None:
    calls: list[Path] = []
    doctor: list[tuple[Path, Path]] = []
    code = setup.setup_runtime(
        full=False,
        python_installer=_fake_installer(calls),
        doctor_runner=_ok_doctor(doctor),
        environ=_env(tmp_path),
    )
    assert code == 0
    runtime = (tmp_path / ".homemaster" / "runtime").resolve()
    assert calls == [runtime / "venv"]
    assert (runtime / "venv" / "bin" / "python").is_file()
    config = (tmp_path / ".homemaster" / "config.yaml").resolve()
    assert doctor == [(config, runtime / "venv" / "bin" / "python")]


def test_existing_venv_is_skipped(tmp_path: Path) -> None:
    runtime = tmp_path / ".homemaster" / "runtime"
    _stub_python(runtime / "venv")
    calls: list[Path] = []
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=False,
        python_installer=_fake_installer(calls),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    assert calls == []


def test_rebuild_recreates_existing_venv(tmp_path: Path) -> None:
    runtime = tmp_path / ".homemaster" / "runtime"
    _stub_python(runtime / "venv")
    marker = runtime / "venv" / "stale-marker"
    marker.write_text("old", encoding="utf-8")
    calls: list[Path] = []
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=False,
        rebuild=True,
        python_installer=_fake_installer(calls),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    assert calls == [runtime / "venv"]
    assert not marker.exists()


def test_missing_uv_errors(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env["PATH"] = str(tmp_path / "empty-bin")
    env.pop("HOMEMASTER_UV", None)
    with pytest.raises(setup.SetupError, match="uv executable is required"):
        setup.setup_runtime(environ=env)


def test_files_tier_config_created_0600_with_absolute_paths(tmp_path: Path) -> None:
    runtime = (tmp_path / ".homemaster" / "runtime").resolve()
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=False,
        python_installer=_fake_installer([]),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    config = tmp_path / ".homemaster" / "config.yaml"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["memory"]["mode"] == "files"
    assert payload["memory"]["data_root"] == str(tmp_path / ".homemaster" / "memory")
    neo4j = payload["memory"]["neo4j"]
    assert neo4j["mode"] == "managed_local"
    assert neo4j["home"] == str(runtime / "neo4j")
    assert neo4j["java_home"] == str(runtime / "java")
    assert neo4j["password"].strip() not in ("", "<set-during-setup>")
    assert (tmp_path / ".homemaster" / "memory").is_dir()


def test_second_run_keeps_password_and_mode(tmp_path: Path) -> None:
    runtime = tmp_path / ".homemaster" / "runtime"
    kwargs = dict(
        runtime_root=runtime,
        full=False,
        python_installer=_fake_installer([]),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert setup.setup_runtime(**kwargs) == 0
    config = tmp_path / ".homemaster" / "config.yaml"
    first = yaml.safe_load(config.read_text(encoding="utf-8"))
    password = first["memory"]["neo4j"]["password"]

    assert setup.setup_runtime(**kwargs) == 0
    second = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert second["memory"]["neo4j"]["password"] == password
    assert second["memory"]["mode"] == "files"


def test_existing_config_wrong_mode_fails(tmp_path: Path) -> None:
    config = tmp_path / ".homemaster" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("memory:\n  mode: files\n", encoding="utf-8")
    config.chmod(0o644)
    with pytest.raises(setup.SetupError, match="mode 0600"):
        setup.setup_runtime(
            runtime_root=tmp_path / ".homemaster" / "runtime",
            full=False,
            python_installer=_fake_installer([]),
            skip_doctor=True,
            environ=_env(tmp_path),
        )


def test_existing_config_tops_up_missing_runtime_keys(tmp_path: Path) -> None:
    config = tmp_path / ".homemaster" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "providers:\n  default: Mine\n  items: []\nmemory:\n  mode: files\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    runtime = tmp_path / ".homemaster" / "runtime"
    assert (
        setup.setup_runtime(
            runtime_root=runtime,
            full=False,
            python_installer=_fake_installer([]),
            skip_doctor=True,
            environ=_env(tmp_path),
        )
        == 0
    )
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["memory"]["mode"] == "files"
    assert payload["memory"]["data_root"] == str(tmp_path / ".homemaster" / "memory")
    assert payload["providers"]["default"] == "Mine"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_offline_only_without_bundle_fails_clearly(tmp_path: Path) -> None:
    runtime = (tmp_path / "runtime").resolve()
    calls: list[Path] = []
    with pytest.raises(setup.SetupError, match="--offline-bundle"):
        setup.setup_runtime(
            runtime_root=runtime,
            full=True,
            offline_only=True,
            python_installer=_fake_installer(calls),
            skip_doctor=True,
            environ=_env(tmp_path),
        )
    assert calls == [runtime / "venv"]


def test_full_tier_skips_present_assets(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    for name in ("neo4j", "neo4j-admin"):
        binary = runtime / "neo4j" / "bin" / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
    java = runtime / "java" / "bin" / "java"
    java.parent.mkdir(parents=True, exist_ok=True)
    java.write_text("#!/bin/sh\n", encoding="utf-8")
    java.chmod(0o755)
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=True,
        python_installer=_fake_installer([]),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    config = tmp_path / ".homemaster" / "config.yaml"
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["memory"]["mode"] == "full"


def test_repo_checkout_detected_and_portable_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "uv.lock").write_text("", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "homemaster"\n', encoding="utf-8")
    runtime = repo / ".runtime"
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=False,
        python_installer=_fake_installer([]),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    config = repo / "config" / "homemaster.yaml"
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["memory"]["mode"] == "files"
    assert payload["memory"]["data_root"] == "../.runtime/memory"
    assert payload["memory"]["neo4j"]["home"] == "../.runtime/neo4j"


def test_packaged_resources_are_readable() -> None:
    import importlib.resources

    resources = importlib.resources.files("homemaster") / "resources"
    lock = json.loads((resources / "runtime-assets.lock.json").read_text(encoding="utf-8"))
    assert lock["neo4j"]["url"] and lock["neo4j"]["sha256"]
    assert "darwin-arm64" in lock["platforms"] and "linux-x86_64" in lock["platforms"]
    example = yaml.safe_load((resources / "homemaster.example.yaml").read_text(encoding="utf-8"))
    assert "providers" in example and "memory" in example


def test_load_lock_repo_copy_wins_over_package(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "runtime-assets.lock.json").write_text(
        json.dumps({"neo4j": {"marker": True}}), encoding="utf-8"
    )
    assert setup._load_lock(repo)["neo4j"]["marker"] is True
    packaged = setup._load_lock(None)
    assert packaged["neo4j"]["url"].startswith("https://")


def test_verify_offline_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = bundle / "payload.bin"
    payload.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    (bundle / "SHA256SUMS").write_text(f"{digest}  ./payload.bin\n", encoding="utf-8")
    setup._verify_offline_bundle(bundle)
    (bundle / "SHA256SUMS").write_text(f"{'0' * 64}  ./payload.bin\n", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="checksum mismatch"):
        setup._verify_offline_bundle(bundle)


def _neo4j_archive(bundle_assets: Path, lock_path: Path) -> Path:
    bundle_assets.mkdir(parents=True, exist_ok=True)
    target = bundle_assets / "neo4j-community-9.9.9-unix.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in (
            "neo4j-community-9.9.9/bin/",
            "neo4j-community-9.9.9/bin/neo4j",
            "neo4j-community-9.9.9/bin/neo4j-admin",
        ):
            info = tarfile.TarInfo(name)
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                data = b"#!/bin/sh\nexit 0\n"
                info.size = len(data)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(data))
    target.write_bytes(buf.getvalue())
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["neo4j"]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    lock["neo4j"]["version"] = "9.9.9"
    lock["neo4j"]["url"] = f"https://example.test/{target.name}"
    lock_path.write_text(json.dumps(lock))
    return target


def test_ensure_neo4j_from_offline_bundle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    lock_path = repo / "config" / "runtime-assets.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "neo4j": {"version": "9.9.9", "url": "https://example.test/x", "sha256": "0" * 64},
                "platforms": {},
            }
        ),
        encoding="utf-8",
    )
    bundle = tmp_path / "bundle"
    _neo4j_archive(bundle / "assets", lock_path)
    destination = tmp_path / "runtime" / "neo4j"
    result = setup.ensure_neo4j(repo, destination, offline_bundle=bundle)
    assert (result / "bin" / "neo4j").is_file()
    assert (result / "bin" / "neo4j-admin").is_file()


def test_version_satisfies_lock_range() -> None:
    assert setup._version_satisfies("0.11.6", ">=0.11,<0.13")
    assert setup._version_satisfies("0.12.9", ">=0.11,<0.13")
    assert not setup._version_satisfies("0.13.0", ">=0.11,<0.13")
    assert not setup._version_satisfies("0.10.0", ">=0.11,<0.13")


def test_generated_config_clears_demo_browser_gateway(tmp_path: Path) -> None:
    """Packaged example pins ``browser_gateway.start_url`` for the web demo;
    a generated managed config must not silently enable the browser env."""
    runtime = (tmp_path / ".homemaster" / "runtime").resolve()
    code = setup.setup_runtime(
        runtime_root=runtime,
        full=False,
        python_installer=_fake_installer([]),
        skip_doctor=True,
        environ=_env(tmp_path),
    )
    assert code == 0
    payload = yaml.safe_load(
        (tmp_path / ".homemaster" / "config.yaml").read_text(encoding="utf-8")
    )
    assert payload["browser_gateway"]["start_url"] is None


def test_package_spec_derives_origin_from_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Dist:
        def read_text(self, name: str) -> str:
            assert name == "direct_url.json"
            return '{"url": "file:///tmp/homemaster-3.2.0-py3-none-any.whl"}'

    monkeypatch.setattr(
        setup.importlib.metadata, "distribution", lambda name: _Dist()
    )
    spec = setup._package_spec({}, full=True, with_browser=False)
    assert spec == (
        "homemaster[memory] @ file:///tmp/homemaster-3.2.0-py3-none-any.whl"
    )


def test_package_spec_falls_back_to_bare_name_when_origin_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Dist:
        def read_text(self, name: str) -> None:
            return None

    monkeypatch.setattr(
        setup.importlib.metadata, "distribution", lambda name: _Dist()
    )
    assert setup._package_spec({}, full=False, with_browser=False) == "homemaster"


def test_package_spec_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = setup._package_spec(
        {"HOMEMASTER_SETUP_PACKAGE": "/src/HomeMaster[memory]"},
        full=True,
        with_browser=True,
    )
    assert spec == "/src/HomeMaster[memory]"
