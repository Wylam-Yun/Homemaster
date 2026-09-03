"""Compile a reusable ProcedureRecord from a ticket and a successful run trace."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from homemaster.memory.models import ProcedureRecord
from homemaster.providers.llm_client import LLMClient

_FAILED_DROP = {"browser_find", "browser_inspect", "browser_wait", "browser_scroll"}
_ALWAYS_DROP = {"browser_inspect", "browser_scroll"}
_KEEP_ARG_KEYS = ("target", "name", "text", "role", "label", "value", "url", "command")
_MAX_RESULT_CHARS = 400
_MAX_TOOLS_PER_PHASE = 80

COMPILER_SYSTEM = """你只蒸馏当前这一个 SOP 步骤的成功 HOW，输出一份 ProcedureRecord JSON。
不要把整张变更单编成一条流程。只输出一个 JSON object，不要 markdown。

输入里只有：
- 当前 SOP 步骤的票面说明（operate_description / operate_verified）
- 当前 phase 的工具列表（已丢掉明显失败的 find/inspect）
- 可用参数槽候选（来自整张票，本步骤用到的才放进 inputs）

规则：
1. 这是「这一步实际怎么点成功的」，不是票面复述，也不是工具回放。
2. 票面要求但本段 trace 里成功做过的，必须留下
   （例如 Region、告警级别、时间窗、云服务）。漏掉查询条件 = 失败。
3. 折叠：日期+时分秒控件收成一步 set_datetime；确认框收成 confirm；终端 grep 收成一步 terminal。
4. 丢掉：失败的 find/inspect、重复空转 wait、被同一步里后来动作覆盖的试探 click。
5. 目标步数大约 6–12 步。不要为每个 inspect 生成一步。
6. 参数进 inputs，steps 里用 {slot}。禁止写死 1.0.0、具体时间戳、WSO id、IP、端口、绝对文件路径。
   终端命令路径用槽，例如 {agent_conf_path}。
7. target 只用 page_name/role/name/text/label/command_template。
   禁止 ref、element_id、snapshot_id、css、url。
8. action 只能是: open_page, click, confirm, select, fill, set_datetime, wait, read, terminal。
9. 每步 expect 必须有至少一个非空条件。expect.field 只能 label+equals。
   use_input 必须是本条 inputs[].name 的 snake_case，不能是中文。
10. note 只写票面没有、但本段 trace 证实有用的极短提示
    （例如时间格无障碍名是 hour HH / minute mm / second ss）。
