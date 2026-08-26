from __future__ import annotations

from dataclasses import replace

import pytest

from homemaster.browser.contracts import BrowserElement, Target
from homemaster.browser.inspection import filter_elements
from homemaster.browser.targets import (
    SnapshotStore,
    TargetResolutionError,
    resolve_semantic,
    semantic_text_matches,
)


def _element(name: str = "TenantId", *, element_id: str = "e1") -> BrowserElement:
    return BrowserElement(
        element_id=element_id,
        tag="input",
        control_type="text",
        role="textbox",
        name=name,
        label=name,
        text="",
        value="",
        frame_id="f0",
        visible=True,
        enabled=True,
        editable=True,
        fingerprint=("input", "textbox", name, name, "", "", "", "text", "", "f0"),
        handle=object(),
        stable_id=f"id-{element_id}",
    )


def test_snapshot_store_retains_bounded_target_refs_across_rerender() -> None:
    store = SnapshotStore(session_id="session-a", max_snapshots=2)
    first = store.replace(
        generation=1,
        url="https://example.test",
        title="One",
        elements=[_element()],
    )
    second = store.replace(
        generation=1,
        url="https://example.test",
        title="Two",
        elements=[_element()],
    )
    first_ref = first.elements[0].target_ref
    assert first_ref is not None
    assert store.resolve_target_ref(first_ref)[0].snapshot_id == first.snapshot_id

    store.invalidate()
    assert store.resolve_target_ref(first_ref)[1].name == "TenantId"

    store.replace(
        generation=1,
        url="https://example.test",
        title="Three",
        elements=[_element()],
    )
    with pytest.raises(TargetResolutionError) as expired:
        store.resolve_target_ref(first_ref)
    assert expired.value.code == "stale_ref"
    assert second.snapshot_id in {snapshot.snapshot_id for snapshot in store.snapshots}


def test_snapshot_store_rejects_generation_and_unknown_element() -> None:
    store = SnapshotStore(session_id="session-a")
    snapshot = store.replace(
        generation=3,
        url="https://example.test",
        title="Page",
        elements=[_element()],
    )
    with pytest.raises(TargetResolutionError) as stale:
        store.resolve(snapshot.snapshot_id, "e1", generation=4)
    assert stale.value.code == "stale_ref"
    with pytest.raises(TargetResolutionError) as missing:
        store.resolve(snapshot.snapshot_id, "e9", generation=3)
    assert missing.value.code == "target_not_found"


def test_target_empty_semantic_fields_are_ignored() -> None:
    target = Target.from_value({"role": "", "name": "2026-08-21", "nth": 0})

    assert target.role is None
    assert target.name == "2026-08-21"
    assert target.nth == 0


def test_semantic_resolver_exact_contains_regex_and_ambiguity() -> None:
    elements = [
        _element("Apply", element_id="e1"),
        _element("Apply all", element_id="e2"),
    ]
    exact = resolve_semantic(
        elements,
        Target(role="textbox", name="Apply"),
        writable=True,
    )
    assert exact.element.element_id == "e1"
    assert exact.level == "exact"

    with pytest.raises(TargetResolutionError) as ambiguous:
        resolve_semantic(
            elements,
            Target(role="textbox", name="Apply", match="contains"),
            writable=True,
        )
    assert ambiguous.value.code == "target_ambiguous"
    assert len(ambiguous.value.details["candidates"]) == 2

    regex = resolve_semantic(
        elements,
        Target(name=r"Apply( all)?", match="regex", nth=1),
        writable=False,
    )
    assert regex.element.element_id == "e2"
    with pytest.raises(TargetResolutionError) as rejected:
        resolve_semantic(
            elements,
            Target(name="Apply.*", match="regex"),
            writable=True,
        )
    assert rejected.value.code == "invalid_match"


def test_semantic_resolver_treats_cell_and_gridcell_as_compatible_roles() -> None:
    element = _element("21")
    element = replace(element, role="cell", text="21")
    assert resolve_semantic(
        [element], Target(role="gridcell", text="21"), writable=True
    ).element.element_id == element.element_id

    named_date = replace(element, name="date 2026-08-21 00:00:00")
    assert resolve_semantic(
        [named_date], Target(role="gridcell", name="21"), writable=True
    ).element.element_id == named_date.element_id


def test_inspection_filter_treats_date_cells_as_compatible_and_matches_visible_day() -> None:
    element = replace(_element("date"), role="cell", name="date 2026-08-21 00:00:00", text="21")

    selected = filter_elements(
        [element], {"role": "gridcell", "name": "21", "match": "exact"}
    )

    assert [item.element_id for item in selected] == [element.element_id]


def test_semantic_text_matching_ignores_only_han_display_spacing() -> None:
    assert semantic_text_matches("确 认", "确认", "exact") is True
    assert semantic_text_matches("请确\t认操作", "确认", "contains") is True

    assert semantic_text_matches("Apply all", "Applyall", "exact") is False
    assert semantic_text_matches("Apply all", "Applyall", "contains") is False


def test_semantic_text_regex_uses_raw_display_text() -> None:
    assert semantic_text_matches("确 认", r"确认", "regex") is False
    assert semantic_text_matches("确 认", r"确\s认", "regex") is True
