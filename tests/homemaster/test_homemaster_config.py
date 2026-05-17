"""Tests for homemaster config loading, validation, and override behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from homemaster.runtime import (
    RuntimeConfigError,
    get_config_section,
    load_homemaster_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = str(Path(__file__).resolve().parents[2] / "src")


def _write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "homemaster.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _run_in_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    """Run a Python script in an isolated subprocess.

    This avoids importlib.reload() contaminating other test files in the
    same process — reload creates new class objects, breaking mock-transport
    bindings in tests that imported those classes earlier.
    """
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHONPATH": _REPO_ROOT},
    )


def _assert_subprocess_pass(tmp_path: Path, body: str) -> None:
    """Write config, run body in subprocess, assert exit 0."""
    script = f"""
import importlib, json
from pathlib import Path

tmp = Path("{tmp_path}")
cfg_path = tmp / "homemaster.json"

import homemaster.runtime as rt
rt.HOMEMASTER_CONFIG_PATH = cfg_path

{body}
"""
    result = _run_in_subprocess(script)
    if result.returncode != 0:
        pytest.fail(
            f"subprocess failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _assert_subprocess_raises(tmp_path: Path, body: str, match: str) -> None:
    """Run body in subprocess, assert it raises RuntimeConfigError matching *match*."""
    script = f"""
import importlib, json, sys
from pathlib import Path

tmp = Path("{tmp_path}")
cfg_path = tmp / "homemaster.json"

import homemaster.runtime as rt
rt.HOMEMASTER_CONFIG_PATH = cfg_path

try:
{body}
    print("DID_NOT_RAISE")
    sys.exit(1)
except rt.RuntimeConfigError as e:
    msg = str(e)
    if {match!r} not in msg:
        print(f"WRONG_MESSAGE: {{msg}}")
        sys.exit(2)
    sys.exit(0)
"""
    result = _run_in_subprocess(script)
    if result.returncode == 1:
        pytest.fail(
            f"expected RuntimeConfigError but no exception raised.\n"
            f"stdout: {result.stdout}"
        )
    elif result.returncode == 2:
        pytest.fail(f"error message mismatch.\nstdout: {result.stdout}\nstderr: {result.stderr}")
    elif result.returncode != 0:
        pytest.fail(
            f"subprocess failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 8a. Loader unit tests
# ---------------------------------------------------------------------------


def test_missing_config_returns_empty_dict(tmp_path: Path) -> None:
    result = load_homemaster_config(tmp_path / "nonexistent.json")
    assert result == {}


def test_valid_config_returns_dict(tmp_path: Path) -> None:
    p = _write_config(tmp_path, {"token_budget": {"max_llm_attempts": 5}})
    result = load_homemaster_config(p)
    assert result["token_budget"]["max_llm_attempts"] == 5


def test_invalid_json_raises_config_error(tmp_path: Path) -> None:
    p = tmp_path / "homemaster.json"
    p.write_text("{bad json}", encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match="invalid homemaster config JSON"):
        load_homemaster_config(p)


def test_non_dict_json_raises_config_error(tmp_path: Path) -> None:
    p = tmp_path / "homemaster.json"
    p.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match="must be a JSON object"):
        load_homemaster_config(p)


# ---------------------------------------------------------------------------
# 8b. Section validator tests
# ---------------------------------------------------------------------------


def test_get_config_section_returns_subsection() -> None:
    config = {"token_budget": {"max_llm_attempts": 5}}
    assert get_config_section(config, "token_budget") == {"max_llm_attempts": 5}


def test_get_config_section_missing_returns_none() -> None:
    assert get_config_section({}, "token_budget") is None


def test_get_config_section_wrong_type_raises() -> None:
    with pytest.raises(RuntimeConfigError, match="must be a JSON object"):
        get_config_section({"token_budget": "not a dict"}, "token_budget")


def test_section_wrong_type_array_raises() -> None:
    with pytest.raises(RuntimeConfigError, match="must be a JSON object"):
        get_config_section({"token_budget": [1, 2, 3]}, "token_budget")


# ---------------------------------------------------------------------------
# 8c. Override tests — prove "调参不用改源码"
#
# These run in subprocesses to avoid importlib.reload() contaminating
# other test files in the same process.
# ---------------------------------------------------------------------------


def test_token_budget_override(tmp_path: Path) -> None:
    _write_config(tmp_path, {"token_budget": {"max_llm_attempts": 7}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.token_budget as mod
importlib.reload(mod)
assert mod.MAX_LLM_ATTEMPTS == 7, f"expected 7, got {mod.MAX_LLM_ATTEMPTS}"
assert mod.INITIAL_MAX_TOKENS["stage_01_smoke"] == 4096, "unchanged key should stay 4096"
""")