11. sop_id 用当前步骤的 sop_step_id，不要把多个 STEP 拼在一起。
12. entry.menu_path 要填左侧怎么点进这一台。route_hint 只能是 path。
13. abort_when / success 只写本 SOP 步骤的边界，用槽。
"""


def _parse_ts(value: object) -> str:
    return str(value or "")


def _compact_args(args: object) -> dict[str, Any]:
    if not isinstance(args, Mapping):
        return {}
    out: dict[str, Any] = {}
    target = args.get("target")
    if isinstance(target, Mapping):
        cleaned = {
            key: val
            for key, val in target.items()
            if key in {"role", "name", "text", "label", "page_name"}
            and val not in (None, "", [], {})
        }
        if cleaned:
            out["target"] = cleaned
    for key in _KEEP_ARG_KEYS:
        if key == "target":
            continue
        val = args.get(key)
        if val not in (None, "", [], {}):
            out[key] = val
    return out


def _compact_result(payload: Mapping[str, Any]) -> str:
    if payload.get("is_error"):
        return f"ERROR: {str(payload.get('result') or '')[:_MAX_RESULT_CHARS]}"
    data = payload.get("data")
    if isinstance(data, Mapping):
        useful = {
            key: data[key]
            for key in (
                "url_after",
                "match",
                "interaction_verified",
                "exit_code",
                "stdout",
                "status",
            )
            if key in data
        }
        target = data.get("target")
        if isinstance(target, Mapping):
            useful["target"] = {
                key: target[key]
                for key in ("role", "name", "control_type")
                if target.get(key)
            }
        if useful:
            return json.dumps(useful, ensure_ascii=False)[:_MAX_RESULT_CHARS]
    result = payload.get("result")
    return str(result)[:_MAX_RESULT_CHARS]


def load_events(trace_path: Path, session_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with trace_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("session_id") != session_id:
                continue
            if event.get("type") == "transport.delta":
                continue
            events.append(event)
    return events


def segment_phases(events: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    current = "unassigned"
    phases: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        name = event.get("name")
        etype = event.get("type")
        payload = event.get("payload") or {}
        if name == "task_progress_check" and etype == "tool.call_completed":
            args = payload.get("args") or {}
            sub = args.get("current_subtask")
            updates = args.get("updates") or []
            if isinstance(updates, list) and updates:
                last = updates[-1] if isinstance(updates[-1], Mapping) else {}
                sub = last.get("subtask_id") or sub
            if isinstance(sub, str) and sub.strip():
                current = sub.strip()
            continue
        if etype not in {"tool.call_completed", "tool.call_failed"}:
            continue
        phases.setdefault(current, []).append(dict(event))
    return phases


def distill_phase_tools(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    distilled: list[dict[str, Any]] = []
    for event in events:
        name = str(event.get("name") or "tool")
        etype = event.get("type")
        payload = event.get("payload") or {}
        failed = etype == "tool.call_failed" or bool(payload.get("is_error"))
        if name in _ALWAYS_DROP:
            continue
        if failed and name in _FAILED_DROP:
            continue
        item = {
            "tool": name,
            "failed": failed,
            "args": _compact_args(payload.get("args")),
            "result": _compact_result(payload) if isinstance(payload, Mapping) else "",
        }
        distilled.append(item)
        if len(distilled) >= _MAX_TOOLS_PER_PHASE:
            break
    return distilled


def ticket_slots(ticket: Mapping[str, Any]) -> dict[str, Any]:
    data = ticket.get("data") if isinstance(ticket.get("data"), Mapping) else ticket
    if not isinstance(data, Mapping):
        return {}
    plan = data.get("change_plan")
    region = None
    if isinstance(plan, list) and plan and isinstance(plan[0], Mapping):
        region_obj = plan[0].get("region")
        if isinstance(region_obj, Mapping):
            region = region_obj.get("unique_id") or region_obj.get("name_cn")
    cloud = data.get("cloud_service") if isinstance(data.get("cloud_service"), Mapping) else {}
    involved: list[dict[str, Any]] = []
    sop = data.get("sop_change_step")
    if isinstance(sop, Mapping):
        for section, steps in sop.items():
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, Mapping) or not step.get("is_involved_step"):
                    continue
                involved.append(
                    {
                        "section": section,
                        "sop_step_id": step.get("sop_step_id"),
                        "check_name": step.get("check_name"),
                        "operate_description": _strip_html(
                            str(step.get("operate_description") or "")
                        )[:1200],
                        "operate_verified": _strip_html(
                            str(step.get("operate_verified") or "")
                        )[:1200],
                    }
                )
    return {
        "ticket_id": data.get("ticket_id"),
        "title": data.get("title"),
        "region": region,
        "service_name": cloud.get("name_cn") if isinstance(cloud, Mapping) else None,
        "service_id": cloud.get("unique_id") if isinstance(cloud, Mapping) else None,
        "involved_steps": involved,
    }


def _strip_html(value: str) -> str:
    return (
        value.replace("<p>", "")
        .replace("</p>", "\n")
        .replace("<code>", "")
        .replace("</code>", "")
        .replace("&amp;", "&")
    )


_PHASE_TO_SOP = {
    "step1": "OPS-SOP-STEP-001",
    "step2": "OPS-SOP-STEP-004",
    "step3": "OPS-SOP-STEP-005",
    "step4": "OPS-SOP-STEP-004",
}

_PHASE_ENTRY = {
    "step1": {
        "page_name": "告警取证台",
        "menu_path": ["运维控制台", "告警取证台"],
        "route_hint": "/ops/alarm-query",
    },
    "step2": {
        "page_name": "变更执行台",
        "menu_path": ["运维控制台", "变更执行台"],
        "route_hint": "/ops/change",
    },
    "step3": {
        "page_name": "资产台账核查台",
        "menu_path": ["运维控制台", "资产台账核查台"],
        "route_hint": "/ops/asset-check",
    },
    "step4": {
        "page_name": "变更执行台",
        "menu_path": ["运维控制台", "变更执行台"],
        "route_hint": "/ops/change",
    },
}


def _involved_for_phase(
    ticket_summary: Mapping[str, Any], phase: str
) -> dict[str, Any] | None:
    wanted = _PHASE_TO_SOP.get(phase)
    for step in ticket_summary.get("involved_steps") or []:
        if not isinstance(step, Mapping):
            continue
        if wanted and step.get("sop_step_id") == wanted:
            return dict(step)
    return None


def build_compiler_prompt(
    *,
    ticket_summary: Mapping[str, Any],
    phase: str,
    phase_tools: Sequence[Mapping[str, Any]],
    involved: Mapping[str, Any] | None,
    fixture_after: str,
) -> str:
    payload = {
        "compile_one_sop_step_only": True,
        "phase": phase,
        "ticket_slots": {
            key: ticket_summary.get(key)
            for key in ("ticket_id", "title", "region", "service_name", "service_id")
        },
        "current_sop_step": involved,
        "suggested_entry": _PHASE_ENTRY.get(phase),
        "suggested_sop_id": _PHASE_TO_SOP.get(phase),
        "phase_tools": list(phase_tools),
        "independent_terminal_readback": fixture_after if phase in {"step2", "step4"} else None,
        "output_schema": {
            "memory_type": "procedure",
            "name": "string",
            "sop_id": "string?",
            "entry": {
                "page_name": "string",
                "menu_path": ["string"],
                "route_hint": "/path?",
            },
            "inputs": [
                {
                    "name": "snake_case",
                    "description": "string",
                    "required": True,
                    "binds_from": "string?",
                }
            ],
            "abort_when": ["string"],
            "steps": [
                {
                    "order": 1,
                    "phase": "STEP-001?",
                    "action": "open_page|click|confirm|select|fill|set_datetime|wait|read|terminal",
                    "target": {
                        "page_name": "?",
                        "role": "?",
                        "name": "?",
                        "text": "?",
                        "label": "?",
                        "command_template": "?",
                    },
                    "use_input": "slot?",
                    "expect": {
                        "visible_text": "?",
                        "field": {"label": "?", "equals": "?"},
                        "row": {"host": "{host}?"},
                        "no_row": {"status": "firing?"},
                        "terminal": {
                            "return_code": 0,
                            "stdout_exact": "?",
                        },
                        "dialog_closed": True,
                    },
                    "abort_when": ["?"],
                    "note": "?",
                }
            ],
            "success": {"all_of": ["string"]},
        },
    }
    return (
        COMPILER_SYSTEM
        + "\n\nINPUT:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n只输出当前 SOP 步骤的一份 ProcedureRecord JSON。"
    )


def _coerce_expect(payload: dict[str, Any]) -> dict[str, Any]:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload
    input_names = {
        item.get("name")
        for item in payload.get("inputs") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for step in steps:
        if not isinstance(step, dict):
            continue
        expect = step.get("expect")
        if not isinstance(expect, dict):
            step["expect"] = {"visible_text": f"{step.get('action') or 'step'} completed"}
            continue
        useful = {key: value for key, value in expect.items() if value not in (None, "", {}, [])}
        field = useful.get("field")
        if isinstance(field, dict):
            if "equals" not in field and "contains" in field:
                field["equals"] = field.pop("contains")
            field.pop("visible", None)
            if "equals" not in field:
                field["equals"] = field.get("label") or "{value}"
            useful["field"] = {key: field[key] for key in ("label", "equals") if key in field}
        if not useful:
            useful = {"visible_text": f"{step.get('action') or 'step'} completed"}
        step["expect"] = useful
        use_input = step.get("use_input")
        if isinstance(use_input, str) and use_input not in input_names:
            step["use_input"] = None
    return payload


async def compile_success_path(
    *,
    client: LLMClient,
    trace_path: Path,
    session_id: str,
    ticket: Mapping[str, Any],
    fixture_after: str,
) -> tuple[list[ProcedureRecord], dict[str, Any]]:
    events = load_events(trace_path, session_id)
    raw_phases = segment_phases(events)
    distilled = {
        phase: distill_phase_tools(items)
        for phase, items in raw_phases.items()
        if items and phase in _PHASE_TO_SOP
    }
    summary = ticket_slots(ticket)
    records: list[ProcedureRecord] = []
    raw_by_phase: dict[str, Any] = {}
    errors: dict[str, str] = {}
    elapsed_ms = 0.0
    provider = model = ""
    for phase in ("step1", "step2", "step3"):
        tools = distilled.get(phase) or []
        if not tools:
            continue
        involved = _involved_for_phase(summary, phase)
        prompt = build_compiler_prompt(
            ticket_summary=summary,
            phase=phase,
            phase_tools=tools,
            involved=involved,
            fixture_after=fixture_after,
        )
        response = await client.complete_json(prompt, temperature=0.0)
        provider = response.provider_name
        model = response.model
        elapsed_ms += float(response.elapsed_ms or 0)
        payload = response.payload
        if not isinstance(payload, dict):
            errors[phase] = f"non-object JSON: {type(payload)}"
            continue
        debug_root = Path(os.environ.get("HOMEMASTER_DEBUG_ROOT", tempfile.gettempdir()))
        debug_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(debug_root, 0o700)
        dump_dir = Path(tempfile.mkdtemp(prefix="homemaster-explore-", dir=debug_root))
        os.chmod(dump_dir, 0o700)
        dump = dump_dir / f"{phase}.invalid.json"
        try:
            record = ProcedureRecord.model_validate(payload)
        except Exception as first_error:
            dump.write_text(
                json.dumps(
                    {"error": str(first_error), "payload": payload},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            repair = (
                COMPILER_SYSTEM
                + "\n\n你上次输出未通过 schema 校验：\n"
                + str(first_error)
                + "\n\n原输出：\n"
                + json.dumps(payload, ensure_ascii=False)
                + "\n\n请只输出当前 SOP 步骤修正后的 ProcedureRecord JSON。"
                " expect.field 只能有 label+equals；"
                " use_input 必须是 inputs[].name 的 snake_case；"
                " 每步 expect 必须至少一个非空条件；"
                " 不要把其他 SOP 步骤编进来。"
            )
            response = await client.complete_json(repair, temperature=0.0)
            elapsed_ms += float(response.elapsed_ms or 0)
            payload = response.payload
            if not isinstance(payload, dict):
                errors[phase] = f"repair non-object: {first_error}"
                continue
            try:
                record = ProcedureRecord.model_validate(payload)
            except Exception as second_error:
                coerced = _coerce_expect(payload)
                dump.write_text(
                    json.dumps(
                        {
                            "first_error": str(first_error),
                            "second_error": str(second_error),
                            "payload": payload,
                            "coerced": coerced,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                try:
                    record = ProcedureRecord.model_validate(coerced)
                    payload = coerced
                except Exception as third_error:
                    errors[phase] = str(third_error)
                    raw_by_phase[phase] = payload
                    continue
        records.append(record)
        raw_by_phase[phase] = payload
    debug = {
        "compiled_at": datetime.now().isoformat(),
        "session_id": session_id,
        "phase_tool_counts": {key: len(val) for key, val in distilled.items()},
        "provider": provider,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "errors": errors,
        "raw_payload": raw_by_phase,
    }
    if not records:
        raise ValueError(f"compiler produced no valid procedures: {errors}")
    return records, debug
