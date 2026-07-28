from __future__ import annotations

import sys
from importlib import metadata

import pytest

from homemaster.memory.vendor_integrity import (
    VendoredMem0IntegrityError,
    verify_vendored_mem0,
)


def test_vendored_mem0_is_owned_by_homemaster_and_matches_manifest() -> None:
    verify_vendored_mem0.cache_clear()
    sys.modules.pop("mem0", None)
    with pytest.raises(metadata.PackageNotFoundError):
        metadata.distribution("mem0ai")

    package_root = verify_vendored_mem0()

    assert package_root.name == "mem0"
    assert "mem0" not in sys.modules
    assert package_root.joinpath("memory", "main.py").is_file()
    assert package_root.joinpath("vector_stores", "qdrant.py").is_file()


def test_vendored_mem0_rejects_external_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    verify_vendored_mem0.cache_clear()
    monkeypatch.setattr(metadata, "distribution", lambda name: object())

    with pytest.raises(VendoredMem0IntegrityError, match="external mem0ai"):
        verify_vendored_mem0()

    verify_vendored_mem0.cache_clear()
