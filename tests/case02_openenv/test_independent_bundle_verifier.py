from __future__ import annotations

import ast
from pathlib import Path

from scripts.coworker_demo.verify_run_bundle import _changed_pixels, _frame_stats


def test_verifier_has_no_product_imports() -> None:
    path = Path("scripts/coworker_demo/verify_run_bundle.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.startswith("case02_openenv") for name in imported)
    assert not any(name.startswith("homemaster.benchmarking.coworker_demo") for name in imported)


def test_verifier_rederives_required_nodes_from_raw_evidence() -> None:
    source = Path("scripts/coworker_demo/verify_run_bundle.py").read_text(encoding="utf-8")
    assert "raw_actions.jsonl" in source
    assert "required - observed" in source
    assert "ffprobe" in source
    assert "manifest_hash" in source
    assert "missing_manifest_entry" in source
    assert "manifest_incomplete" in source
    assert "unknown_evidence_ref" in source
    assert '"rawvideo"' in source
    assert "first_packet_not_proven" in source


def test_independent_frame_metrics_are_derived_from_raw_rgb() -> None:
    frame = bytes([0, 0, 0, 255, 255, 255])
    stats, grayscale = _frame_stats(frame, 2, 1)
    assert stats == {
        "nonblack_ratio": 0.5,
        "dark_ratio": 0.5,
        "variance": 16256.25,
    }
    assert grayscale == bytes([0, 255])
    assert _changed_pixels(grayscale, bytes([0, 254])) == 1
