from homemaster.tools import ToolResult
from homemaster.tools.contracts import ToolExecutionStatus
from scripts.v20.verify_skill_installation import _GateResult


def test_gate_result_uses_canonical_error_flag_when_status_metadata_is_absent() -> None:
    success = _GateResult(ToolResult("loaded", False, {"name": "example"}))
    failure = _GateResult(ToolResult("missing", True, {"name": "example"}))

    assert success.status is ToolExecutionStatus.SUCCESS
    assert failure.status is ToolExecutionStatus.FAILURE
