#!/usr/bin/env python3
"""Vendor the locked mem0 runtime from its proven PyPI wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "2.0.13"
WHEEL_SHA256 = "dff29057329370243d88bfccd367deba41c2fb1652f63225a23068cbdd1bc066"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/7c/6f/"
    "a9294a41cde34035eb78181c3042816b1ace883ce74a2a9e1ad5b6a984b2/"
    "mem0ai-2.0.13-py3-none-any.whl"
)
SDIST_SHA256 = "a81446d760ebd30fbfe356b4e3f8e95a1a567dd05e74494071dc7a2340acdcc3"
UPSTREAM_INIT = (
    b"""import importlib.metadata\n\n__version__ = importlib.metadata.version("mem0ai")\n"""
)
PATCHED_INIT = b"""# Modified by HomeMaster: the vendored runtime has no mem0ai distribution metadata.\nimport importlib.metadata\n\ntry:\n    __version__ = importlib.metadata.version("mem0ai")\nexcept importlib.metadata.PackageNotFoundError:\n    __version__ = "2.0.13"\n"""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def vendor(wheel: Path, repo: Path) -> None:
    wheel_bytes = wheel.read_bytes()
    observed = _sha256(wheel_bytes)
    if observed != WHEEL_SHA256:
        raise SystemExit(f"wheel SHA-256 mismatch: {observed}")

    target = repo / "third_party" / f"mem0ai-{VERSION}"
    package_target = target / "mem0"
    manifest_target = repo / "src" / "homemaster" / "memory" / "mem0_vendor_manifest.json"
    if package_target.exists():
        shutil.rmtree(package_target)
    package_target.mkdir(parents=True)

    entries: list[dict[str, object]] = []
    license_content: bytes | None = None
    with zipfile.ZipFile(wheel) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            path = PurePosixPath(info.filename)
            if info.is_dir():
                continue
            if path.parts[:1] == ("mem0",):
                relative = PurePosixPath(*path.parts[1:])
                if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                    raise SystemExit(f"unsafe wheel member: {info.filename}")
                upstream_content = archive.read(info)
                content = upstream_content
                modified = False
                if info.filename == "mem0/__init__.py":
                    if not upstream_content.startswith(UPSTREAM_INIT):
                        raise SystemExit("unexpected upstream mem0/__init__.py")
                    content = upstream_content.replace(UPSTREAM_INIT, PATCHED_INIT, 1)
                    modified = True
                destination = package_target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                entry: dict[str, object] = {
                    "path": f"mem0/{relative.as_posix()}",
                    "sha256": _sha256(content),
                    "size": len(content),
                }
                if modified:
                    entry["modified_by_homemaster"] = True
                    entry["upstream_sha256"] = _sha256(upstream_content)
                entries.append(entry)
            elif path.name == "LICENSE" and path.parts[0].startswith("mem0ai-"):
                license_content = archive.read(info)

    if license_content is None:
        raise SystemExit("wheel contains no mem0 license")
    (target / "LICENSE").write_bytes(license_content)
    manifest = {
        "schema_version": "homemaster-vendored-python-package-v1",
        "distribution": "mem0ai",
        "version": VERSION,
        "wheel": {"url": WHEEL_URL, "sha256": WHEEL_SHA256},
        "sdist": {"sha256": SDIST_SHA256},
        "license": "Apache-2.0",
        "files": entries,
    }
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (target / "manifest.json").write_bytes(encoded)
    manifest_target.write_bytes(encoded)
    print(f"vendored {len(entries)} mem0 files from {wheel.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    vendor(args.wheel.resolve(strict=True), args.repo.resolve(strict=True))


if __name__ == "__main__":
    main()
