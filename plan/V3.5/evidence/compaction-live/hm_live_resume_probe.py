"""Resume leg: reload the session snapshot from disk in a FRESH process
(real resume — nothing in memory), run one more turn, assert the durable
artifact projects the head + live tail and does NOT re-fold."""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "third_party/MindMemOS/src/mindmemos")

exec(open("/tmp/hm_live_run_probe.py").read().split("async def main")[0])

async def main() -> int:
    config = load_config("config/homemaster.yaml")
    for item in config.providers.items:
        if item.name.casefold() == "mimo" and item.kind == "chat":
            item.context_window_tokens = 16000
    config.context = config.context.model_copy(update={
        "output_reserve_tokens": 2000,
        "safety_buffer_tokens": 2000,
        "default_keep_recent_tool_results": 50,
    })
    config.memory.enabled = False
    config.observability.session_dir = str(WORK / "sessions")

    executor = ScanExecutor()
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(_scan_tool(executor))])
    bus = EventBus()
    app = create_application(config=config, registry=registry, event_bus=bus)

    result = await app.run(RunRequest(
        text="Scan waypoint index 12 with scan_waypoint, then reply DONE.",
        session_id=SID, resume=True,
    ))
    print("resume status:", result.status, "| calls:", executor.calls)
    assert result.status is RunStatus.REPLIED
    assert executor.calls >= 1

    comps = [e.type for e in bus.events if "compaction" in e.type]
    print("compaction events in resumed run:", comps)
    assert not comps, "resumed run re-folded — artifact was not durable"

    snap = json.loads((WORK/"sessions"/SID/"revisions").joinpath(
        json.loads((WORK/"sessions"/SID/"latest.json").read_text())["revision_path"]
    ).read_text())
    as_ = snap.get("agent_state") or {}
    art = as_.get("compaction") or {}
    print("post-resume artifact first_kept:", art.get("first_kept_index"),
          "anchor:", as_.get("usage_anchor"))
    msgs = snap.get("messages") or []
    print("canonical messages:", len(msgs))
    assert art.get("first_kept_index") == 10, "artifact boundary moved"
    await app.aclose()
    print("RESUME LEG PASS")
    return 0

sys.exit(asyncio.run(main()))
