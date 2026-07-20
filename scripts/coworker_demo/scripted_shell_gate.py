"""Drive the actual HomeMaster shell with a local Anthropic-compatible scripted provider."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

try:
    from scripts.coworker_demo.verify_run_bundle import verify
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from verify_run_bundle import verify


class ScriptedConversation:
    def __init__(self, scenario_id: str, profile: str = "clean") -> None:
        self.scenario_id = scenario_id
        self.profile = profile
        self.index = 0
        self.steps = self._steps(scenario_id, profile)

    def next_response(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]] | str:
        if self.profile == "observable_failures":
            time.sleep(0.55)
        if self.index >= len(self.steps):
            return "Scripted run finished."
        name, arguments = self.steps[self.index]
        self.index += 1
        resolved = self._resolve(arguments, request)
        return name, resolved

    def _resolve(self, arguments: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        objects = list(_walk_decoded(request))
        text = "\n".join(value for value in objects if isinstance(value, str))
        controls: dict[str, str] = {}
        for item in objects:
            if not isinstance(item, dict) or not isinstance(item.get("bid"), str):
                continue
            visible = item.get("readback") or item.get("value") or item.get("text")
            if isinstance(visible, str) and visible:
                controls[item["bid"]] = visible
        evidence = sorted(set(re.findall(r"(?:ev-\d{5}-[a-f0-9]{8}|terminal-cmd-[a-f0-9]+)", text)))

        def replace(value: Any) -> Any:
            if isinstance(value, str) and value.startswith("$"):
                key = value[1:]
                if key == "evidence":
                    return evidence
                if key.startswith("job_"):
                    operation = key.removeprefix("job_")
                    matches = re.findall(rf"job-{re.escape(operation)}-[a-f0-9]+", text)
                    if not matches:
                        raise RuntimeError(f"scripted provider did not observe {operation} job id")
                    return matches[-1]
                if key == "command":
                    tenant = controls.get("automation-tenant-id") or _visible_value(
                        text, "TenantId"
                    )
                    item = controls.get("automation-item-code") or _visible_value(text, "ItemCode")
                    return (
                        f'grep -A 3 "{tenant}:{item}" '
                        "/opt/app/service_layer/component/config/extension_item_mapping.json"
                    )
                bid = {
                    "tenant": "automation-tenant-id",
                    "item": "automation-item-code",
                    "spec": "automation-spec-code",
                    "extension": "automation-extension-name",
                    "bucket": "automation-resource-bucket",
                    "timestamp": "automation-business-timestamp",
                    "factor": "automation-factor",
                    "region": "monitor-region",
                    "cluster": "monitor-cluster",
                }.get(key)
                if bid and controls.get(bid):
                    return controls[bid]
                label = {
                    "tenant": "TenantId",
                    "item": "ItemCode",
                    "spec": "SpecCode",
                    "extension": "ExtensionName",
                }.get(key)
                if label:
                    return _visible_value(text, label)
                raise RuntimeError(f"unknown scripted placeholder: {value}")
            if isinstance(value, list):
                return [replace(item) for item in value]
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            return value

        return replace(arguments)

    @classmethod
    def _steps(cls, scenario_id: str, profile: str) -> list[tuple[str, dict[str, Any]]]:
        clean = cls._clean_steps(scenario_id)
        if profile == "clean":
            return clean
        if profile != "observable_failures":
            raise ValueError(f"unknown scripted profile: {profile}")

        def first_index(name: str, predicate) -> int:
            for index, step in enumerate(clean):
                if step[0] == name and predicate(step[1]):
                    return index
            raise RuntimeError(f"clean scripted sequence missing {name}")

        config_end = first_index(
            "browser_navigate", lambda arguments: arguments.get("route") == "monitor"
        )
        precheck_progress = (
            "task_progress_check",
            {
                "updates": [{"subtask_id": "precheck", "status": "completed"}],
                "current_subtask": "implement",
                "next_focus": "implement",
            },
        )
        rejected_precheck = (
            "sop_decide",
            {
                "stage": "check_before_change",
                "decision": "proceed",
                "evidence_refs": "$evidence",
                "reason": "Attempted before all checks were observed",
            },
        )
        if scenario_id == "normal":
            steps = [*clean[:config_end]]
            steps.extend([precheck_progress, rejected_precheck])
            steps.extend(clean[config_end:])
            successful_precheck = next(
                index
                for index, step in enumerate(steps)
                if step[0] == "sop_decide"
                and step[1].get("reason") == "All visible prechecks passed"
            )
            progress_index = next(
                index
                for index in range(successful_precheck + 1, len(steps))
                if steps[index][0] == "task_progress_check"
            )
            steps.insert(
                progress_index,
                ("browser_navigate", {"route": "automation"}),
            )
            return steps

        steps = [*clean[:config_end]]
        steps.append(rejected_precheck)
        steps.extend(clean[config_end:])
        successful_precheck = next(
            index
            for index, step in enumerate(steps)
            if step[0] == "sop_decide"
            and step[1].get("stage") == "check_before_change"
            and step[1].get("reason") == "All visible prechecks passed"
        )
        add_wait = next(
            index
            for index, step in enumerate(steps)
            if step[0] == "browser_wait" and step[1].get("job_id") == "$job_add"
        )
        steps.insert(
            add_wait,
            ("terminal_execute", {"command": "$command"}),
        )
        alarm_index = next(
            index
            for index, step in enumerate(steps)
            if step[0] == "browser_click"
            and step[1].get("bid") == "monitor-query-alarm"
            and index > successful_precheck
        )
        steps[alarm_index + 1 : alarm_index + 1] = [
            ("browser_click", {"bid": "monitor-query-probe"}),
            ("browser_click", {"bid": "monitor-query-capacity"}),
            ("browser_click", {"bid": "monitor-query-runtime-metrics"}),
            ("browser_click", {"bid": "monitor-query-traffic"}),
        ]
        rollback_index = next(
            index
            for index, step in enumerate(steps)
            if step[0] == "sop_decide" and step[1].get("decision") == "rollback"
        )
        rejected_remove = [
            ("browser_navigate", {"route": "automation"}),
            ("browser_select", {"bid": "automation-script", "value": "svc_cfg_cli_runner"}),
            ("browser_select", {"bid": "automation-operation", "value": "remove"}),
            ("browser_fill", {"bid": "automation-tenant-id", "value": "$tenant"}),
            ("browser_fill", {"bid": "automation-item-code", "value": "$item"}),
            ("browser_click", {"bid": "automation-submit"}),
            (
                "sop_decide",
                {
                    "stage": "change_rollback",
                    "decision": "rolled_back",
                    "evidence_refs": "$evidence",
                    "reason": "Attempted rollback before authorization",
                },
            ),
        ]
        steps[rollback_index:rollback_index] = rejected_remove
        return steps

    @staticmethod
    def _clean_steps(scenario_id: str) -> list[tuple[str, dict[str, Any]]]:
        plan = [
            {"id": "precheck", "description": "Complete every required pre-change check"},
            {"id": "implement", "description": "Submit the change and verify its terminal state"},
            {"id": "postcheck", "description": "Complete post-change health checks"},
            {"id": "business", "description": "Verify the business record"},
            {"id": "rollback", "description": "Roll back if the procedure requires it"},
            {"id": "conclusion", "description": "Persist the evidence-backed terminal decision"},
        ]
        steps: list[tuple[str, dict[str, Any]]] = [
            ("browser_navigate", {"route": "ticket"}),
            ("browser_observe", {}),
            (
                "task_planner",
                {
                    "goal": "Execute and independently verify the whole visible change ticket",
                    "subtasks": plan,
                    "current_subtask": "precheck",
                    "next_focus": "precheck",
                },
            ),
            ("skill_view", {"skill_name": "change_execution"}),
            ("skill_view", {"skill_name": "evidence_discipline"}),
            ("browser_click", {"bid": "ticket-query-extension-config"}),
            ("browser_click", {"bid": "ticket-query-upstream-ready"}),
            ("browser_navigate", {"route": "monitor"}),
            ("browser_select", {"bid": "monitor-region", "value": "$region"}),
            ("browser_select", {"bid": "monitor-cluster", "value": "$cluster"}),
            ("browser_click", {"bid": "monitor-query-alarm"}),
            ("browser_click", {"bid": "monitor-query-probe"}),
            ("browser_click", {"bid": "monitor-query-capacity"}),
            ("browser_click", {"bid": "monitor-query-runtime-metrics"}),
            ("browser_click", {"bid": "monitor-query-traffic"}),
            (
                "sop_decide",
                {
                    "stage": "check_before_change",
                    "decision": "proceed",
                    "evidence_refs": "$evidence",
                    "reason": "All visible prechecks passed",
                },
            ),
            (
                "task_progress_check",
                {
                    "updates": [
                        {"subtask_id": "precheck", "status": "completed"},
                        {"subtask_id": "implement", "status": "in_progress"},
                    ],
                    "current_subtask": "implement",
                    "next_focus": "implement",
                },
            ),
            ("browser_navigate", {"route": "automation"}),
            ("browser_select", {"bid": "automation-script", "value": "svc_cfg_cli_runner"}),
            ("browser_select", {"bid": "automation-operation", "value": "add"}),
            ("browser_fill", {"bid": "automation-tenant-id", "value": "$tenant"}),
            ("browser_fill", {"bid": "automation-item-code", "value": "$item"}),
            ("browser_fill", {"bid": "automation-spec-code", "value": "$spec"}),
            ("browser_fill", {"bid": "automation-extension-name", "value": "$extension"}),
            ("browser_click", {"bid": "automation-submit"}),
            ("browser_wait", {"job_id": "$job_add", "target_status": "terminal"}),
            ("terminal_execute", {"command": "$command"}),
            (
                "sop_decide",
                {
                    "stage": "change_implement",
                    "decision": "proceed",
                    "evidence_refs": "$evidence",
                    "reason": "The add job and independent grep succeeded",
                },
            ),
            (
                "task_progress_check",
                {
                    "updates": [
                        {"subtask_id": "implement", "status": "completed"},
                        {"subtask_id": "postcheck", "status": "in_progress"},
                    ],
                    "current_subtask": "postcheck",
                    "next_focus": "postcheck",
                },
            ),
            ("browser_navigate", {"route": "monitor"}),
            ("browser_click", {"bid": "monitor-query-alarm"}),
        ]
        if scenario_id == "post_change_anomaly":
            steps.extend(
                [
                    (
                        "sop_decide",
                        {
                            "stage": "change_verified",
                            "decision": "rollback",
                            "evidence_refs": "$evidence",
                            "reason": "The post-change alarm is active and causal",
                        },
                    ),
                    ("browser_navigate", {"route": "automation"}),
                    ("browser_select", {"bid": "automation-script", "value": "svc_cfg_cli_runner"}),
                    ("browser_select", {"bid": "automation-operation", "value": "remove"}),
                    ("browser_fill", {"bid": "automation-tenant-id", "value": "$tenant"}),
                    ("browser_fill", {"bid": "automation-item-code", "value": "$item"}),
                    ("browser_click", {"bid": "automation-submit"}),
                    ("browser_wait", {"job_id": "$job_remove", "target_status": "terminal"}),
                    ("terminal_execute", {"command": "$command"}),
                    (
                        "task_progress_check",
                        {
                            "updates": [
                                {"subtask_id": "postcheck", "status": "completed"},
                                {"subtask_id": "rollback", "status": "completed"},
                            ],
                            "current_subtask": "conclusion",
                            "next_focus": "conclusion",
                        },
                    ),
                    (
                        "sop_decide",
                        {
                            "stage": "change_rollback",
                            "decision": "rolled_back",
                            "evidence_refs": "$evidence",
                            "reason": "Remove succeeded and the independent grep proves absence",
                        },
                    ),
                ]
            )
            return steps
        steps.extend(
            [
                ("browser_click", {"bid": "monitor-query-probe"}),
                ("browser_click", {"bid": "monitor-query-capacity"}),
                ("browser_click", {"bid": "monitor-query-runtime-metrics"}),
                ("browser_click", {"bid": "monitor-query-traffic"}),
                ("browser_navigate", {"route": "automation"}),
                (
                    "browser_select",
                    {"bid": "automation-script", "value": "svc_usage_record_fetcher"},
                ),
                ("browser_select", {"bid": "automation-operation", "value": "business_verify"}),
                ("browser_fill", {"bid": "automation-resource-bucket", "value": "$bucket"}),
                ("browser_fill", {"bid": "automation-business-timestamp", "value": "$timestamp"}),
                ("browser_fill", {"bid": "automation-factor", "value": "$factor"}),
                ("browser_click", {"bid": "automation-submit"}),
                ("browser_wait", {"job_id": "$job_business_verify", "target_status": "terminal"}),
                (
                    "task_progress_check",
                    {
                        "updates": [
                            {"subtask_id": "postcheck", "status": "completed"},
                            {"subtask_id": "business", "status": "completed"},
                        ],
                        "current_subtask": "conclusion",
                        "next_focus": "conclusion",
                    },
                ),
                (
                    "sop_decide",
                    {
                        "stage": "change_verified",
                        "decision": "complete",
                        "evidence_refs": "$evidence",
                        "reason": "All postchecks and business verification passed",
                    },
                ),
            ]
        )
        return steps


def _walk_decoded(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_decoded(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_decoded(item)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                decoded = json.loads(stripped)
            except ValueError:
                return
            yield from _walk_decoded(decoded)


def _visible_value(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}\s+([^\s]+)", text)
    if not match:
        raise RuntimeError(f"scripted provider did not observe visible {label}")
    return match.group(1)


def _sse_tool(call_id: str, name: str, arguments: dict[str, Any]) -> bytes:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg-{call_id}",
                    "type": "message",
                    "role": "assistant",
                    "model": "scripted-coworker",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
    ).encode()


def _sse_text(text: str) -> bytes:
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg-final",
                    "type": "message",
                    "role": "assistant",
                    "model": "scripted-coworker",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
    ).encode()


def _handler(conversation: ScriptedConversation):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length))
            response = conversation.next_response(request)
            if isinstance(response, str):
                body = _sse_text(response)
            else:
                name, arguments = response
                body = _sse_tool(f"call-{conversation.index:03d}", name, arguments)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _verify_observable_failure_profile(run_root: Path, scenario_id: str) -> list[str]:
    snapshot = json.loads((run_root / "presentation/snapshot.json").read_text(encoding="utf-8"))
    video_manifest = json.loads(
        (run_root / "video/video_manifest.json").read_text(encoding="utf-8")
    )
    expected_codes = {
        "normal": ["missing_precheck_evidence", "progress_required"],
        "post_change_anomaly": [
            "missing_precheck_evidence",
            "wait_required",
            "rollback_decision_required",
            "progress_required",
        ],
    }[scenario_id]
    incidents = snapshot.get("incidents") or []
    failures: list[str] = []
    observed_codes = [incident.get("failure_code") for incident in incidents]
    if observed_codes != expected_codes:
        failures.append(f"profile_incident_codes:{observed_codes}")
    for incident in incidents:
        incident_id = incident.get("incident_id")
        if incident.get("status") != "resolved" or not incident.get("recovery"):
            failures.append(f"profile_incident_not_resolved:{incident_id}")
    if any(incident.get("status") == "open" for incident in incidents):
        failures.append("profile_open_incident")
    event_frames = video_manifest.get("event_frames") or {}
    for incident in incidents:
        open_name = f"incident_open_{incident.get('opened_sequence')}"
        if open_name not in event_frames:
            failures.append(f"profile_missing_frame:{open_name}")
        recovery = incident.get("recovery") or {}
        resolved_name = f"incident_resolved_{recovery.get('resolved_sequence')}"
        if resolved_name not in event_frames:
            failures.append(f"profile_missing_frame:{resolved_name}")
    return failures


def run_gate(
    scenario_id: str,
    ticket: Path,
    output_root: Path,
    *,
    profile: str = "clean",
) -> dict[str, Any]:
    conversation = ScriptedConversation(scenario_id, profile)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(conversation))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    output_root.mkdir(parents=True, exist_ok=True)
    real_config = yaml.safe_load(Path("config/homemaster.yaml").read_text(encoding="utf-8"))
    default = real_config.get("runtime_defaults", {}).get("default_provider_name", "Mimo")
    for provider in real_config["providers"]["items"]:
        if provider["name"].casefold() == default.casefold():
            provider.update(
                {
                    "api_format": "anthropic",
                    "transport": "anthropic_sdk",
                    "base_url": f"http://127.0.0.1:{port}",
                    "model": "scripted-coworker",
                    "api_keys": ["scripted-local-key"],
                }
            )
    provider_config = output_root / f"provider-{scenario_id}-{profile}.yaml"
    provider_config.write_text(yaml.safe_dump(real_config, sort_keys=False), encoding="utf-8")
    message = f"{ticket}\n" if scenario_id == "normal" else f"post_change_anomaly {ticket}\n"
    process = subprocess.run(
        [".venv/bin/homemaster", "shell"],
        input=message + "/exit\n",
        text=True,
        capture_output=True,
        env={**os.environ, "HOMEMASTER_COWORKER_PROVIDER_CONFIG": str(provider_config.resolve())},
        timeout=900,
        check=False,
    )
    server.shutdown()
    server.server_close()
    prefix = f"{scenario_id}-{profile}"
    (output_root / f"shell-{prefix}.stdout.log").write_text(process.stdout, encoding="utf-8")
    (output_root / f"shell-{prefix}.stderr.log").write_text(process.stderr, encoding="utf-8")
    matches = re.findall(r"运行产物：(.+)", process.stdout)
    if not matches:
        raise RuntimeError(f"shell did not publish a run path; return={process.returncode}")
    run_root = Path(matches[-1].strip()).resolve()
    verification = verify(run_root, Path("data/coworker_demo/case_02").resolve())
    profile_failures = (
        _verify_observable_failure_profile(run_root, scenario_id)
        if profile == "observable_failures"
        else []
    )
    result = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "profile": profile,
        "shell_return_code": process.returncode,
        "provider_calls": conversation.index,
        "run_root": str(run_root),
        "verification": verification,
        "profile_failures": profile_failures,
        "pass": process.returncode == 0 and verification["pass"] and not profile_failures,
    }
    (output_root / f"result-{prefix}.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("normal", "post_change_anomaly"), required=True)
    parser.add_argument("--profile", choices=("clean", "observable_failures"), default="clean")
    parser.add_argument("--ticket", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("var/coworker-demo/scripted"))
    args = parser.parse_args()
    result = run_gate(
        args.scenario,
        args.ticket.resolve(),
        args.output_root.resolve(),
        profile=args.profile,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
