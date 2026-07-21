from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.v19_release.capture_baseline import (
    BASELINE_FILES,
    HOMEMASTER_BASELINE_COMMIT,
    OPENHARNESS_BASELINE_COMMIT,
    _assert_no_sensitive_values,
    _sanitize_output,
    _verify_locked_sources,
    capture_baseline,
)
from scripts.v19_release.capture_environment_identity import _import_identity, capture_identity
from scripts.v19_release.validate_upstream_port_manifest import (
    _validate_python_node,
    _validate_python_symbol,
    validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENHARNESS_ROOT = REPO_ROOT.parent / "OpenHarness"


def test_upstream_port_manifest_is_valid_with_current_ports() -> None:
    report = validate_manifest(
        REPO_ROOT / "plan/V1.9/upstream-port-manifest.json", repo_root=REPO_ROOT
    )
    assert report == {
        "status": "PASS",
        "upstream_commit": "9b2efd795c6aa09f88b0c257d269a9e518da6ae7",
        "port_count": 7,
    }


def test_port_without_upstream_tests_requires_gap_and_local_characterization(
    tmp_path: Path,
) -> None:
    source_path = "src/openharness/tools/base.py"
    source_bytes = subprocess.run(
        [
            "git",
            "-C",
            str(OPENHARNESS_ROOT),
            "show",
            f"9b2efd795c6aa09f88b0c257d269a9e518da6ae7:{source_path}",
        ],
        check=True,
        capture_output=True,
    ).stdout
    payload = {
        "schema_version": "homemaster-v1.9-upstream-port-manifest-v1",
        "upstream": {
            "repo": "../OpenHarness",
            "commit": "9b2efd795c6aa09f88b0c257d269a9e518da6ae7",
        },
        "ports": [
            {
                "id": "test-base-tool",
                "mode": "A",
                "source": {
                    "repo": "../OpenHarness",
                    "commit": "9b2efd795c6aa09f88b0c257d269a9e518da6ae7",
                    "path": source_path,
                    "symbol": "BaseTool",
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                },
                "destination": "src/homemaster/tools/contracts.py",
                "copied_test_ids": [],
                "mechanical_deltas": ["imports"],
                "homemaster_deltas": ["stable id"],
                "upstream_test_gap": {
                    "reason": "locked upstream has no direct BaseTool schema test",
                    "search_evidence": ["rg BaseTool tests"],
                },
                "characterization_test_ids": [
                    "tests/homemaster/test_v19_release_tools.py::test_upstream_port_manifest_is_valid_with_current_ports"
                ],
                "sync_policy": "manual upstream comparison",
            }
        ],
    }
    path = tmp_path / "ports.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_manifest(path, repo_root=REPO_ROOT)["port_count"] == 1

    payload["ports"][0]["characterization_test_ids"] = [
        "tests/homemaster/test_v19_release_tools.py::test_not_a_real_node"
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="symbol does not exist"):
        validate_manifest(path, repo_root=REPO_ROOT)

    payload["ports"][0]["characterization_test_ids"] = [
        "tests/homemaster/test_v19_release_tools.py::test_upstream_port_manifest_is_valid_with_current_ports"
    ]
    payload["ports"][0]["source"]["symbol"] = "DefinitelyNotInSource"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source symbol does not exist"):
        validate_manifest(path, repo_root=REPO_ROOT)

    payload["ports"][0]["source"]["symbol"] = "BaseTool"
    payload["ports"][0]["characterization_test_ids"] = [
        "tests/homemaster/test_v19_release_tools.py::"
        "test_upstream_port_manifest_is_valid_with_current_ports[definitely-not-collected]"
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="parameter id"):
        validate_manifest(path, repo_root=REPO_ROOT)

    payload["ports"][0]["characterization_test_ids"] = [
        "tests/homemaster/test_v19_release_tools.py::test_upstream_port_manifest_is_valid_with_current_ports"
    ]
    payload["ports"][0]["upstream_test_gap"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="requires upstream_test_gap"):
        validate_manifest(path, repo_root=REPO_ROOT)


def test_environment_identity_fails_closed_for_hkust4_alfworld_without_conda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    with pytest.raises(ValueError, match="hm_alfworld"):
        capture_identity(
            repo_root=REPO_ROOT,
            site="hkust4",
            provider="mimo",
            model="mimo-v2.5",
            alfworld_check=True,
            alfworld_root=REPO_ROOT,
            alfworld_config=REPO_ROOT / "config/alfworld_v18_regression_trials.json",
            alfworld_trials=REPO_ROOT / "config/alfworld_v19_release_trials.json",
            coworker_manifest=REPO_ROOT / "data/coworker_demo/case_02/dataset_manifest.json",
            conda_explicit=b"explicit",
        )


def test_port_provenance_rejects_symbols_pytest_will_not_collect() -> None:
    invalid_nodes = (
        (b"def helper():\n    pass\n", "tests/test_x.py::helper"),
        (
            b"class Helper:\n    def test_method(self):\n        pass\n",
            "tests/test_x.py::Helper::test_method",
        ),
        (
            b"import pytest\n"
            b"@pytest.mark.parametrize('value', [1, 2], ids=['claimed-exact'])\n"
            b"def test_case(value):\n    pass\n",
            "tests/test_x.py::test_case",
        ),
        (
            b"def test_disabled():\n    pass\n"
            b"test_disabled.__test__ = False\n",
            "tests/test_x.py::test_disabled",
        ),
        (
            b"def test_rebound():\n    pass\n"
            b"test_rebound = None\n",
            "tests/test_x.py::test_rebound",
        ),
        (
            b"def test_disabled_zero():\n    pass\n"
            b"test_disabled_zero.__test__ = 0\n",
            "tests/test_x.py::test_disabled_zero",
        ),
        (
            b"import pytest\n"
            b"@pytest.mark.parametrize('value', "
            b"[pytest.param(1, id='actual')], ids=['claimed'])\n"
            b"def test_override(value):\n    pass\n",
            "tests/test_x.py::test_override[claimed]",
        ),
    )
    for source, node_id in invalid_nodes:
        with pytest.raises(ValueError):
            _validate_python_node(source, node_id=node_id, label="test node")

    with pytest.raises(ValueError, match="does not exist"):
        _validate_python_symbol(
            b"Ghost: int\n",
            symbol="Ghost",
            label="source symbol",
        )

    _validate_python_node(
        b"import pytest\n"
        b"@pytest.mark.parametrize('value', [1, 2], ids=['first', 'second'])\n"
        b"def test_case(value):\n    pass\n",
        node_id="tests/test_x.py::test_case[first]",
        label="test node",
    )
    _validate_python_node(
        b"import pytest\n"
        b"@pytest.mark.parametrize('value', "
        b"[pytest.param(1, id='actual')], ids=['claimed'])\n"
        b"def test_override(value):\n    pass\n",
        node_id="tests/test_x.py::test_override[actual]",
        label="test node",
    )


def test_environment_identity_records_required_hkust4_alfworld_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "hm_alfworld")
    conda_prefix = tmp_path / "conda/envs/hm_alfworld"
    (conda_prefix / "bin").mkdir(parents=True)
    monkeypatch.setenv("CONDA_PREFIX", str(conda_prefix))
    monkeypatch.setattr(
        "scripts.v19_release.capture_environment_identity.sys.executable",
        str(conda_prefix / "bin/python"),
    )

    def fake_import(package, *, repo_root, conda_prefix):
        origin = (
            repo_root / "src/homemaster/__init__.py"
            if package == "homemaster"
            else conda_prefix / f"lib/python3.11/site-packages/{package}/__init__.py"
        )
        return {
            "status": "present",
            "version": "test",
            "origin": str(origin),
            "origin_within_repo": package == "homemaster",
            "origin_within_conda_prefix": package != "homemaster",
        }

    monkeypatch.setattr(
        "scripts.v19_release.capture_environment_identity._import_identity",
        fake_import,
    )
    root = tmp_path / "alfworld"
    root.mkdir()
    (root / "identity.json").write_text("{}", encoding="utf-8")
    identity = capture_identity(
        repo_root=REPO_ROOT,
        site="hkust4",
        provider="mimo",
        model="mimo-v2.5",
        alfworld_check=True,
        alfworld_root=root,
        alfworld_config=REPO_ROOT / "config/alfworld_v18_regression_trials.json",
        alfworld_trials=REPO_ROOT / "config/alfworld_v19_release_trials.json",
        coworker_manifest=REPO_ROOT / "data/coworker_demo/case_02/dataset_manifest.json",
        conda_explicit=b"@EXPLICIT\npackage\n",
    )
    assert identity["alfworld"]["environment_name"] == "hm_alfworld"
    assert identity["alfworld"]["conda_explicit_sha256"] == hashlib.sha256(
        b"@EXPLICIT\npackage\n"
    ).hexdigest()
    assert set(identity["imports"]) == {"homemaster", "alfworld", "ai2thor"}
    assert identity["coworker"]["declared_file_sha256"]
    assert identity["python"]["executable"] == str(conda_prefix / "bin/python")


