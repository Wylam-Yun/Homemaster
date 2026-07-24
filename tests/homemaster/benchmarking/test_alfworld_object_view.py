from __future__ import annotations

from types import SimpleNamespace

from homemaster.benchmarking.alfworld.object_view import CurrentObjectView

OBJECT = "Mug|+00.10|+00.90|+00.20"


def test_current_object_view_reads_visibility_from_the_current_thor_event() -> None:
    event = SimpleNamespace(
        metadata={"objects": [{"objectId": OBJECT, "visible": True}]},
        instance_detections2D={OBJECT: [0, 0, 3, 2]},
    )

    result = CurrentObjectView(event=event, event_sequence=4).read(OBJECT)

    assert result.status == "ok"
    assert result.event_sequence == 4
    assert result.strict_visible is True
    assert result.bbox_area == 6.0


def test_current_object_view_does_not_depend_on_screenshot_or_provider_metadata() -> None:
    event = SimpleNamespace(
        metadata={"objects": [{"objectId": OBJECT, "visible": False}]},
        instance_detections2D={},
    )

    result = CurrentObjectView(event=event, event_sequence=5).read(OBJECT)

    assert result.status == "ok"
    assert result.strict_visible is False
