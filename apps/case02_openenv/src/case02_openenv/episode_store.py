"""Run isolation, action ledger, domain transitions and evidence ownership."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from case02_openenv.artifacts import ArtifactRegistry, append_jsonl, atomic_write_json
from case02_openenv.models import (
    ActionRecord,
    ActionReservation,
    AuditEvent,
    AutomationJob,
    DecisionRequest,
    EpisodePhase,
    JobStatus,
    RunState,
)
from case02_openenv.observable_presentation import reduce_observable_state
from case02_openenv.presentation import (
    PresentationEvent,
    PresentationInput,
    PresentationMappingError,
    PresentationSnapshot,
    PresentationTask,
    display_stage,
    map_task,
    verify_presentation_payload,
)


class EpisodeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class Episode:
    state: RunState
    scenario_id: str
    ticket: dict[str, Any]
    run_root: Path
    episode_root: Path
    config_file: Path
    registry: ArtifactRegistry
    audit: list[AuditEvent] = field(default_factory=list)
    presentation_events: list[PresentationEvent] = field(default_factory=list)
    current_presentation_task: PresentationTask | None = None
    presentation_failures: list[str] = field(default_factory=list)
    presentation_generation: int = 0
    recording_monotonic_start_s: float | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class EpisodeStore:
    def __init__(self, *, data_root: Path, artifact_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self._episodes: dict[str, Episode] = {}
        self._lock = threading.RLock()

    def create(self, run_id: str, scenario_id: str) -> RunState:
        if not run_id or "/" in run_id or ".." in run_id:
            raise EpisodeError(
                "invalid_run_id", "run_id contains unsafe characters", status_code=422
            )
        with self._lock:
            if run_id in self._episodes:
                raise EpisodeError("run_exists", f"run already exists: {run_id}")
            scenario_path = self.data_root / "scenarios" / f"{scenario_id}.yaml"
            if not scenario_path.is_file():
                raise EpisodeError(
                    "unknown_scenario", f"unknown scenario: {scenario_id}", status_code=422
                )
            scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
            ticket_path = self.data_root / "test_set/item_change_ticket.json"
            ticket = json.loads(ticket_path.read_text(encoding="utf-8-sig"))
            variables = copy.deepcopy(scenario["variables"])
            target = copy.deepcopy(scenario["target"])
            state = RunState(
                run_id=run_id,
                variables=variables,
                target=target,
                upstream_ready=bool(scenario["precheck"]["upstream_ready"]),
                anomaly_code=scenario["postcheck"].get("anomaly"),
            )
            run_root = self.artifact_root / run_id
            episode_root = run_root / "environment/episode_root"
            config_file = (
                episode_root / "service_layer/component/config/extension_item_mapping.json"
            )
            atomic_write_json(config_file, {})
            registry = ArtifactRegistry(run_root, run_id)
            episode = Episode(
                state=state,
                scenario_id=scenario_id,
                ticket=ticket,
                run_root=run_root,
                episode_root=episode_root,
                config_file=config_file,
                registry=registry,
            )
            self._episodes[run_id] = episode
            self._write_inputs(episode, scenario, scenario_path, ticket_path)
            self._snapshot(episode)
            return state.model_copy(deep=True)

    def reset(self, run_id: str) -> RunState:
        episode = self._episode(run_id)
        with episode.lock:
            scenario_path = self.data_root / "scenarios" / f"{episode.scenario_id}.yaml"
            scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
            episode.state = RunState(
                run_id=run_id,
                variables=copy.deepcopy(scenario["variables"]),
                target=copy.deepcopy(scenario["target"]),
                upstream_ready=bool(scenario["precheck"]["upstream_ready"]),
                anomaly_code=scenario["postcheck"].get("anomaly"),
            )
            episode.audit.clear()
            episode.presentation_events.clear()
            episode.current_presentation_task = None
            episode.presentation_failures.clear()
            episode.presentation_generation += 1
            episode.recording_monotonic_start_s = None
            for path in (
                episode.run_root / "environment/audit_events.jsonl",
                episode.run_root / "trajectory/raw_actions.jsonl",
                episode.run_root / "presentation/events.jsonl",
                episode.run_root / "presentation/snapshot.json",
                episode.run_root / "presentation/verification.json",
            ):
                path.unlink(missing_ok=True)
            atomic_write_json(episode.config_file, {})
            self._snapshot(episode)
            return episode.state.model_copy(deep=True)

    def state(self, run_id: str) -> RunState:
        episode = self._episode(run_id)
        with episode.lock:
            return episode.state.model_copy(deep=True)

    def episode(self, run_id: str) -> Episode:
        return self._episode(run_id)

    def set_recording_timebase(self, run_id: str, timebase: dict[str, Any]) -> None:
        episode = self._episode(run_id)
        start = timebase.get("recording_started_monotonic_s")
        if not isinstance(start, int | float):
            raise EpisodeError(
                "presentation_consistency_error",
                "recording monotonic origin is unavailable",
                status_code=500,
            )
        with episode.lock:
            episode.recording_monotonic_start_s = float(start)

    def presentation_task(self, run_id: str, item: PresentationInput) -> PresentationTask | None:
        episode = self._episode(run_id)
        with episode.lock:
            task = map_task(
                episode.ticket,
                episode.state,
                item,
                episode.current_presentation_task,
            )
            if task is not None:
                episode.current_presentation_task = task
            return task

    def presentation_stage(
        self,
        run_id: str,
        item: PresentationInput,
        task: PresentationTask | None,
    ) -> str:
        episode = self._episode(run_id)
        with episode.lock:
            return display_stage(episode.ticket, episode.state, item, task)

    def record_presentation(self, run_id: str, item: PresentationInput) -> PresentationEvent:
        episode = self._episode(run_id)
        with episode.lock:
            self._validate_presentation_run_id(run_id, item.arguments)
            self._validate_presentation_run_id(run_id, item.result)
            candidate_task = episode.current_presentation_task
            candidate_failures = list(episode.presentation_failures)
            failure = None
            correlated_start = next(
                (
                    event
                    for event in reversed(episode.presentation_events)
                    if event.tool_call_id == item.tool_call_id and event.status == "running"
                ),
                None,
            )
            has_explicit_control = any(
                key in item.arguments for key in ("bid", "route", "operation", "value")
            )
            if item.status in {"accepted", "succeeded", "failed", "rejected"} and (
                correlated_start is not None and not has_explicit_control
            ):
                task = correlated_start.task
            else:
                try:
                    task = map_task(
                        episode.ticket,
                        episode.state,
                        item,
                        candidate_task,
                    )
                except PresentationMappingError as exc:
                    failure = str(exc)
                    candidate_failures.append(failure)
                    task = candidate_task
                else:
                    if task is not None:
                        candidate_task = task

            sequence = len(episode.presentation_events) + 1
            event_arguments = item.arguments
            if not event_arguments and correlated_start is not None:
                event_arguments = correlated_start.arguments
            event = PresentationEvent(
                event_id=f"presentation-{sequence:05d}-{uuid.uuid4().hex[:8]}",
                sequence=sequence,
                run_id=run_id,
                event_type=item.runtime_event_type,
                timestamp=item.timestamp,
                monotonic_offset_s=(
                    time.monotonic() - episode.recording_monotonic_start_s
                    if episode.recording_monotonic_start_s is not None
                    else None
                ),
                tool_call_id=item.tool_call_id,
                action_id=item.action_id,
                stage=display_stage(episode.ticket, episode.state, item, task),
                task=task,
                tool_name=item.tool_name,
                tool_label_zh=item.tool_label_zh,
                tool_kind=item.tool_kind,
                status=item.status,
                arguments=copy.deepcopy(event_arguments),
                result=copy.deepcopy(item.result),
                evidence_refs=list(item.evidence_refs),
                failure=failure,
                failure_code=item.failure_code,
                plan=item.plan,
                public_model_output=item.public_model_output,
            )
            candidate_events = [*episode.presentation_events, event]
            observable = reduce_observable_state(episode.state, candidate_events)
            incident_delta = next(
                (
                    incident
                    for incident in observable.incidents
                    if incident.opened_sequence == sequence
                    or (
                        incident.recovery is not None
                        and incident.recovery.resolved_sequence == sequence
                    )
                ),
                None,
            )
            event = event.model_copy(
                update={
                    "decision_summary": observable.decision_summary,
                    "incident_delta": incident_delta,
                }
            )
            candidate_events[-1] = event
            candidate_snapshot = self._build_presentation_snapshot(
                episode.state,
                candidate_events,
                candidate_task,
                candidate_failures,
            )
            events_path = episode.run_root / "presentation/events.jsonl"
            snapshot_path = episode.run_root / "presentation/snapshot.json"
            events_existed = events_path.exists()
            events_size = events_path.stat().st_size if events_existed else 0
            try:
                append_jsonl(events_path, event.model_dump(mode="json"))
                atomic_write_json(snapshot_path, candidate_snapshot)
            except Exception:
                try:
                    self._rollback_presentation_jsonl(
                        events_path,
                        existed=events_existed,
                        size=events_size,
                    )
                except Exception as rollback_exc:
                    raise EpisodeError(
                        "presentation_consistency_error",
                        "failed to restore presentation ledger after publish failure",
                        status_code=500,
                    ) from rollback_exc
                raise

            episode.presentation_events = candidate_events
            episode.current_presentation_task = candidate_task
            episode.presentation_failures = candidate_failures
            return event.model_copy(deep=True)

    def presentation_events(self, run_id: str) -> list[PresentationEvent]:
        episode = self._episode(run_id)
        with episode.lock:
            return [event.model_copy(deep=True) for event in episode.presentation_events]

    def presentation_snapshot(self, run_id: str) -> dict[str, Any]:
        episode = self._episode(run_id)
        with episode.lock:
            return copy.deepcopy(self._presentation_snapshot_payload(episode))

    def presentation_stream_state(
        self, run_id: str
    ) -> tuple[int, list[PresentationEvent], dict[str, Any]]:
        episode = self._episode(run_id)
        with episode.lock:
            return (
                episode.presentation_generation,
                [event.model_copy(deep=True) for event in episode.presentation_events],
                copy.deepcopy(self._presentation_snapshot_payload(episode)),
            )

    def verify_presentation(self, run_id: str, *, observer_was_alive: bool) -> dict[str, Any]:
        episode = self._episode(run_id)
        with episode.lock:
            snapshot = self._presentation_snapshot_payload(episode)
            atomic_write_json(
                episode.run_root / "presentation/snapshot.json",
                snapshot,
            )
            report = verify_presentation_payload(
                episode.presentation_events,
                episode.presentation_failures,
                snapshot=snapshot,
                observer_was_alive=observer_was_alive,
            )
            atomic_write_json(
                episode.run_root / "presentation/verification.json",
                report,
            )
            return copy.deepcopy(report)

    def audit(self, run_id: str) -> list[AuditEvent]:
        episode = self._episode(run_id)
        with episode.lock:
            return [event.model_copy(deep=True) for event in episode.audit]

    def reserve_action(self, run_id: str, reservation: ActionReservation) -> ActionRecord:
        episode = self._episode(run_id)
        with episode.lock:
            state = episode.state
            self._require_active(state)
            if reservation.tool_name.startswith("browser_"):
                self._require_browser_gate(episode, reservation.tool_name)
            if reservation.page_state_version != state.state_version:
                raise EpisodeError("stale_state_version", "page state version is stale")
            if reservation.action_id in state.action_ledger:
                raise EpisodeError("action_replay", "action_id has already been reserved")
            record = ActionRecord(
                action_id=reservation.action_id,
                tool_name=reservation.tool_name,
                reserved_state_version=reservation.page_state_version,
            )
            state.action_ledger[reservation.action_id] = record
            return record.model_copy(deep=True)

    def consume_action(
        self, run_id: str, action_id: str, page_state_version: int, expected_tool: str
    ) -> None:
        episode = self._episode(run_id)
        state = episode.state
        self._require_active(state)
        record = state.action_ledger.get(action_id)
        if record is None:
            raise EpisodeError("unreserved_action", "action_id was not reserved")
        if record.consumed:
            raise EpisodeError("action_replay", "action_id has already been consumed")
        if record.tool_name != expected_tool:
            raise EpisodeError(
                "action_tool_mismatch", "reserved action tool does not match request"
            )
        if page_state_version != state.state_version:
            raise EpisodeError("stale_state_version", "page state version is stale")
        record.consumed = True

    def record(
        self,
        run_id: str,
        *,
        source: str,
        kind: str,
        status: str,
        action_id: str | None = None,
        arguments: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        node_id: str | None = None,
        mutate_version: bool = True,
    ) -> AuditEvent:
        episode = self._episode(run_id)
        with episode.lock:
            if mutate_version:
                episode.state.state_version += 1
            event_id = f"ev-{len(episode.audit) + 1:05d}-{uuid.uuid4().hex[:8]}"
            event = AuditEvent(
                event_id=event_id,
                sequence=len(episode.audit) + 1,
                run_id=run_id,
                action_id=action_id,
                source=source,
                kind=kind,
                stage=self.stage_for(episode.state),
                status=status,
                arguments=arguments or {},
                evidence_refs=evidence_refs or [],
                node_id=node_id,
                state_version=episode.state.state_version,
            )
            episode.audit.append(event)
            payload = event.model_dump(mode="json")
            append_jsonl(episode.run_root / "environment/audit_events.jsonl", payload)
            append_jsonl(episode.run_root / "trajectory/raw_actions.jsonl", payload)
            self._snapshot(episode)
            return event.model_copy(deep=True)

    def check_config(
        self, run_id: str, *, action_id: str, page_state_version: int, check: str
    ) -> tuple[AuditEvent, dict[str, Any]]:
        episode = self._episode(run_id)
        with episode.lock:
            self.consume_action(run_id, action_id, page_state_version, "browser_click")
            if episode.state.phase not in {EpisodePhase.CREATED, EpisodePhase.PRECHECKING}:
                raise EpisodeError("invalid_phase", "configuration precheck is closed")
            episode.state.phase = EpisodePhase.PRECHECKING
            ok = check == "extension_config" or episode.state.upstream_ready
            episode.state.config_checks[check] = ok
            event = self.record(
                run_id,
                source="backend",
                kind="ticket_config_check",
                status="succeeded" if ok else "failed",
                action_id=action_id,
                arguments={"check": check, "ok": ok},
                node_id="PRE_CONFIG",
            )
            observation = {"check": check, "ready": ok}
            if check == "extension_config":
                observation["variables"] = copy.deepcopy(episode.state.variables)
            return event, observation

    def monitor_query(
        self,
        run_id: str,
        *,
        action_id: str,
        page_state_version: int,
        query: str,
        region: str,
        cluster: str,
    ) -> tuple[AuditEvent, dict[str, Any]]:
        episode = self._episode(run_id)
        with episode.lock:
            self.consume_action(run_id, action_id, page_state_version, "browser_click")
            state = episode.state
            if region != state.target["region"] or cluster != state.target["cluster"]:
                raise EpisodeError(
                    "target_mismatch", "monitor target differs from the locked run target"
                )
            post = state.phase in {
                EpisodePhase.CHANGE_APPLIED,
                EpisodePhase.VERIFYING,
                EpisodePhase.ANOMALY_DETECTED,
                EpisodePhase.ROLLBACK_SUBMITTED,
                EpisodePhase.ROLLED_BACK,
            }
            if not post and state.phase not in {EpisodePhase.CREATED, EpisodePhase.PRECHECKING}:
                raise EpisodeError(
                    "invalid_phase", "monitor query is not valid in the current phase"
                )
            bucket = state.postchecks if post else state.prechecks
            result = "normal"
            observation: dict[str, Any] = {
                "query": query,
                "stage": "post_change" if post else "pre_change",
                "region": region,
                "cluster": cluster,
                "status": result,
            }
            if query == "capacity":
                observation["status"] = result = "sufficient"
            if post and query == "alarm" and state.causal_anomaly_armed and state.anomaly_code:
                result = "active"
                observation.update(
                    {
                        "status": "active",
                        "alarm_code": state.anomaly_code,
                        "caused_by_current_change": True,
                        "causal_add_job_id": state.causal_add_job_id,
                    }
                )
                state.phase = EpisodePhase.ANOMALY_DETECTED
            elif query == "alarm":
                observation.update({"status": "clear", "active_alarms": []})
                result = "clear"
            bucket[query] = result
            prefix = (
                "ANOMALY_FOUND"
                if observation.get("caused_by_current_change")
                else (
                    f"POST_{self._node_query(query)}" if post else f"PRE_{self._node_query(query)}"
                )
            )
            event = self.record(
                run_id,
                source="backend",
                kind="monitor_query",
                status="succeeded",
                action_id=action_id,
                arguments=observation,
                node_id=prefix,
            )
            return event, observation

    def decide(self, run_id: str, request: DecisionRequest) -> AuditEvent:
        episode = self._episode(run_id)
        with episode.lock:
            self._validate_evidence_refs(episode, request.evidence_refs)
            self.consume_action(run_id, request.action_id, request.page_state_version, "sop_decide")
            state = episode.state
            node_id: str
            if request.stage == "check_before_change" and request.decision == "proceed":
                required = {"alarm", "probe", "capacity", "runtime_metrics", "traffic"}
                if not required.issubset(state.prechecks) or not {
                    "extension_config",
                    "upstream_ready",
                }.issubset(state.config_checks):
                    raise EpisodeError(
                        "missing_precheck_evidence", "all prechecks must pass before proceed"
                    )
                state.phase = EpisodePhase.READY_TO_CHANGE
                node_id = "PRE_DECISION"
            elif request.stage == "change_implement" and request.decision == "proceed":
                if not self._job_succeeded(state, "add") or not state.add_grep_evidence_id:
                    raise EpisodeError(
                        "missing_implementation_evidence", "add job and grep must succeed"
                    )
                state.phase = EpisodePhase.CHANGE_APPLIED
                node_id = "IMPLEMENT_DECISION"
            elif request.stage == "change_verified" and request.decision == "complete":
                if state.phase == EpisodePhase.CHANGE_SUBMITTED:
                    raise EpisodeError(
                        "implementation_decision_required",
                        "call sop_decide with stage change_implement and decision proceed "
                        "before complete",
                    )
                if state.phase not in {EpisodePhase.CHANGE_APPLIED, EpisodePhase.VERIFYING}:
                    raise EpisodeError(
                        "invalid_phase", "complete is not valid in the current phase"
                    )
                self._require_progress_node(episode, "NORMAL_PROGRESS")
                required = {"alarm", "probe", "capacity", "runtime_metrics", "traffic"}
                if not required.issubset(state.postchecks) or not state.business_verified:
                    raise EpisodeError(
                        "missing_postcheck_evidence",
                        "postchecks and business verification are required",
                    )
                if not self.config_contains_target(run_id):
                    raise EpisodeError("external_state_mismatch", "locked configuration is absent")
                state.phase = EpisodePhase.COMPLETED
                state.terminal_outcome = "complete"
                node_id = "NORMAL_COMPLETE"
            elif request.stage == "change_verified" and request.decision == "rollback":
                if state.phase != EpisodePhase.ANOMALY_DETECTED:
                    raise EpisodeError(
                        "missing_anomaly_evidence", "rollback requires a causal anomaly"
                    )
                state.phase = EpisodePhase.ROLLBACK_SUBMITTED
                node_id = "ROLLBACK_DECISION"
            elif request.stage == "change_rollback" and request.decision == "rolled_back":
                self._require_progress_node(episode, "ROLLBACK_PROGRESS")
                if not self._job_succeeded(state, "remove") or not state.rollback_grep_evidence_id:
                    raise EpisodeError(
                        "missing_rollback_evidence", "remove job and rollback grep are required"
                    )
                if self.config_contains_target(run_id):
                    raise EpisodeError(
                        "external_state_mismatch", "locked configuration still exists"
                    )
                state.phase = EpisodePhase.ROLLED_BACK
                state.terminal_outcome = "rolled_back"
                node_id = "ROLLED_BACK"
            elif request.decision in {"block", "escalate", "insufficient_evidence"}:
                node_id = "TERMINAL_DECISION"
                state.terminal_outcome = request.decision
                state.phase = {
                    "block": EpisodePhase.BLOCKED_PRECHECK,
                    "escalate": EpisodePhase.ESCALATED,
                    "insufficient_evidence": EpisodePhase.INSUFFICIENT_EVIDENCE,
                }[request.decision]
            else:
                raise EpisodeError(
                    "invalid_decision_for_stage",
                    f"decision {request.decision} is not valid for stage {request.stage}",
                )
            decision = request.model_dump(mode="json")
            state.decisions.append(decision)
            return self.record(
                run_id,
                source="decision",
                kind="sop_decision",
                status="succeeded",
                action_id=request.action_id,
                arguments=decision,
                evidence_refs=request.evidence_refs,
                node_id=node_id,
            )

    def terminal_completed(
        self,
        run_id: str,
        *,
        action_id: str,
        page_state_version: int,
        command: str,
        exit_code: int,
        stdout: str,
        evidence_id: str,
    ) -> AuditEvent:
        episode = self._episode(run_id)
        with episode.lock:
            self.require_terminal_wait(run_id)
            self.consume_action(run_id, action_id, page_state_version, "terminal_execute")
            state = episode.state
            if self._job_succeeded(state, "add") and state.add_grep_evidence_id is None:
                if exit_code != 0 or not self._stdout_has_variables(state, stdout):
                    raise EpisodeError(
                        "add_grep_failed", "add grep did not prove the locked record"
                    )
                state.add_grep_evidence_id = evidence_id
                node_id = "ADD_GREP"
                add_job = self._latest_job(state, "add")
                if state.anomaly_code and add_job is not None:
                    state.causal_anomaly_armed = True
                    state.causal_add_job_id = add_job.job_id
                    state.causal_grep_evidence_id = evidence_id
            elif self._job_succeeded(state, "remove"):
                if exit_code != 1 or stdout:
                    raise EpisodeError(
                        "rollback_grep_failed", "rollback grep did not prove absence"
                    )
                if self.config_contains_target(run_id):
                    raise EpisodeError(
                        "external_state_mismatch", "target still exists after remove"
                    )
                state.rollback_grep_evidence_id = evidence_id
                node_id = "ROLLBACK_GREP"
            else:
                raise EpisodeError(
                    "invalid_phase", "no completed job is ready for terminal verification"
                )
            return self.record(
                run_id,
                source="terminal",
                kind="command_completed",
                status="succeeded",
                action_id=action_id,
                arguments={"command": command, "exit_code": exit_code, "stdout": stdout},
                evidence_refs=[evidence_id],
                node_id=node_id,
            )

    def require_terminal_wait(self, run_id: str) -> None:
        episode = self._episode(run_id)
        with episode.lock:
            state = episode.state
            self._require_active(state)
            if self._job_succeeded(state, "add") and state.add_grep_evidence_id is None:
                job = self._latest_job(state, "add")
                required_node = "ADD_WAIT"
            elif self._job_succeeded(state, "remove"):
                job = self._latest_job(state, "remove")
                required_node = "REMOVE_WAIT"
            else:
                return
            assert job is not None
            if not self._has_browser_wait(episode, required_node, job.job_id):
                raise EpisodeError(
                    "wait_required",
                    f"call browser_wait with exact job_id {job.job_id} before terminal_execute",
                )

    def validate_runtime_node(self, run_id: str, node_id: str | None) -> None:
        episode = self._episode(run_id)
        with episode.lock:
            self._require_active(episode.state)
            if node_id == "NORMAL_PROGRESS":
                required = {"alarm", "probe", "capacity", "runtime_metrics", "traffic"}
                missing = sorted(required.difference(episode.state.postchecks))
                if missing:
                    raise EpisodeError(
                        "postchecks_required",
                        "complete all five postchecks before NORMAL_PROGRESS; missing: "
                        + ", ".join(missing),
                    )
                job = self._latest_job(episode.state, "business_verify")
                if job is None or not self._has_browser_wait(episode, "BUSINESS_WAIT", job.job_id):
                    job_id = job.job_id if job is not None else "the business verification job"
                    raise EpisodeError(
                        "wait_required",
                        f"call browser_wait with exact job_id {job_id} before NORMAL_PROGRESS",
                    )
            elif node_id == "ROLLBACK_PROGRESS" and not self._has_event_node(
                episode, "ROLLBACK_GREP", source="terminal", kind="command_completed"
            ):
                raise EpisodeError(
                    "rollback_verification_required",
                    "complete remove, browser_wait, and rollback terminal grep before "
                    "ROLLBACK_PROGRESS",
                )

    def add_job(self, run_id: str, job: AutomationJob) -> AuditEvent:
        episode = self._episode(run_id)
        with episode.lock:
            state = episode.state
            accepted_evidence_id = f"job-{job.job_id}-accepted"
            job.evidence_refs.append(accepted_evidence_id)
            state.jobs[job.job_id] = job
            if job.operation == "add":
                state.phase = EpisodePhase.CHANGE_SUBMITTED
                node_id = "ADD_SUBMIT"
            elif job.operation == "remove":
                node_id = "REMOVE_SUBMIT"
            else:
                node_id = "BUSINESS_SUBMIT"
            return self.record(
                run_id,
                source="backend",
                kind="automation_job_submitted",
                status="accepted",
                action_id=job.action_id,
                arguments=job.model_dump(mode="json"),
                evidence_refs=[accepted_evidence_id],
                node_id=node_id,
            )

    def update_job(
        self, run_id: str, job_id: str, status: JobStatus, return_code: int | None
    ) -> AuditEvent:
        episode = self._episode(run_id)
        with episode.lock:
            job = episode.state.jobs.get(job_id)
            if job is None:
                raise EpisodeError("unknown_job", f"unknown job: {job_id}", status_code=404)
            valid = {
                JobStatus.ACCEPTED: {JobStatus.RUNNING},
                JobStatus.RUNNING: {JobStatus.SUCCEEDED, JobStatus.FAILED},
            }
            if status not in valid.get(job.status, set()):
                raise EpisodeError(
                    "invalid_job_transition", f"cannot move {job.status} to {status}"
                )
            job.status = status
            job.business_return_code = return_code
            evidence_id = f"job-{job_id}-{status.value}"
            job.evidence_refs.append(evidence_id)
            return self.record(
                run_id,
                source="backend",
                kind="automation_job_status",
                status=status.value,
                action_id=job.action_id,
                arguments={
                    "job_id": job_id,
                    "operation": job.operation,
                    "return_code": return_code,
                },
                evidence_refs=[evidence_id],
            )

    def job(self, run_id: str, job_id: str) -> AutomationJob:
        state = self._episode(run_id).state
        job = state.jobs.get(job_id)
        if job is None:
            raise EpisodeError("unknown_job", f"unknown job: {job_id}", status_code=404)
        return job.model_copy(deep=True)

    def config_contains_target(self, run_id: str) -> bool:
        episode = self._episode(run_id)
        try:
            payload = json.loads(episode.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        key = f"{episode.state.variables['TenantId']}:{episode.state.variables['ItemCode']}"
        return key in payload

    @staticmethod
    def stage_for(state: RunState) -> str:
        if state.phase in {
            EpisodePhase.CREATED,
            EpisodePhase.PRECHECKING,
            EpisodePhase.READY_TO_CHANGE,
        }:
            return "check_before_change"
        if state.phase in {EpisodePhase.CHANGE_SUBMITTED}:
            return "change_implement"
        if state.phase in {
            EpisodePhase.CHANGE_APPLIED,
            EpisodePhase.VERIFYING,
            EpisodePhase.ANOMALY_DETECTED,
        }:
            return "change_verified"
        if state.phase in {EpisodePhase.ROLLBACK_SUBMITTED, EpisodePhase.ROLLED_BACK}:
            return "change_rollback"
        return state.phase.value

    @staticmethod
    def _validate_presentation_run_id(run_id: str, value: Any) -> None:
        if isinstance(value, dict):
            embedded = value.get("run_id")
            if embedded is not None and embedded != run_id:
                raise EpisodeError(
                    "presentation_run_mismatch",
                    f"presentation event run_id {embedded!r} does not match path run {run_id!r}",
                )
            for nested in value.values():
                EpisodeStore._validate_presentation_run_id(run_id, nested)
        elif isinstance(value, list):
            for nested in value:
                EpisodeStore._validate_presentation_run_id(run_id, nested)

    @staticmethod
    def _presentation_snapshot_payload(episode: Episode) -> dict[str, Any]:
        return EpisodeStore._build_presentation_snapshot(
            episode.state,
            episode.presentation_events,
            episode.current_presentation_task,
            episode.presentation_failures,
        )

    @staticmethod
    def _build_presentation_snapshot(
        state: RunState,
        events: list[PresentationEvent],
        current_task: PresentationTask | None,
        failures: list[str],
    ) -> dict[str, Any]:
        active_by_call: dict[str, PresentationEvent] = {}
        terminal_statuses = {"accepted", "succeeded", "failed", "rejected"}
        completed: dict[str, PresentationTask] = {}
        for event in events:
            if event.status == "running" and event.tool_call_id:
                active_by_call[event.tool_call_id] = event
            elif event.status in terminal_statuses and event.tool_call_id:
                active_by_call.pop(event.tool_call_id, None)
            if event.status == "succeeded" and event.failure is None and event.task is not None:
                completed[event.task.source_sha256] = event.task

        last_event = events[-1] if events else None
        if state.terminal_outcome is not None:
            stage = "terminal"
        elif state.phase in {
            EpisodePhase.ROLLBACK_SUBMITTED,
            EpisodePhase.ROLLED_BACK,
        }:
            stage = "change_rollback"
        elif last_event is not None:
            stage = last_event.stage
        else:
            stage = "check_before_change"
        observable = reduce_observable_state(state, events)
        snapshot = PresentationSnapshot(
            run_id=state.run_id,
            phase=state.phase,
            stage=stage,
            terminal_outcome=state.terminal_outcome,
            current_task=current_task,
            plan=observable.plan,
            current_action=observable.current_action,
            last_result=observable.last_result,
            public_model_output=observable.public_model_output,
            decision_summary=observable.decision_summary,
            incidents=list(observable.incidents),
            critical_history=list(observable.critical_history),
            in_flight=list(active_by_call.values()),
            last_event=last_event,
            last_sequence=last_event.sequence if last_event is not None else 0,
            completed_steps=list(completed.values()),
            next_step=(
                current_task.check_name
                if current_task is not None
                else "\u7b49\u5f85 Agent \u8bfb\u53d6\u53d8\u66f4\u5355"
            ),
            presentation_failures=list(failures),
        )
        return snapshot.model_dump(mode="json")

    @staticmethod
    def _rollback_presentation_jsonl(path: Path, *, existed: bool, size: int) -> None:
        if not existed:
            path.unlink(missing_ok=True)
            return
        with path.open("r+b") as handle:
            handle.truncate(size)
            handle.flush()
            os.fsync(handle.fileno())

    def _episode(self, run_id: str) -> Episode:
        episode = self._episodes.get(run_id)
        if episode is None:
            raise EpisodeError("unknown_run", f"unknown run: {run_id}", status_code=404)
        return episode

    @staticmethod
    def _require_active(state: RunState) -> None:
        if state.terminal_outcome is not None:
            raise EpisodeError("terminal_outcome", "run has already reached a terminal outcome")

    @classmethod
    def _require_browser_gate(cls, episode: Episode, tool_name: str) -> None:
        state = episode.state
        ticket_read = any(
            event.source == "browser"
            and event.kind == "browser_action"
            and event.status == "succeeded"
            and event.arguments.get("tool_name") == "browser_navigate"
            and event.node_id == "TICKET_READ"
            for event in episode.audit
        )
        if (
            ticket_read
            and tool_name != "browser_observe"
            and not cls._has_task_node(episode, "PLAN_CREATED", "task_planner")
        ):
            raise EpisodeError(
                "plan_required",
                "call task_planner to record PLAN_CREATED before operational browser actions",
            )
        if state.phase == EpisodePhase.CHANGE_SUBMITTED and state.add_grep_evidence_id:
            raise EpisodeError(
                "implementation_decision_required",
                "call sop_decide with stage change_implement and decision proceed before "
                "post-change actions",
            )
        required_node: str | None = None
        if state.phase == EpisodePhase.READY_TO_CHANGE:
            required_node = "PRE_PROGRESS"
        elif state.phase == EpisodePhase.CHANGE_APPLIED:
            required_node = "IMPLEMENT_PROGRESS"
        if required_node is not None:
            cls._require_progress_node(episode, required_node)

    @staticmethod
    def _require_progress_node(episode: Episode, node_id: str) -> None:
        if not EpisodeStore._has_task_node(episode, node_id, "task_progress_check"):
            raise EpisodeError(
                "progress_required",
                f"call task_progress_check to record {node_id} before the next action",
            )

    @staticmethod
    def _has_task_node(episode: Episode, node_id: str, tool_name: str) -> bool:
        return any(
            event.source == "runtime"
            and event.kind == "tool_result"
            and event.status == "succeeded"
            and event.arguments.get("tool_name") == tool_name
            and event.node_id == node_id
            for event in episode.audit
        )

    @staticmethod
    def _has_event_node(episode: Episode, node_id: str, *, source: str, kind: str) -> bool:
        return any(
            event.source == source
            and event.kind == kind
            and event.status == "succeeded"
            and event.node_id == node_id
            for event in episode.audit
        )

    @staticmethod
    def _has_browser_wait(episode: Episode, node_id: str, job_id: str) -> bool:
        return any(
            event.source == "browser"
            and event.kind == "browser_action"
            and event.status == "succeeded"
            and event.arguments.get("tool_name") == "browser_wait"
            and event.arguments.get("job_id") == job_id
            and event.node_id == node_id
            for event in episode.audit
        )

    @staticmethod
    def _validate_evidence_refs(episode: Episode, evidence_refs: list[str]) -> None:
        known: set[str] = set()
        for event in episode.audit:
            if event.status not in {"accepted", "succeeded"}:
                continue
            known.add(event.event_id)
            if event.source in {"backend", "browser", "terminal", "state"}:
                known.update(event.evidence_refs)
        unknown = sorted(set(evidence_refs).difference(known))
        if unknown:
            raise EpisodeError(
                "unknown_evidence_ref",
                "evidence refs are not persisted for this run: " + ", ".join(unknown),
                status_code=422,
            )

    @staticmethod
    def _node_query(query: str) -> str:
        return {
            "alarm": "ALARM",
            "probe": "PROBE",
            "capacity": "CAPACITY",
            "runtime_metrics": "RUNTIME",
            "traffic": "TRAFFIC",
        }[query]

    @staticmethod
    def _latest_job(state: RunState, operation: str) -> AutomationJob | None:
        jobs = [job for job in state.jobs.values() if job.operation == operation]
        return jobs[-1] if jobs else None

    @classmethod
    def _job_succeeded(cls, state: RunState, operation: str) -> bool:
        job = cls._latest_job(state, operation)
        return bool(job and job.status == JobStatus.SUCCEEDED and job.business_return_code == 0)

    @staticmethod
    def _stdout_has_variables(state: RunState, stdout: str) -> bool:
        return all(value in stdout for value in state.variables.values())

    def _write_inputs(
        self, episode: Episode, scenario: dict[str, Any], scenario_path: Path, ticket_path: Path
    ) -> None:
        input_root = episode.run_root / "input"
        atomic_write_json(input_root / "item_change_ticket.json", episode.ticket)
        atomic_write_json(input_root / "scenario.json", scenario)
        manifest = json.loads(
            (self.data_root / "dataset_manifest.json").read_text(encoding="utf-8")
        )
        atomic_write_json(input_root / "dataset_manifest.json", manifest)
        atomic_write_json(
            input_root / "ground_truth_hashes.json",
            {
                "ticket_source": str(ticket_path),
                "scenario_source": str(scenario_path),
                "declared_hashes": manifest["contract"]["file_sha256"],
                "coworker_demo": manifest["coworker_demo"],
            },
        )
        for name in (
            "item_change_ticket.json",
            "scenario.json",
            "dataset_manifest.json",
            "ground_truth_hashes.json",
        ):
            episode.registry.register(f"input/{name}", producer="episode_store")

    def _snapshot(self, episode: Episode) -> None:
        payload = episode.state.model_dump(mode="json")
        append_jsonl(episode.run_root / "environment/state_snapshots.jsonl", payload)
        atomic_write_json(episode.run_root / "environment/state.json", payload)
