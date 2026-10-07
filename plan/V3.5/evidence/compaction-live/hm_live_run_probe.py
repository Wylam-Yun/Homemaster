"""End-to-end live run probe: REAL ApplicationRuntime + real provider +
real agent loop, where summary compaction must trigger naturally mid-run.

Black-box terminal assertions (no internals mocked):
  1. tool executor counter — the model really called scan_waypoint >= 8x
  2. run result REPLIED
  3. context.compaction event fired on the real event bus
  4. session.json on disk: agent_state.compaction artifact (first_kept_index>0,
     head_messages present), usage_anchor with real input_tokens, and
     agentscope_state.context still append-only (canonical kept full history)
  5. second resumed run: prepares the durable head without a re-fold storm.
"""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "third_party/MindMemOS/src/mindmemos")

from homemaster.config import load_config
from homemaster.application.factory import create_application
from homemaster.application.contracts import RunRequest, RunStatus
from homemaster.events.bus import EventBus
from homemaster.tools.base import ToolRegistry
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.contracts import (
    RegisteredTool, ToolDefinition, ToolProvenance, ToolExecutionResult,
    ToolExecutionStatus, VerificationPolicy,
)

WORK = Path("/tmp/hm_live_run")
MARKER = WORK / "scan_marker.txt"
SID = "live-compact-e2e"


class ScanExecutor:
    def __init__(self):
        self.calls = 0

    async def execute(self, arguments, context):
        i = int(arguments["index"])
        self.calls += 1
        MARKER.write_text(f"last={i} calls={self.calls}", encoding="utf-8")
        blob = f"waypoint {i} occupancy grid " + "free-space segment data " * 120
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text=blob, backend_attempted=True
        )


def _scan_tool(executor):
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id="probe.scan_waypoint.v1",
            model_alias="scan_waypoint",
            description="Scan one waypoint of the room map and return its occupancy grid.",
            input_schema={
                "type": "object",
                "properties": {"index": {"type": "integer"}},
                "required": ["index"],
            },
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="test", reference="probe"),
            version="1.0.0",
        ),
        executor=executor,
    )


async def main() -> int:
    config = load_config("config/homemaster.yaml")
    for item in config.providers.items:
        if item.name.casefold() == "mimo" and item.kind == "chat":
            item.context_window_tokens = 16000
    config.context = config.context.model_copy(update={
        "output_reserve_tokens": 2000,
        "safety_buffer_tokens": 2000,
        "default_keep_recent_tool_results": 50,  # keep micro from rescuing; force summary path
    })
    config.memory.enabled = False
    config.observability.session_dir = str(WORK / "sessions")
    if config.runtime.max_tool_iterations is None:
        config.runtime.max_tool_iterations = 25

    executor = ScanExecutor()
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(_scan_tool(executor))])
    bus = EventBus()
    app = create_application(config=config, registry=registry, event_bus=bus)

    req = RunRequest(
        text=(
            "Use the scan_waypoint tool to scan waypoint indices 0 through 11, "
            "one call each, in order. When all results are back, reply DONE."
        ),
        session_id=SID,
    )
    result = await app.run(req)
    print("run status:", result.status)
    print("tool calls:", executor.calls)
    assert result.status is RunStatus.REPLIED, f"run failed: {result.error_code} {result.metadata}"
    assert executor.calls >= 8, "model did not drive enough tool rounds"

    types = [e.type for e in bus.events]
    comp_events = [t for t in types if "compaction" in t]
    print("compaction events:", comp_events)
    assert comp_events, "no context.compaction event fired during a real run"

    snap = json.loads((WORK / "sessions" / SID / "session.json").read_text())
    agent_state = snap["agent_state"]
    art = agent_state.get("compaction")
    anchor = agent_state.get("usage_anchor")
    usage = agent_state.get("provider_usage") or agent_state.get("usage")
    print("artifact first_kept:", (art or {}).get("first_kept_index"))
    print("anchor:", anchor)
    print("provider_usage:", usage)
    canon_len = len(snap["agentscope_state"]["context"])
    print("canonical context len:", canon_len)
    assert art and art.get("first_kept_index", 0) > 0, "no durable artifact in snapshot"
    head_text = json.dumps(art.get("head_messages") or [])
    assert "END OF CONTEXT SUMMARY" in head_text, "head has no summary marker"
    assert anchor and anchor.get("input_tokens", 0) > 0, "usage anchor missing"

    print("--- resume run ---")
    req2 = RunRequest(
        text="Scan waypoint index 12 with scan_waypoint, then reply DONE.",
        session_id=SID, resume=True,
    )
    result2 = await app.run(req2)
    print("run2 status:", result2.status, "calls now:", executor.calls)
    comp_events2 = [e.type for e in bus.events if "compaction" in e]
    print("compaction events after resume:", comp_events2)
    assert result2.status is RunStatus.REPLIED
    assert len(comp_events2) == len(comp_events), "resume re-folded — artifact not durable"

    await app.aclose()
    print("LIVE RUN PROBE PASS")
    return 0


sys.exit(asyncio.run(main()))
