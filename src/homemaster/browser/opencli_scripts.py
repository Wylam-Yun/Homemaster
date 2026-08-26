"""Load and verify deterministic page-side scripts generated from OpenCLI 1.8.7."""

from __future__ import annotations

import hashlib
import json
from importlib import resources

_ROOT = "browser/generated/opencli_1_8_7"


def load_opencli_script(name: str) -> str:
    if name not in {
        "dom_snapshot_full.js",
        "dom_snapshot_interactive.js",
        "form_state.js",
        "extract_html.js",
        "html_tree.js",
    }:
        raise ValueError(f"unknown generated OpenCLI script: {name}")
    root = resources.files("homemaster").joinpath(_ROOT)
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    content = root.joinpath(name).read_bytes()
    expected = manifest["generated"][name]
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise RuntimeError(f"generated OpenCLI script hash mismatch: {name}")
    return content.decode("utf-8")


__all__ = ["load_opencli_script"]
