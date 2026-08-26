from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

VENDOR = (
    Path(__file__).parents[3]
    / "src"
    / "homemaster"
    / "browser"
    / "vendor"
    / "opencli_1_8_7"
)
GENERATED = VENDOR.parent.parent / "generated" / "opencli_1_8_7"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_opencli_version_license_and_provenance_are_locked() -> None:
    package = json.loads((VENDOR / "package.json").read_text(encoding="utf-8"))
    upstream = (VENDOR / "UPSTREAM.md").read_text(encoding="utf-8")
    patches = (VENDOR / "PATCHES.md").read_text(encoding="utf-8")

    assert package["name"] == "@jackwener/opencli"
    assert package["version"] == "1.8.7"
    assert package["license"] == "Apache-2.0"
    assert _sha256(VENDOR / "package.json") == (
        "44d0eb2ea36788423ddfb079b2d089d326146aab78b792178a33f4a7e07b70ff"
    )
    assert _sha256(VENDOR / "LICENSE") == (
        "0210b8b66cf00358242cb921ba2be3a46dfe0190159b1b952388a3880ce1ff54"
    )
    for required in (
        "@jackwener/opencli",
        "1.8.7",
        "https://github.com/jackwener/opencli",
        "v22.22.1",
        "Apache-2.0",
    ):
        assert required in upstream
    assert "No upstream OpenCLI source file is intentionally modified" in patches


def test_opencli_sha256_manifest_covers_every_vendored_and_generated_file() -> None:
    rows: dict[Path, str] = {}
    for line in (VENDOR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = Path(os.path.abspath(VENDOR / relative))
        resolved = path.resolve()
        assert resolved.is_relative_to(VENDOR.resolve()) or resolved.is_relative_to(
            GENERATED.resolve()
        )
        assert path not in rows
        rows[path] = digest

    expected = {
        path.absolute()
        for path in VENDOR.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    expected.update(path.absolute() for path in GENERATED.rglob("*") if path.is_file())
    assert set(rows) == expected
    for path, digest in rows.items():
        assert _sha256(path) == digest, path


def test_opencli_relative_esm_import_dependency_closure_is_complete() -> None:
    root = VENDOR / "dist" / "src"
    missing: list[tuple[str, str]] = []
    pattern = re.compile(r"(?:from\s+|import\s*\()['\"]([^'\"]+)['\"]")
    for source in root.rglob("*.js"):
        text = re.sub(
            r"/\*.*?\*/|//[^\n]*", "", source.read_text(encoding="utf-8"), flags=re.S
        )
        for specifier in pattern.findall(text):
            if not specifier.startswith("."):
                continue
            target = source.parent / specifier
            if not target.suffix:
                target = target.with_suffix(".js")
            if not target.is_file():
                missing.append((source.relative_to(root).as_posix(), specifier))
    assert missing == []


def test_opencli_upstream_test_fixtures_are_complete_and_locked() -> None:
    expected = {
        "deno-v2.html": "a1cf3ef85c3ac85790d402e4e24a531391ec4be7b6b39b86e33c0e7a58487f9d",
        "openai-cookbook-readme.txt": (
            "ed596b962642ea131470fef9aa699606881f218621bae563f4b12c418ef33476"
        ),
        "wikipedia-markdown.html": (
            "f2ab2ba9afa7e48c0d06a3d8365a1671189229e939515e9622e3bf330d7770b7"
        ),
    }
    fixtures = VENDOR / "dist" / "src" / "browser" / "__fixtures__" / "article-extract"
    actual = {
        path.name: _sha256(path)
        for path in fixtures.iterdir()
        if path.is_file()
    } if fixtures.is_dir() else {}
    assert actual == expected


def test_vendored_node_dependencies_retain_package_metadata_and_license() -> None:
    package_files = sorted((VENDOR / "node_modules").rglob("package.json"))
    assert package_files
    for package_file in package_files:
        package = json.loads(package_file.read_text(encoding="utf-8"))
        assert package.get("name") and package.get("version")
        assert any(
            path.is_file() and path.name.casefold().startswith("license")
            for path in package_file.parent.iterdir()
        ), package_file.parent


def test_vendored_tree_contains_no_generated_test_or_build_caches() -> None:
    forbidden = {".pytest_cache", ".ruff_cache", ".vite", "__pycache__"}
    found = {
        path.relative_to(VENDOR).as_posix()
        for path in VENDOR.rglob("*")
        if path.name in forbidden
    }
    assert found == set()


def test_generated_opencli_manifest_matches_loaded_page_scripts() -> None:
    manifest = json.loads((GENERATED / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["upstream"] == "@jackwener/opencli@1.8.7"
    scripts = {
        path.name: _sha256(path)
        for path in GENERATED.glob("*.js")
        if path.is_file()
    }
    assert manifest["generated"] == scripts
