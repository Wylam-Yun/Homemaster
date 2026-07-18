from __future__ import annotations

import pytest

from homemaster.benchmarking.alfworld.translator import (
    TranslatorValidationError,
    create_translator,
)


def test_textworld_translator_maps_core_actions() -> None:
    translator = create_translator("AlfredTWEnv")

    assert translator.observe(mode="look") == "look"
    assert translator.observe(mode="inventory") == "inventory"
    assert translator.observe(mode="examine", target="apple 1") == "examine apple 1"
    assert translator.navigate(target_receptacle="countertop 1") == "go to countertop 1"
    assert translator.manipulate(
        action="take",
        object="apple 1",
        source_receptacle="countertop 1",
    ) == "take apple 1 from countertop 1"
    assert translator.manipulate(
        action="put",
        object="apple 1",
        target_receptacle="diningtable 1",
    ) == "move apple 1 to diningtable 1"
    assert translator.manipulate(
        action="heat",
        object="mug 1",
        tool_receptacle="microwave 1",
    ) == "heat mug 1 with microwave 1"


def test_translator_rejects_missing_conditional_arguments() -> None:
    translator = create_translator("AlfredTWEnv")

    with pytest.raises(TranslatorValidationError, match="source_receptacle"):
        translator.manipulate(action="take", object="apple 1")

    with pytest.raises(TranslatorValidationError, match="target_receptacle"):
        translator.manipulate(action="put", object="apple 1")

    with pytest.raises(TranslatorValidationError, match="tool_receptacle"):
        translator.manipulate(action="clean", object="mug 1")


def test_public_action_schema_contains_textworld_put_template() -> None:
    translator = create_translator("AlfredTWEnv")

    schema = translator.public_action_schema()
    assert schema["navigation"] == {
        "tool": "robot_go_to",
        "required": ["target"],
        "command_template": "go to {target}",
    }
    put_actions = [
        item for item in schema["manipulation_actions"]
        if item["action"] == "put"
    ]

    assert put_actions[0]["command_template"] == "move {object} to {target_receptacle}"


def test_public_action_schema_warns_about_abstract_state_change_actions() -> None:
    translator = create_translator("AlfredThorEnv")

    schema = translator.public_action_schema()
    actions = {
        item["action"]: item
        for item in schema["manipulation_actions"]
    }

    assert "holding the object" in actions["heat"]["notes"]
    assert "Do not open, put into, close, or use the microwave" in actions["heat"]["notes"]
    assert "stand at the fridge while holding the object" in actions["cool"]["notes"]
    assert "stand at a sinkbasin while holding the object" in actions["clean"]["notes"]
    assert "Use only for switch/toggle objects" in actions["use"]["notes"]