def test_token_budget_partial_override(tmp_path: Path) -> None:
    _write_config(tmp_path, {"token_budget": {"initial_max_tokens": {"stage_01_smoke": 2048}}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.token_budget as mod
importlib.reload(mod)
assert mod.MAX_LLM_ATTEMPTS == 3, f"expected 3, got {mod.MAX_LLM_ATTEMPTS}"
assert mod.INITIAL_MAX_TOKENS["stage_01_smoke"] == 2048
assert mod.INITIAL_MAX_TOKENS["stage_05_orchestration"] == 16384, "unchanged key"
""")


def test_scoring_override(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "retrieval_scoring": {
                "rrf_k": 30,
                "metadata_weights": {"target_category_match": 0.5},
            }
        },
    )
    _assert_subprocess_pass(tmp_path, """
import homemaster.memory_rag as mod
importlib.reload(mod)
assert mod.RRF_K == 30, f"expected 30, got {mod.RRF_K}"
assert mod.METADATA_WEIGHT_CATEGORY == 0.5, f"expected 0.5, got {mod.METADATA_WEIGHT_CATEGORY}"
assert mod.METADATA_WEIGHT_ALIAS == 0.2, "unchanged weight"
""")


def test_grounding_merge_override(tmp_path: Path) -> None:
    """Config adds a room without losing defaults."""
    _write_config(tmp_path, {"grounding": {"room_hints": {"balcony": ["阳台"]}}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.grounding as mod
importlib.reload(mod)
assert "balcony" in mod.ROOM_HINTS, "new key should be added"
assert mod.ROOM_HINTS["balcony"] == ("阳台",), f"got {mod.ROOM_HINTS['balcony']}"
assert mod.ROOM_HINTS["kitchen"] == ("厨房", "kitchen"), "default preserved"
""")


def test_provider_client_timeout_override(tmp_path: Path) -> None:
    _write_config(tmp_path, {"provider_client": {"timeout_s": 120.0}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.llm_client as mod
importlib.reload(mod)
assert mod._DEFAULT_TIMEOUT_S == 120.0, f"expected 120.0, got {mod._DEFAULT_TIMEOUT_S}"
assert mod._DEFAULT_CONNECT_TIMEOUT_S == 10.0, "unchanged"
""")


def test_executor_override(tmp_path: Path) -> None:
    _write_config(tmp_path, {"executor": {"step_multiplier": 5, "minimum_max_steps": 10}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.stages.executor as mod
importlib.reload(mod)
assert mod.STEP_MULTIPLIER == 5, f"expected 5, got {mod.STEP_MULTIPLIER}"
assert mod.MINIMUM_MAX_STEPS == 10, f"expected 10, got {mod.MINIMUM_MAX_STEPS}"
""")


def test_runtime_defaults_live_models_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"live_models": True}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_defaults_config()
""", "no longer supported")


def test_runtime_defaults_mock_skills_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"mock_skills": True}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_defaults_config()
""", "no longer supported")


def test_runtime_defaults_skill_mode_simulated_ok(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"skill_mode": "simulated"}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.runtime as rt
result = rt.load_runtime_defaults_config()
assert result.get("skill_mode") == "simulated"
""")


def test_runtime_defaults_skill_mode_real_ok(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"skill_mode": "real"}})
    _assert_subprocess_pass(tmp_path, """
import homemaster.runtime as rt
result = rt.load_runtime_defaults_config()
assert result.get("skill_mode") == "real"
""")


def test_runtime_defaults_skill_mode_invalid_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"skill_mode": "invalid"}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_defaults_config()
""", "must be 'simulated' or 'real'")


def test_runtime_defaults_skill_mode_bad_type_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"skill_mode": 123}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_defaults_config()
""", "must be str")


# ---------------------------------------------------------------------------
# 8d. Validation failure tests — bad config must fail fast
# ---------------------------------------------------------------------------


def test_token_budget_bad_max_attempts_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"token_budget": {"max_llm_attempts": -1}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.token_budget as mod
    importlib.reload(mod)
""", "positive int")


def test_token_budget_non_int_max_attempts_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"token_budget": {"max_llm_attempts": "three"}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.token_budget as mod
    importlib.reload(mod)
""", "positive int")


def test_token_budget_bad_token_value_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path, {"token_budget": {"initial_max_tokens": {"stage_01_smoke": -100}}}
    )
    _assert_subprocess_raises(tmp_path, """
    import homemaster.token_budget as mod
    importlib.reload(mod)
""", "positive int")


def test_scoring_rrf_k_zero_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"retrieval_scoring": {"rrf_k": 0}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.memory_rag as mod
    importlib.reload(mod)
""", "positive int")


