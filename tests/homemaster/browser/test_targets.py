from __future__ import annotations

import pytest

from homemaster.browser.contracts import BrowserElement
from homemaster.browser.targets import SnapshotStore, TargetResolutionError


def _element(name: str = "TenantId") -> BrowserElement:
    return BrowserElement(
        element_id="e1",
        tag="input",
        control_type="input",
        role="textbox",
        name=name,
        label=name,
        text="",
        value="",
        frame_id="main",
        visible=True,
        enabled=True,
        editable=True,
        fingerprint=("input", "textbox", name, name, "", "", "main"),
        handle=object(),
    )


def test_snapshot_store_only_accepts_latest_exact_target() -> None:
    store = SnapshotStore(session_id="session-a")
    first = store.replace(
        generation=1, url="https://example.test", title="One", elements=[_element()]
    )
    second = store.replace(
        generation=1, url="https://example.test", title="Two", elements=[_element()]
    )

    with pytest.raises(TargetResolutionError, match="stale_ref"):
        store.resolve(first.snapshot_id, "e1", generation=1)
    assert store.resolve(second.snapshot_id, "e1", generation=1).name == "TenantId"

    store.invalidate()
    with pytest.raises(TargetResolutionError, match="stale_ref"):
        store.resolve(second.snapshot_id, "e1", generation=1)


def test_snapshot_store_rejects_generation_and_unknown_element() -> None:
    store = SnapshotStore(session_id="session-a")
    snapshot = store.replace(
        generation=3,
        url="https://example.test",
        title="Page",
        elements=[_element()],
    )

    with pytest.raises(TargetResolutionError, match="stale_ref"):
        store.resolve(snapshot.snapshot_id, "e1", generation=4)
    with pytest.raises(TargetResolutionError, match="unknown_element"):
        store.resolve(snapshot.snapshot_id, "e9", generation=3)