def test_environment_identity_requires_all_alfworld_hash_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "hm_alfworld")
    with pytest.raises(ValueError, match="missing required inputs"):
        capture_identity(
            repo_root=REPO_ROOT,
            site="hkust4",
            provider="mimo",
            model="mimo-v2.5",
            alfworld_check=True,
            alfworld_root=None,
            alfworld_config=None,
            alfworld_trials=None,
            coworker_manifest=REPO_ROOT / "data/coworker_demo/case_02/dataset_manifest.json",
            conda_explicit=b"explicit",
        )


def test_environment_identity_rejects_homemaster_outside_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import(package, *, repo_root, conda_prefix):
        return {
            "status": "present" if package == "homemaster" else "absent",
            "version": "test" if package == "homemaster" else None,
            "origin": "/tmp/not-candidate/__init__.py" if package == "homemaster" else None,
            "origin_within_repo": False if package == "homemaster" else None,
            "origin_within_conda_prefix": None,
        }

    monkeypatch.setattr(
        "scripts.v19_release.capture_environment_identity._import_identity",
        fake_import,
    )
    with pytest.raises(ValueError, match="outside the candidate worktree"):
        capture_identity(
            repo_root=REPO_ROOT,
            site="hpc2",
            provider="mimo",
            model="mimo-v2.5",
            alfworld_check=False,
            alfworld_root=None,
            alfworld_config=None,
            alfworld_trials=None,
            coworker_manifest=REPO_ROOT / "data/coworker_demo/case_02/dataset_manifest.json",
        )


