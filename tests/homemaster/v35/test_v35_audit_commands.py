"""Static deletion gates that must pass after the V3.5 migration."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "scripts")


def _text_files() -> list[Path]:
    return sorted(
        path
        for root in SCAN_ROOTS
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".git" not in path.parts
        and not any(".egg-info" in part for part in path.parts)
        and path.parent != REPO_ROOT / "tests" / "homemaster" / "v35"
        and path != REPO_ROOT / "scripts" / "guard_no_legacy_terms.py"
    )


def test_legacy_tool_symbols_are_removed_from_runtime_surfaces() -> None:
    forbidden = (
        "ToolSpec",
        "legacy_adapter",
        "from_tool_spec",
        "robot_navigate",
        "robot_find_object",
        "build_universal_tool_registry",
    )
    offenders = {
        term: [
            str(path.relative_to(REPO_ROOT))
            for path in _text_files()
            if term in path.read_text(encoding="utf-8")
        ]
        for term in forbidden
    }
    offenders = {term: paths for term, paths in offenders.items() if paths}
    assert offenders == {}, f"legacy runtime symbols remain: {offenders}"


def test_textworld_and_http_worker_paths_are_removed() -> None:
    forbidden = ("AlfredTWEnv", "TextWorld", "http_worker.py", "http_client.py")
    offenders = {
        term: [
            str(path.relative_to(REPO_ROOT))
            for path in _text_files()
            if term in path.read_text(encoding="utf-8")
        ]
        for term in forbidden
    }
    offenders = {term: paths for term, paths in offenders.items() if paths}
    assert offenders == {}, f"removed environment paths remain: {offenders}"
