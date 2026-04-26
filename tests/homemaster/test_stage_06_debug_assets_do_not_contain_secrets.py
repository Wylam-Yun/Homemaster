from __future__ import annotations

from pathlib import Path

SECRET_MARKERS = (
    "Bearer ",
    "Authorization",
    "x-api-key",
    "api_keys",
    "sk-",
    "secret-one",
)


def test_stage_06_debug_assets_do_not_contain_secrets() -> None:
    root = Path("tests/homemaster/llm_cases/stage_06")
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in SECRET_MARKERS:
            assert marker not in text, f"{marker!r} leaked in {path}"
