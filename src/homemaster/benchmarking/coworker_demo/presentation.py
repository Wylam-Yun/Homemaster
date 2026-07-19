"""Safe, field-specific projection of runtime events for live presentation."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from datetime import datetime, timezone
from math import log2
from typing import Any

from homemaster.benchmarking.coworker_demo.correlation import action_id_for
from homemaster.events.runtime_events import RuntimeEvent

_TOOL_EVENTS = {"tool.call_started", "tool.call_completed", "tool.call_failed"}
_RUNTIME_STATUSES = {
    "runtime.turn_completed": "succeeded",
    "runtime.turn_failed": "failed",
}
_TOOLS = {
    "task_planner",
    "task_progress_check",
    "skill_view",
    "browser_navigate",
    "browser_observe",
    "browser_click",
    "browser_fill",
    "browser_select",
    "browser_wait",
    "terminal_execute",
    "sop_decide",
}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_JOB_ID = re.compile(r"job-(?:add|remove|business_verify)-[0-9a-f]{10}\Z")
_EVIDENCE_ID = re.compile(
    r"(?:ev-[0-9]{5}-[0-9a-f]{8}"
    r"|terminal-cmd-[0-9a-f]{12}"
    r"|job-job-(?:add|remove|business_verify)-[0-9a-f]{10}-"
    r"(?:accepted|running|succeeded|failed))\Z"
)
_MAX_EVIDENCE_REFS = 16
_MAX_EVIDENCE_BYTES = 1024
_MAX_COMMAND_CHARS = 256
_TERMINAL_PATH = "/opt/app/service_layer/component/config/extension_item_mapping.json"
_TERMINAL_TARGET = re.compile(r"[A-Za-z0-9_.-]{1,64}:[A-Za-z0-9_.-]{1,64}\Z")

_SKILLS = {"change_execution", "evidence_discipline"}
_ROUTES = {"ticket", "monitor", "automation"}
_BIDS = {
    "ticket-query-extension-config",
    "ticket-query-upstream-ready",
    "monitor-cluster",
    "monitor-region",
    "monitor-query-alarm",
    "monitor-query-probe",
    "monitor-query-capacity",
    "monitor-query-runtime-metrics",
    "monitor-query-traffic",
    "automation-script",
    "automation-operation",
    "automation-tenant-id",
    "automation-item-code",
    "automation-spec-code",
    "automation-extension-name",
    "automation-resource-bucket",
    "automation-business-timestamp",
    "automation-factor",
    "automation-submit",
}
_CLOSED_VALUES = {
    "add",
    "remove",
    "business_verify",
    "svc_cfg_cli_runner",
    "svc_usage_record_fetcher",
}
_OPERATIONS = {"add", "remove", "business_verify"}
_TARGET_STATUSES = {"terminal"}
_VISIBLE_STATUSES = {
    "accepted",
    "active",
    "clear",
    "failed",
    "normal",
    "ready",
    "rejected",
    "running",
    "succeeded",
    "sufficient",
}
_RESULT_STAGES = {"pre_change", "post_change"}
_CHECKS = {"extension_config", "upstream_ready"}
_QUERIES = {"alarm", "probe", "capacity", "runtime_metrics", "traffic"}
_SOP_STAGES = {
    "check_before_change",
    "change_implement",
    "change_verified",
    "change_rollback",
}
_DECISIONS = {
    "proceed",
    "block",
    "rollback",
    "complete",
    "rolled_back",
    "escalate",
    "insufficient_evidence",
}
_TASK_STATUSES = {"none", "active", "paused", "completed", "failed", "cancelled"}
_PLAN_STATUSES = {
    "pending",
    "in_progress",
    "completed",
    "blocked",
    "cancelled",
    "uncertain",
}
_PLAN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TOOL_PRESENTATION = {
    "task_planner": ("创建执行计划", "orchestration"),
    "task_progress_check": ("更新计划进度", "orchestration"),
    "skill_view": ("读取操作规范", "orchestration"),
    "browser_navigate": ("打开业务页面", "observation"),
    "browser_observe": ("读取页面状态", "observation"),
    "browser_click": ("执行页面操作", "mutation"),
    "browser_fill": ("填写变更参数", "mutation"),
    "browser_select": ("选择变更选项", "mutation"),
    "browser_wait": ("等待自动化任务", "wait"),
    "terminal_execute": ("执行独立终端验证", "verification"),
    "sop_decide": ("提交流程决策", "gate"),
}
_SAFE_FAILURE_CODES = {
    "plan_required",
    "missing_precheck_evidence",
    "progress_required",
    "wait_required",
    "postchecks_required",
    "rollback_verification_required",
    "rollback_decision_required",
    "missing_anomaly_evidence",
    "missing_implementation_evidence",
    "missing_postcheck_evidence",
    "missing_rollback_evidence",
    "external_state_mismatch",
    "parameter_mismatch",
    "command_not_allowed",
    "invalid_decision_for_stage",
    "stale_state_version",
    "action_replay",
    "terminal_outcome",
    "unclassified_failure",
}
_FAILURE_CODE_ALIASES = {
    "action_replay": "action_replay",
    "action_tool_mismatch": "parameter_mismatch",
    "add_grep_failed": "external_state_mismatch",
    "command_not_allowed": "command_not_allowed",
    "external_state_mismatch": "external_state_mismatch",
    "implementation_decision_required": "missing_implementation_evidence",
    "invalid_decision_for_stage": "invalid_decision_for_stage",
    "invalid_job_transition": "invalid_decision_for_stage",
    "invalid_phase": "invalid_decision_for_stage",
    "invalid_run_id": "parameter_mismatch",
    "missing_anomaly_evidence": "missing_anomaly_evidence",
    "missing_implementation_evidence": "missing_implementation_evidence",
    "missing_postcheck_evidence": "missing_postcheck_evidence",
    "missing_precheck_evidence": "missing_precheck_evidence",
    "missing_rollback_evidence": "missing_rollback_evidence",
    "parameter_mismatch": "parameter_mismatch",
    "plan_required": "plan_required",
    "postchecks_required": "postchecks_required",
    "pre_gate_not_satisfied": "missing_precheck_evidence",
    "presentation_consistency_error": "unclassified_failure",
    "presentation_run_mismatch": "parameter_mismatch",
    "progress_required": "progress_required",
    "rollback_grep_failed": "external_state_mismatch",
    "rollback_not_authorized": "rollback_decision_required",
    "rollback_verification_required": "rollback_verification_required",
    "run_exists": "action_replay",
    "script_operation_mismatch": "parameter_mismatch",
    "stale_state_version": "stale_state_version",
    "target_mismatch": "parameter_mismatch",
    "terminal_outcome": "terminal_outcome",
    "unknown_evidence_ref": "parameter_mismatch",
    "unknown_job": "parameter_mismatch",
    "unknown_run": "parameter_mismatch",
    "unknown_scenario": "parameter_mismatch",
    "unreserved_action": "parameter_mismatch",
    "wait_required": "wait_required",
}
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|api[_-]?key\s*[:=]\s*\S+)"
)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PEM_PATTERN = re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----")
_SIGNED_URL_PATTERN = re.compile(
    r"(?i)https?://\S+[?&](?:x-amz-signature|signature|sig|token)=[^&\s]+"
)
_SK_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_TOKEN_CANDIDATE = re.compile(r"\b[A-Za-z0-9_+/=-]{24,}\b")


class ProjectionError(RuntimeError):
    """Raised when a runtime event cannot cross the presentation trust boundary."""


def _entropy(value: str) -> float:
    counts = {character: value.count(character) for character in set(value)}
    length = len(value)
    return -sum((count / length) * log2(count / length) for count in counts.values())


def reject_secret_text(value: Any, *, sensitive_values: Iterable[str] = ()) -> bool:
    """Return True for credential-like free text without logging the source value."""

    if not isinstance(value, str):
        return True
    if any(secret and secret in value for secret in sensitive_values):
        return True
    if any(
        pattern.search(value)
        for pattern in (
            _CREDENTIAL_PATTERN,
            _JWT_PATTERN,
            _PEM_PATTERN,
            _SIGNED_URL_PATTERN,
            _SK_TOKEN_PATTERN,
        )
    ):
        return True
    for match in _TOKEN_CANDIDATE.finditer(value):
        token = match.group(0)
        classes = sum(
            any(predicate(character) for character in token)
            for predicate in (str.islower, str.isupper, str.isdigit)
        )
        if classes >= 3 and _entropy(token) >= 4.0:
            return True
    return False


def _safe_display_text(
    value: Any,
    *,
    limit: int,
    sensitive_values: Iterable[str],
) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        return None
    if reject_secret_text(value, sensitive_values=sensitive_values):
        return None
    return value.strip()


def _safe_plan_snapshot(
    value: Any,
    *,
    sensitive_values: Iterable[str],
) -> dict[str, Any] | None:
    source = _dict(value)
    subtasks = source.get("subtasks")
    if not isinstance(subtasks, list) or len(subtasks) > 12:
        return None
    items: list[dict[str, str]] = []
    for raw_item in subtasks:
        item = _dict(raw_item)
        item_id = item.get("id")
        title = _safe_display_text(
            item.get("description"), limit=160, sensitive_values=sensitive_values
        )
        status = item.get("status", "pending")
        if (
            not isinstance(item_id, str)
            or _PLAN_ID.fullmatch(item_id) is None
            or title is None
            or status not in _PLAN_STATUSES
        ):
            return None
        items.append({"id": item_id, "title": title, "status": status})
    current = source.get("current_subtask")
    if current is not None and (
        not isinstance(current, str) or _PLAN_ID.fullmatch(current) is None
    ):
        return None
    next_focus = source.get("next_focus")
    safe_focus = None
    if next_focus is not None:
        safe_focus = _safe_display_text(next_focus, limit=240, sensitive_values=sensitive_values)
        if safe_focus is None:
            return None
    return {"items": items, "current_id": current, "next_focus": safe_focus}


def _normalize_failure_code(data: dict[str, Any]) -> str:
    for key in ("error_code", "failure_code", "code"):
        raw_code = data.get(key)
        if isinstance(raw_code, str) and raw_code in _FAILURE_CODE_ALIASES:
            return _FAILURE_CODE_ALIASES[raw_code]
    failure = data.get("failure_reason") or data.get("error")
    if isinstance(failure, str):
        if "remove requires a rollback decision" in failure:
            return "rollback_decision_required"
        for safe_code in _SAFE_FAILURE_CODES:
            if re.search(rf"(?:^|:\s){re.escape(safe_code)}(?:\s*:|$)", failure):
                return safe_code
        for raw_code, safe_code in _FAILURE_CODE_ALIASES.items():
            if re.search(rf"(?:^|:\s){re.escape(raw_code)}(?:\s*:|$)", failure):
                return safe_code
    return "unclassified_failure"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _closed(source: dict[str, Any], key: str, allowed: set[str]) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) and value in allowed else None


def _required_closed(source: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _closed(source, key, allowed)
    if value is None:
        raise ProjectionError(f"invalid presentation {key}")
    return value


def _bounded_count(value: Any, limit: int = 100) -> int:
    return min(len(value), limit) if isinstance(value, list | tuple) else 0


def _safe_timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 48:
        raise ProjectionError("invalid presentation timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProjectionError("invalid presentation timestamp") from exc
    if parsed.tzinfo is None:
        raise ProjectionError("invalid presentation timestamp")
    normalized = parsed.astimezone(timezone.utc).replace(microsecond=0)  # noqa: UP017
    return normalized.isoformat().replace("+00:00", "Z")


def _command_kind(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_COMMAND_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        return "other"
    try:
        tokens = tuple(shlex.split(value, posix=True))
    except ValueError:
        return "other"
    if (
        len(tokens) == 5
        and tokens[:3] == ("grep", "-A", "3")
        and _TERMINAL_TARGET.fullmatch(tokens[3]) is not None
        and tokens[4] == _TERMINAL_PATH
    ):
        return "sop_grep"
    return "other"


def _safe_arguments(tool_name: str, value: Any) -> dict[str, Any]:
    arguments = _dict(value)
    if tool_name == "task_planner":
        return {
            "subtask_count": _bounded_count(arguments.get("subtasks")),
            "has_current_subtask": bool(arguments.get("current_subtask")),
            "has_next_focus": bool(arguments.get("next_focus")),
        }
    if tool_name == "task_progress_check":
        return {
            "update_count": _bounded_count(arguments.get("updates")),
            "has_current_subtask": bool(arguments.get("current_subtask")),
            "has_next_focus": bool(arguments.get("next_focus")),
        }
    if tool_name == "skill_view":
        return {"skill_name": _required_closed(arguments, "skill_name", _SKILLS)}
    if tool_name == "browser_navigate":
        return {"route": _required_closed(arguments, "route", _ROUTES)}
    if tool_name == "browser_observe":
        return {}
    if tool_name == "browser_click":
        return {"bid": _required_closed(arguments, "bid", _BIDS)}
    if tool_name in {"browser_fill", "browser_select"}:
        result: dict[str, Any] = {"bid": _required_closed(arguments, "bid", _BIDS)}
        value_text = arguments.get("value")
        if isinstance(value_text, str) and value_text in _CLOSED_VALUES:
            result["value"] = value_text
        else:
            result["value_class"] = "free_text"
            result["value_present"] = isinstance(value_text, str) and bool(value_text)
        return result
    if tool_name == "browser_wait":
        job_id = arguments.get("job_id")
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise ProjectionError("invalid presentation job_id")
        return {
            "job_id": job_id,
            "target_status": _required_closed(arguments, "target_status", _TARGET_STATUSES),
        }
    if tool_name == "terminal_execute":
        return {"command_kind": _command_kind(arguments.get("command"))}
    if tool_name == "sop_decide":
        return {
            "stage": _required_closed(arguments, "stage", _SOP_STAGES),
            "decision": _required_closed(arguments, "decision", _DECISIONS),
        }
    raise ProjectionError("unknown presentation tool")


def _visible_layers(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    visible = _dict(data.get("visible_observation"))
    receipt = _dict(visible.get("receipt"))
    payload = _dict(receipt.get("payload"))
    return visible, receipt, payload


def _valid_job_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _JOB_ID.fullmatch(value) else None


def _browser_result(source: dict[str, Any], *, include_receipt: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, allowed in (
        ("check", _CHECKS),
        ("query", _QUERIES),
        ("stage", _RESULT_STAGES),
        ("status", _VISIBLE_STATUSES),
        ("operation", _OPERATIONS),
    ):
        safe = _closed(source, key, allowed)
        if safe is not None:
            result[key] = safe
    ready = source.get("ready")
    if isinstance(ready, bool):
        result["ready"] = ready
    job_id = _valid_job_id(source.get("job_id"))
    if job_id is not None:
        result["job_id"] = job_id
    if not include_receipt:
        bid = _closed(source, "bid", _BIDS)
        if bid is not None:
            result["bid"] = bid
        value = _closed(source, "value", _CLOSED_VALUES)
        if value is not None:
            result["value"] = value
        if isinstance(source.get("value"), str) and isinstance(source.get("readback"), str):
            result["readback_matches"] = source["value"] == source["readback"]
    return result


def summarize_tool_result(tool_name: str, data: Any) -> dict[str, Any]:
    """Build a fresh result summary without forwarding runtime free text."""
    if tool_name not in _TOOLS:
        raise ProjectionError("unknown presentation tool")
    safe_data = _dict(data)
    visible, _receipt, payload = _visible_layers(safe_data)
    if tool_name == "browser_click":
        return _browser_result(payload or visible, include_receipt=True)
    if tool_name == "browser_wait":
        return _browser_result(visible or safe_data, include_receipt=True)
    if tool_name in {"browser_fill", "browser_select"}:
        return _browser_result(visible or safe_data, include_receipt=False)
    if tool_name == "terminal_execute":
        result: dict[str, Any] = {}
        exit_code = safe_data.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            result["exit_code"] = exit_code
        stdout = safe_data.get("stdout")
        stderr = safe_data.get("stderr")
        result["stdout_present"] = isinstance(stdout, str) and bool(stdout)
        result["stderr_present"] = isinstance(stderr, str) and bool(stderr)
        return result
    if tool_name == "sop_decide":
        result = {}
        backend = _closed(safe_data, "backend_status", {"accepted", "succeeded"})
        classification = _closed(safe_data, "classification", _DECISIONS)
        if backend is not None:
            result["backend_status"] = backend
        if isinstance(safe_data.get("terminal"), bool):
            result["terminal"] = safe_data["terminal"]
        if classification is not None:
            result["classification"] = classification
        return result
    if tool_name in {"task_planner", "task_progress_check"}:
        result = {
            "subtask_count": _bounded_count(safe_data.get("subtasks")),
            "has_current_subtask": bool(safe_data.get("current_subtask")),
            "has_next_focus": bool(safe_data.get("next_focus")),
        }
        status = _closed(safe_data, "status", _TASK_STATUSES)
        if status is not None:
            result["status"] = status
        if isinstance(safe_data.get("success"), bool):
            result["success"] = safe_data["success"]
        return result
    if tool_name == "skill_view":
        result = {}
        skill_name = _closed(safe_data, "name", _SKILLS)
        if skill_name is not None:
            result["name"] = skill_name
        if isinstance(safe_data.get("success"), bool):
            result["success"] = safe_data["success"]
        return result
    if isinstance(safe_data.get("success"), bool):
        return {"success": safe_data["success"]}
    return {}


def _evidence_refs(data: dict[str, Any], receipt: dict[str, Any]) -> list[str]:
    result: list[str] = []
    total_bytes = 0
    for source in (data.get("evidence_refs"), receipt.get("evidence_refs")):
        items = source if isinstance(source, list | tuple) else []
        for item in items:
            if not isinstance(item, str) or _EVIDENCE_ID.fullmatch(item) is None:
                continue
            size = len(item.encode("utf-8"))
            if item in result:
                continue
            if len(result) >= _MAX_EVIDENCE_REFS or total_bytes + size > _MAX_EVIDENCE_BYTES:
                return result
            result.append(item)
            total_bytes += size
    return result


def _validate_identity(event: RuntimeEvent) -> None:
    for value in (event.run_id, event.tool_call_id):
        if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
            raise ProjectionError("invalid presentation event identity")
    if event.name not in _TOOLS:
        raise ProjectionError("unknown presentation tool")


def _visible_statuses(data: dict[str, Any]) -> list[str]:
    visible, _receipt, payload = _visible_layers(data)
    statuses: list[str] = []
    for source in (payload, visible):
        if "status" not in source:
            continue
        status = source.get("status")
        if not isinstance(status, str) or status not in _VISIBLE_STATUSES:
            raise ProjectionError("inconsistent tool lifecycle")
        statuses.append(status)
    return statuses


def _tool_status(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "tool.call_started":
        return "running"
    backend = data.get("backend_status")
    success = data.get("success")
    visible_statuses = _visible_statuses(data)
    if "success" in data and not isinstance(success, bool):
        raise ProjectionError("inconsistent tool lifecycle")
    if event_type == "tool.call_completed":
        if (
            backend not in {None, "accepted", "succeeded"}
            or success is False
            or any(status in {"failed", "rejected"} for status in visible_statuses)
        ):
            raise ProjectionError("inconsistent tool lifecycle")
        return "accepted" if backend == "accepted" else "succeeded"
    if (
        backend not in {None, "failed", "rejected"}
        or success is True
        or any(status in {"accepted", "succeeded"} for status in visible_statuses)
    ):
        raise ProjectionError("inconsistent tool lifecycle")
    return "rejected" if backend == "rejected" else "failed"


def project_runtime_event(
    event: RuntimeEvent,
    *,
    sensitive_values: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Project a runtime event into the presentation API's safe lifecycle schema."""
    if event.type == "assistant.reply":
        timestamp = _safe_timestamp(event.timestamp)
        if not isinstance(event.run_id, str) or _SAFE_ID.fullmatch(event.run_id) is None:
            raise ProjectionError("invalid presentation event identity")
        reply = _safe_display_text(
            _dict(event.payload).get("reply"),
            limit=1_200,
            sensitive_values=sensitive_values,
        )
        if reply is None:
            return None
        return {
            "schema_version": 2,
            "runtime_event_type": "model.public_reply",
            "status": "succeeded",
            "public_model_output": {
                "kind": "assistant_reply",
                "text": reply,
                "outcome": "intermediate",
            },
            "timestamp": timestamp,
        }
    if event.type not in _RUNTIME_STATUSES and event.type not in _TOOL_EVENTS:
        return None
    timestamp = _safe_timestamp(event.timestamp)
    if event.type in _RUNTIME_STATUSES:
        if not isinstance(event.run_id, str) or _SAFE_ID.fullmatch(event.run_id) is None:
            raise ProjectionError("invalid presentation event identity")
        return {
            "schema_version": 2,
            "runtime_event_type": event.type,
            "status": _RUNTIME_STATUSES[event.type],
            "timestamp": timestamp,
        }
    _validate_identity(event)
    assert event.tool_call_id is not None
    assert event.name is not None

    payload = _dict(event.payload)
    data = _dict(payload.get("data"))
    expected_action_id = action_id_for(event.run_id, event.tool_call_id)
    if event.type != "tool.call_started" and "action_id" in data:
        if data.get("action_id") != expected_action_id:
            raise ProjectionError("action identity mismatch")

    projected: dict[str, Any] = {
        "schema_version": 2,
        "runtime_event_type": event.type,
        "tool_call_id": event.tool_call_id,
        "action_id": expected_action_id,
        "tool_name": event.name,
        "tool_label_zh": _TOOL_PRESENTATION[event.name][0],
        "tool_kind": _TOOL_PRESENTATION[event.name][1],
        "status": _tool_status(event.type, data),
    }
    if event.type == "tool.call_started":
        projected["arguments"] = _safe_arguments(event.name, payload.get("arguments"))
    else:
        projected["result"] = summarize_tool_result(event.name, data)
        _visible, receipt, _receipt_payload = _visible_layers(data)
        projected["evidence_refs"] = _evidence_refs(data, receipt)
        if event.type == "tool.call_failed":
            projected["failure_code"] = _normalize_failure_code(data)
        if event.type == "tool.call_completed" and event.name in {
            "task_planner",
            "task_progress_check",
        }:
            plan = _safe_plan_snapshot(data, sensitive_values=sensitive_values)
            if plan is not None:
                projected["plan"] = plan
    projected["timestamp"] = timestamp
    return projected
