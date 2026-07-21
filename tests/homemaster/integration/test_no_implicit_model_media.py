from __future__ import annotations

from types import SimpleNamespace

from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.application import RunRequest
from homemaster.benchmarking.alfworld.tools import _receipt_tool_result
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


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


def test_dispatcher_does_not_convert_plain_frame_path_to_model_image(tmp_path) -> None:
    frame = tmp_path / "native-action-frame.png"
    frame.write_bytes(b"internal-evidence-only")

    def executor(*, arguments, run_context):
        del arguments, run_context
        return ToolResult(
            success=True,
            tool_name="action",
            data={"frame_path": str(frame), "evidence_ref": "action/1"},
        )

    dispatcher = ToolDispatcher()
    dispatcher.register(
        ToolSpec(
            name="action",
            description="Action with an internal native frame.",
            input_schema={"type": "object"},
            executor_mode="programmatic",
            executor=executor,
        )
    )
    context = RunContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        settings=SimpleNamespace(),
        event_sink=None,
    )
    message = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call-1", name="action", arguments={})],
        run_context=context,
    )[0]

    assert message.data is not None
    assert message.data["frame_path"] == str(frame)
    assert [block.type for block in message.content] == ["text"]
