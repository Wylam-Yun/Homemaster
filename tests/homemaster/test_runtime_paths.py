"""Tests for validate_run_id() path safety."""

from __future__ import annotations

import pytest

from homemaster.config.runtime_paths import InvalidRunIdError, validate_run_id


def test_path_traversal_parent_dir_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="path separators"):
        validate_run_id("../x")


def test_absolute_path_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="path separators"):
        validate_run_id("/tmp/x")


def test_slash_in_name_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="path separators"):
        validate_run_id("a/b")


def test_backslash_in_name_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="path separators"):
        validate_run_id("a\\b")


def test_dot_dot_fails() -> None:
    with pytest.raises(InvalidRunIdError):
        validate_run_id("..")


def test_dot_fails() -> None:
    with pytest.raises(InvalidRunIdError):
        validate_run_id(".")


def test_empty_string_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="non-empty"):
        validate_run_id("")


def test_none_fails() -> None:
    with pytest.raises(InvalidRunIdError, match="non-empty"):
        validate_run_id(None)  # type: ignore[arg-type]


def test_valid_run_id_passes() -> None:
    assert validate_run_id("live-fetch-cup-001") == "live-fetch-cup-001"


def test_valid_run_id_with_timestamp_passes() -> None:
    assert validate_run_id("fetch_cup_retry-1778258562") == "fetch_cup_retry-1778258562"


def test_valid_run_id_with_dots_passes() -> None:
    assert validate_run_id("stage07.fetch_cup") == "stage07.fetch_cup"


def test_too_long_fails() -> None:
    with pytest.raises(InvalidRunIdError):
        validate_run_id("a" * 129)
