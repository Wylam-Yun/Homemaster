from __future__ import annotations

from homemaster.application import RunRequest
from homemaster.benchmarking.alfworld.tools import _receipt_tool_result


def test_initial_prompt_and_alfworld_action_receipt_have_no_image() -> None:
    request = RunRequest(text="task", profile="alfworld")
    assert request.text == "task"
    assert not hasattr(request, "user_content")

    result = _receipt_tool_result(
        name="robot_go_to",
        success=True,
        data={"success": True, "evidence_ref": "action/1"},
    )
    assert [block.type for block in result.content] == ["text"]
