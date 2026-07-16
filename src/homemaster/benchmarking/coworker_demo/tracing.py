"""Local-first runtime event sink with best-effort HTTP mirroring."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient


class CoworkerTraceSink:
    def __init__(
        self,
        path: Path,
        client: EnvironmentClient,
        run_id: str,
        *,
        transcript_path: Path | None = None,
    ) -> None:
        self.path = path
        self.client = client
        self.run_id = run_id
        self.transcript_path = transcript_path
        self.mirror_failures: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: Any) -> None:
        payload = asdict(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            handle.flush()
        if self.transcript_path is not None:
            self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            with self.transcript_path.open("a", encoding="utf-8") as transcript:
                transcript.write(self._transcript_line(payload) + "\n")
                transcript.flush()
        try:
            self.client.runtime_event(
                self.run_id,
                action_id=payload.get("tool_call_id"),
                tool_name=str(payload.get("name") or payload.get("type") or "runtime_event"),
                arguments={"runtime_event_type": payload.get("type")},
            )
        except Exception as exc:
            self.mirror_failures.append(f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _transcript_line(payload: dict[str, Any]) -> str:
        event_type = str(payload.get("type") or "event")
        name = str(payload.get("name") or "")
        event_payload = payload.get("payload") or {}
        if event_type == "tool.call_started":
            return (
                f"TOOL {name}: {json.dumps(event_payload.get('arguments', {}), ensure_ascii=False)}"
            )
        if event_type in {"tool.call_completed", "tool.call_failed"}:
            return f"RESULT {name}: {str(event_payload.get('result') or '')[:800]}"
        if event_type == "assistant.reply":
            return f"MODEL: {event_payload.get('reply', '')}"
        if event_type == "assistant.thinking":
            return "MODEL: working through the change procedure"
        return event_type