def test_import_identity_resolves_symlinks_before_containment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    conda_prefix = tmp_path / "conda"
    outside = tmp_path / "outside"
    candidate.mkdir()
    conda_prefix.mkdir()
    outside.mkdir()
    outside_origin = outside / "__init__.py"
    outside_origin.write_text("", encoding="utf-8")

    candidate_link = candidate / "homemaster.py"
    candidate_link.symlink_to(outside_origin)
    monkeypatch.setattr(
        "scripts.v19_release.capture_environment_identity.importlib.util.find_spec",
        lambda package: SimpleNamespace(origin=str(candidate_link)),
    )
    homemaster = _import_identity(
        "homemaster", repo_root=candidate, conda_prefix=None
    )
    assert homemaster["origin"] == str(outside_origin)
    assert homemaster["origin_within_repo"] is False

    conda_link = conda_prefix / "alfworld.py"
    conda_link.symlink_to(outside_origin)
    monkeypatch.setattr(
        "scripts.v19_release.capture_environment_identity.importlib.util.find_spec",
        lambda package: SimpleNamespace(origin=str(conda_link)),
    )
    alfworld = _import_identity(
        "alfworld", repo_root=candidate, conda_prefix=conda_prefix
    )
    assert alfworld["origin"] == str(outside_origin)
    assert alfworld["origin_within_conda_prefix"] is False


def test_baseline_capture_writes_exact_sanitized_contract_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Phase -1's source lock is tested separately below. Later additive phases
    # isolate serialization determinism from the expected production-tree drift.
    monkeypatch.setattr(
        "scripts.v19_release.capture_baseline._verify_locked_sources",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.v19_release.capture_baseline._run_pytest",
        lambda repo_root, *args: SimpleNamespace(
            returncode=0,
            stdout=(
                "tests/homemaster/test_cli_run.py::test_run_command_status_field\n"
                "tests/homemaster/test_cli_interactive.py::test_shell_status_reports_last_turn_status\n"
                "tests/homemaster/benchmarking/test_alfworld_runtime_contract.py::"
                "test_runtime_contract_requires_exact_identity\n"
                "tests/homemaster/benchmarking/coworker_demo/test_registry.py::"
                "test_registry_contains_exactly_eleven_tools_in_stable_order\n"
            ),
        ),
    )
    output = tmp_path / "baseline"
    assert (
        capture_baseline(
            repo_root=REPO_ROOT,
            openharness_root=OPENHARNESS_ROOT,
            output_dir=output,
            run_tests=False,
        )
        == 0
    )
    assert {path.name for path in output.iterdir()} == BASELINE_FILES
    surfaces = json.loads((output / "tool-surfaces.json").read_text(encoding="utf-8"))
    assert surfaces["profiles"]["coworker"]["ordered_tool_names"][4] == "observe"
    assert len(surfaces["profiles"]["coworker"]["ordered_tool_names"]) == 11
    for path in output.iterdir():
        assert str(REPO_ROOT) not in path.read_text(encoding="utf-8")

    second = tmp_path / "baseline-second"
    assert (
        capture_baseline(
            repo_root=REPO_ROOT,
            openharness_root=OPENHARNESS_ROOT,
            output_dir=second,
            run_tests=False,
        )
        == 0
    )
    assert {
        path.name: path.read_bytes() for path in output.iterdir()
    } == {path.name: path.read_bytes() for path in second.iterdir()}


