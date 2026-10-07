# Live compaction run evidence (v35-architecture)

`hm_live_run_probe.py` — real `ApplicationRuntime.run()` + real Mimo provider
(`config/homemaster.yaml`, window shrunk to 16000 + reserve/buffer 2000 so the
fold fires mid-run). Terminal state observed:

- run REPLIED after 12 real `scan_waypoint` tool calls; `context.compaction`
  fired once mid-run on the real event bus
- on-disk `revisions/…03.json`: `agent_state.compaction` = {first_kept_index:10,
  kind:summary, head_messages×4=[summary, protected prefix verbatim incl. the
  instruction]}; `usage_anchor.input_tokens=3855` (real provider number) with
  prefix/tail/tools fingerprints; canonical messages append-only

`hm_live_resume_probe.py` — fresh process, `resume=True`, one more turn:
zero compaction events (no re-fold), first_kept stable, anchor re-anchored on
new real usage (canonical_len 15→23, input_tokens 3855→5033), canonical 16→24.

Caveat: the mimo-v2.6-flash summary text was degenerate ("DONE") — mechanical
pipeline correct (finish=stop accepted, fold committed), summary quality is a
model-capability issue, not a boundary/budget bug.
