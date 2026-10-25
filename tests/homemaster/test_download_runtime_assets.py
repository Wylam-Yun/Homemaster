from __future__ import annotations

import hashlib
import io
import json
import platform
import tarfile
from pathlib import Path

import pytest

from scripts import download_runtime_assets as assets


def _fake_platform(monkeypatch: pytest.MonkeyPatch, sysname: str, machine: str) -> None:
    # ``_current_platform()`` reads ``platform.system``/``platform.machine``
    # (os.uname does not exist on Windows), so the fake must patch that seam.
    monkeypatch.setattr(platform, "system", lambda: sysname)
    monkeypatch.setattr(platform, "machine", lambda: machine)


def _lock(repo: Path, *, platform_key: str = "darwin-arm64") -> Path:
    config = repo / "config"
    config.mkdir(parents=True, exist_ok=True)
    lock = {
        "schema_version": 2,
        "python": {"version_range": ">=3.11,<3.12", "provider": "uv-managed"},
        "uv": {"version_range": ">=0.11,<0.13"},
        "neo4j": {
            "version": "2026.05.0",
            "url": "https://example.test/neo4j.tar.gz",
            "sha256": "0" * 64,
        },
        "platforms": {
            platform_key: {
                "java": {
                    "version": "21.0.12.1+1",
                    "url": "https://example.test/jdk.tar.gz",
                    "sha256": "0" * 64,
                }
            }
        },
    }
    path = config / "runtime-assets.lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def _jdk_archive(bundle_assets: Path, *, macos_layout: bool) -> Path:
    """Build a fake JDK tarball inside an offline bundle assets dir."""
    bundle_assets.mkdir(parents=True, exist_ok=True)
    target = bundle_assets / "jdk.tar.gz"
    prefix = "Contents/Home/bin" if macos_layout else "bin"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in (f"jdk-21.0.12.1+1/{prefix}/", f"jdk-21.0.12.1+1/{prefix}/java"):
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
    return target


def _patch_lock_sha(archive: Path, lock_path: Path) -> None:
    lock = json.loads(lock_path.read_text())
    lock["platforms"]["darwin-arm64"]["java"]["sha256"] = hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    lock_path.write_text(json.dumps(lock))


def test_current_platform_maps_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_platform(monkeypatch, "Linux", "x86_64")
    assert assets._current_platform() == "linux-x86_64"
    _fake_platform(monkeypatch, "Darwin", "arm64")
    assert assets._current_platform() == "darwin-arm64"


def test_current_platform_rejects_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_platform(monkeypatch, "Darwin", "x86_64")
    with pytest.raises(RuntimeError, match="unsupported platform"):
        assets._current_platform()


def test_ensure_java_resolves_platform_keyed_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_platform(monkeypatch, "Darwin", "arm64")
    repo = tmp_path / "repo"
    lock_path = _lock(repo)
    bundle = tmp_path / "bundle"
    archive = _jdk_archive(bundle / "assets", macos_layout=True)
    _patch_lock_sha(archive, lock_path)

    destination = tmp_path / "runtime" / "java"
    result = assets.ensure_java(repo, destination, offline_bundle=bundle)

    assert (result / "bin" / "java").is_file()


def test_ensure_java_handles_macos_contents_home_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_platform(monkeypatch, "Darwin", "arm64")
    repo = tmp_path / "repo"
    lock_path = _lock(repo)
    bundle = tmp_path / "bundle"
    archive = _jdk_archive(bundle / "assets", macos_layout=True)
    _patch_lock_sha(archive, lock_path)

    assets.ensure_java(repo, tmp_path / "java", offline_bundle=bundle)

    assert (tmp_path / "java" / "bin" / "java").is_file()
    # moved dir is Contents/Home itself, not the outer jdk-* wrapper
    assert not (tmp_path / "java" / "Contents").exists()


def test_ensure_java_handles_linux_top_level_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_platform(monkeypatch, "Darwin", "arm64")  # platform pick is separate from layout
    repo = tmp_path / "repo"
    lock_path = _lock(repo)
    bundle = tmp_path / "bundle"
    archive = _jdk_archive(bundle / "assets", macos_layout=False)
    _patch_lock_sha(archive, lock_path)

    assets.ensure_java(repo, tmp_path / "java", offline_bundle=bundle)

    assert (tmp_path / "java" / "bin" / "java").is_file()
