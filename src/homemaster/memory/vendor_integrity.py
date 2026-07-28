"""Integrity checks for the mem0 runtime distributed inside HomeMaster."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from functools import lru_cache
from importlib import metadata, resources
from pathlib import Path


class VendoredMem0IntegrityError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def verify_vendored_mem0() -> Path:
    try:
        metadata.distribution("mem0ai")
    except metadata.PackageNotFoundError:
        pass
    else:
        raise VendoredMem0IntegrityError("external mem0ai distribution is installed")

    spec = importlib.util.find_spec("mem0")
    if spec is None or spec.origin is None:
        raise VendoredMem0IntegrityError("vendored mem0 package is not importable")
    package_root = Path(spec.origin).resolve().parent
    manifest_resource = resources.files("homemaster.memory").joinpath("mem0_vendor_manifest.json")
    manifest = json.loads(manifest_resource.read_text(encoding="utf-8"))
    if manifest.get("distribution") != "mem0ai" or manifest.get("version") != "2.0.13":
        raise VendoredMem0IntegrityError("vendored mem0 manifest identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise VendoredMem0IntegrityError("vendored mem0 manifest has no files")

    for item in files:
        if not isinstance(item, dict):
            raise VendoredMem0IntegrityError("vendored mem0 manifest entry is invalid")
        raw_path = item.get("path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(raw_path, str) or not raw_path.startswith("mem0/"):
            raise VendoredMem0IntegrityError("vendored mem0 manifest path is invalid")
        relative = Path(raw_path).relative_to("mem0")
        if relative.is_absolute() or ".." in relative.parts:
            raise VendoredMem0IntegrityError("vendored mem0 manifest path escapes package")
        target = package_root / relative
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise VendoredMem0IntegrityError(f"vendored mem0 file is missing: {raw_path}") from exc
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise VendoredMem0IntegrityError(f"vendored mem0 file checksum mismatch: {raw_path}")
    return package_root


__all__ = ["VendoredMem0IntegrityError", "verify_vendored_mem0"]
