"""Observability, session persistence, and interruption configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class ObservabilityConfig(BaseModel):
    """Runtime observability settings shared by CLI, traces, sessions, and interrupts."""

    console_enabled: bool = True
    console_show_thinking: bool = True
    console_thinking_first_line_only: bool = True
    console_verbose_to_expand: bool = True
    default_output_level: str = "medium"

    trace_dir: str = "~/.homemaster/trace"
    trace_full_payload: bool = True
    trace_rotation_max_mb: int = Field(default=100, ge=1)

    session_dir: str = "~/.homemaster/sessions"
    save_session_per_iteration: bool = True
    save_on_sigint: bool = True
    strip_images_in_snapshot: bool = True

    interrupt_enabled: bool = True
    interrupt_abort_llm_stream: bool = True

    schema_version: int = SCHEMA_VERSION