def test_scoring_top_k_out_of_range_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"retrieval_scoring": {"top_k_limit": 100}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.memory_rag as mod
    importlib.reload(mod)
""", "must be int in [1, 50]")


def test_scoring_weight_bad_type_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {"retrieval_scoring": {"metadata_weights": {"target_category_match": "high"}}},
    )
    _assert_subprocess_raises(tmp_path, """
    import homemaster.memory_rag as mod
    importlib.reload(mod)
""", "must be a number")


def test_grounding_bad_element_type_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path, {"grounding": {"room_hints": {"kitchen": [123]}}}
    )
    _assert_subprocess_raises(tmp_path, """
    import homemaster.grounding as mod
    importlib.reload(mod)
""", "must be a string")


def test_grounding_non_array_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path, {"grounding": {"room_hints": {"kitchen": "厨房"}}}
    )
    _assert_subprocess_raises(tmp_path, """
    import homemaster.grounding as mod
    importlib.reload(mod)
""", "must be a JSON array")


def test_provider_client_negative_timeout_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"provider_client": {"timeout_s": -5}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_provider_client_config()
""", "must be > 0")


def test_provider_client_string_timeout_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"provider_client": {"timeout_s": "slow"}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_provider_client_config()
""", "must be")


def test_runtime_paths_bad_type_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_paths": {"debug_root": 12345}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_paths_config()
""", "must be a string or null")


def test_runtime_defaults_bad_type_live_models_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"runtime_defaults": {"live_models": "yes"}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.runtime as rt
    rt.load_runtime_defaults_config()
""", "no longer supported")


def test_executor_bad_step_multiplier_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"executor": {"step_multiplier": "three"}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.stages.executor as mod
    importlib.reload(mod)
""", "positive int")


def test_executor_negative_step_multiplier_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, {"executor": {"step_multiplier": -1}})
    _assert_subprocess_raises(tmp_path, """
    import homemaster.stages.executor as mod
    importlib.reload(mod)
""", "positive int")


# ---------------------------------------------------------------------------
# 8e. Defaults regression — no config file → all modules use code defaults
# ---------------------------------------------------------------------------


def test_defaults_without_config(tmp_path: Path) -> None:
    """No config file → all modules use code defaults.

    Runs in subprocess to guarantee a clean import with no prior reload side-effects.
    """
    nonexistent = tmp_path / "nonexistent"
    script = f"""
import importlib, sys
from pathlib import Path

# Point config to nonexistent file
import homemaster.runtime as rt
rt.HOMEMASTER_CONFIG_PATH = Path("{nonexistent}")

# Reload consumers so they re-read config (which returns {{}})
for mod_name in (
    "homemaster.runtime",
    "homemaster.token_budget",
    "homemaster.memory_rag",
    "homemaster.grounding",
    "homemaster.llm_client",
    "homemaster.embedding_client",
    "homemaster.stages.executor",
):
    importlib.reload(importlib.import_module(mod_name))

from homemaster.token_budget import INITIAL_MAX_TOKENS, MAX_LLM_ATTEMPTS
from homemaster.memory_rag import METADATA_WEIGHT_CATEGORY, RRF_K, TOP_K_LIMIT
from homemaster.grounding import ANCHOR_HINTS, ROOM_HINTS
from homemaster.runtime import (
    DEFAULT_PROVIDER_NAME,
    DEFAULT_EMBEDDING_PROVIDER_NAME,
    DEFAULT_SKILL_MODE,
)
from homemaster.stages.executor import STEP_MULTIPLIER, MINIMUM_MAX_STEPS

assert MAX_LLM_ATTEMPTS == 3, f"MAX_LLM_ATTEMPTS={{MAX_LLM_ATTEMPTS}}"
assert INITIAL_MAX_TOKENS["stage_01_smoke"] == 4096
assert INITIAL_MAX_TOKENS["stage_05_orchestration"] == 16384
assert METADATA_WEIGHT_CATEGORY == 0.2
assert RRF_K == 60
assert TOP_K_LIMIT == 50
assert ROOM_HINTS["kitchen"] == ("厨房", "kitchen")
assert "桌子" in ANCHOR_HINTS["table"]
assert DEFAULT_PROVIDER_NAME == "Mimo"
assert DEFAULT_SKILL_MODE == "simulated"
assert STEP_MULTIPLIER == 3
assert MINIMUM_MAX_STEPS == 3
assert DEFAULT_EMBEDDING_PROVIDER_NAME == "MemoryEmbedding"
"""
    result = _run_in_subprocess(script)
    if result.returncode != 0:
        pytest.fail(
            f"defaults regression failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