def test_baseline_canonicalizes_runtime_duration_and_rejects_upstream_head_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _sanitize_output(
        "875 passed in 23.13s", repo_root=REPO_ROOT
    ) == "875 passed in <DURATION>"
    leaked = _sanitize_output(
        "Authorization: Bearer sk-phase1-secret x-api-key=abc123 env=opaque-value",
        repo_root=REPO_ROOT,
        sensitive_values=("opaque-value",),
    )
    assert "sk-phase1-secret" not in leaked
    assert "abc123" not in leaked
    assert "opaque-value" not in leaked
    assert "Authorization: [REDACTED]" == leaked
    credential_classes = _sanitize_output(
        "AWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\n"
        "Cookie: session=private-cookie\n"
        "DATABASE_URL=postgres://user:pass@example.test/db\n"
        "raw=postgres://user:pass@example.test/db\n"
        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturevalue\n"
        "client_id=public-looking-but-sensitive-client-id\n"
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOpaqueKeyMaterial\n"
        "aZ8Pq3Lw9Xk2Vm7Nc4Rt6Ys1Hd5Jf0Bu",
        repo_root=REPO_ROOT,
    )
    for secret in ("AKIAABCDEFGHIJKLMNOP", "private-cookie", "user:pass", "signaturevalue"):
        assert secret not in credential_classes
    assert "public-looking-but-sensitive-client-id" not in credential_classes
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIOpaqueKeyMaterial" not in credential_classes
    assert "aZ8Pq3Lw9Xk2Vm7Nc4Rt6Ys1Hd5Jf0Bu" not in credential_classes
    with pytest.raises(RuntimeError, match="pytest collection contains a configured secret"):
        _assert_no_sensitive_values(
            "tests/test_example.py::test_case[opaque-value]",
            sensitive_values=("opaque-value",),
            label="pytest collection",
        )

    def fake_git(repo: Path, *args: str) -> str:
        if args and args[0] == "ls-files":
            return ""
        if repo == OPENHARNESS_ROOT:
            return "f" * 40
        return HOMEMASTER_BASELINE_COMMIT

    monkeypatch.setattr("scripts.v19_release.capture_baseline._git", fake_git)
    with pytest.raises(RuntimeError, match="OpenHarness HEAD drifted"):
        _verify_locked_sources(repo_root=REPO_ROOT, openharness_root=OPENHARNESS_ROOT)

    def fake_untracked_git(repo: Path, *args: str) -> str:
        if args[:3] == ("ls-files", "--others", "--exclude-standard"):
            return "src/homemaster/new_runtime.py"
        if args and args[0] == "ls-files":
            return ""
        if repo == OPENHARNESS_ROOT:
            return OPENHARNESS_BASELINE_COMMIT
        return HOMEMASTER_BASELINE_COMMIT

    monkeypatch.setattr("scripts.v19_release.capture_baseline._git", fake_untracked_git)
    with pytest.raises(RuntimeError, match="untracked source"):
        _verify_locked_sources(repo_root=REPO_ROOT, openharness_root=OPENHARNESS_ROOT)

    def fake_ignored_git(repo: Path, *args: str) -> str:
        if args and args[0] == "ls-files":
            return "src/homemaster/injected.py" if "--ignored" in args else ""
        if repo == OPENHARNESS_ROOT:
            return OPENHARNESS_BASELINE_COMMIT
        return HOMEMASTER_BASELINE_COMMIT

    monkeypatch.setattr("scripts.v19_release.capture_baseline._git", fake_ignored_git)
    with pytest.raises(RuntimeError, match="ignored non-generated"):
        _verify_locked_sources(repo_root=REPO_ROOT, openharness_root=OPENHARNESS_ROOT)
