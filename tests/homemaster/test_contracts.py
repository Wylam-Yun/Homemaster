from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from homemaster.contracts import TaskCard, VLMImageInput


def _valid_task_card() -> dict[str, object]:
    return {
        "task_type": "check_presence",
        "target": "药盒",
        "delivery_target": None,
        "location_hint": "桌子那边",
        "success_criteria": ["观察到药盒是否还在桌子附近"],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.88,
    }


def test_task_card_serializes_and_validates_json() -> None:
    task_card = TaskCard.model_validate(_valid_task_card())
    encoded = task_card.model_dump_json()

    decoded = TaskCard.model_validate_json(encoded)

    assert decoded == task_card
    assert json.loads(encoded)["target"] == "药盒"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_type", "inspect_object"),
        ("target", "  "),
        ("success_criteria", []),
        ("confidence", 1.2),
    ],
)
def test_task_card_rejects_invalid_contract_values(field: str, value: object) -> None:
    payload = _valid_task_card()
    payload[field] = value

    with pytest.raises(ValidationError):
        TaskCard.model_validate(payload)


def test_task_card_forbids_extra_fields() -> None:
    payload = _valid_task_card()
    payload["invented_field"] = "should fail"

    with pytest.raises(ValidationError):
        TaskCard.model_validate(payload)


def test_vlm_image_input_defaults_to_disabled_and_serializes() -> None:
    image_input = VLMImageInput()

    assert image_input.enabled is False
    assert VLMImageInput.model_validate_json(image_input.model_dump_json()) == image_input
