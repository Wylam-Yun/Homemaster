"""Typed fact records and reusable successful-path procedure records."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MemorySource = Literal["user_statement", "environment_observation"]
SubjectType = Literal["object", "device", "room", "place", "service", "account", "other"]
ProcedureAction = Literal[
    "open_page",
    "click",
    "confirm",
    "select",
    "fill",
    "set_datetime",
    "wait",
    "read",
    "terminal",
]
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SLOT_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FORBIDDEN_TARGET_KEYS = frozenset(
    {
        "ref",
        "target_ref",
        "element_id",
        "snapshot_id",
        "css",
        "xpath",
        "x",
        "y",
        "url",
        "href",
        "entry_url",
    }
)


class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SubjectType
    name: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)

    @field_validator("name", "id")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class FactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    memory_type: Literal["fact"] = "fact"
    subject: Subject
    predicate: str = Field(
        description="Lowercase English snake_case relationship, such as location or status."
    )
    value: str | int | float | bool | dict[str, Any]
    source: MemorySource

    @field_validator("predicate")
    @classmethod
    def _predicate_is_snake_case(cls, value: str) -> str:
        if not _SNAKE_CASE.fullmatch(value):
            raise ValueError("predicate must be lowercase snake_case")
        return value

    @field_validator("value")
    @classmethod
    def _value_is_explainable(cls, value: object) -> object:
        if value is None or isinstance(value, bytes):
            raise ValueError("fact value must be explainable JSON and not null")
        return value


class ProcedureEntry(BaseModel):
    """Logical start page. Never an absolute URL."""

    model_config = ConfigDict(extra="forbid")

    page_name: str = Field(min_length=1)
    menu_path: tuple[str, ...] = ()
    route_hint: str | None = None

    @field_validator("page_name")
    @classmethod
    def _strip_page_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("route_hint")
    @classmethod
    def _path_only_route(cls, value: str | None) -> str | None:
        if value is None:
            return None
        hint = value.strip()
        if not hint:
            return None
        if "://" in hint or hint.startswith("//") or ":" in hint.split("/", 1)[0]:
            raise ValueError("route_hint must be a host-free path")
        if not hint.startswith("/"):
            raise ValueError("route_hint must start with /")
        return hint


class ProcedureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True
    binds_from: str | None = None

    @field_validator("name")
    @classmethod
    def _slot_name(cls, value: str) -> str:
        name = value.strip()
        if not _SLOT_NAME.fullmatch(name):
            raise ValueError("input name must be lowercase snake_case")
        return name


class SemanticTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_name: str | None = None
    role: str | None = None
    name: str | None = None
    text: str | None = None
    label: str | None = None
    command_template: str | None = None

    @model_validator(mode="after")
    def _has_selector(self) -> SemanticTarget:
        if not any(
            (
                self.page_name,
                self.role,
                self.name,
                self.text,
                self.label,
                self.command_template,
            )
        ):
            raise ValueError("target must include a semantic selector")
        return self


class ExpectField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    equals: str = Field(min_length=1)


class ExpectTerminal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_code: int = 0
    stdout_exact: str | None = None
    stdout_contains: str | None = None


class ProcedureExpect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_text: str | None = None
    field: ExpectField | None = None
    row: dict[str, str] | None = None
    no_row: dict[str, str] | None = None
    terminal: ExpectTerminal | None = None
    dialog_closed: bool | None = None

    @model_validator(mode="after")
    def _has_condition(self) -> ProcedureExpect:
        if not any(
            (
                self.visible_text,
                self.field,
                self.row,
                self.no_row,
                self.terminal,
                self.dialog_closed is not None,
            )
        ):
            raise ValueError("expect must include at least one condition")
        return self


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    phase: str | None = None
    action: ProcedureAction
    target: SemanticTarget
    use_input: str | None = None
    expect: ProcedureExpect
    abort_when: tuple[str, ...] = ()
    note: str | None = None

    @field_validator("use_input")
    @classmethod
    def _use_input_slot(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not _SLOT_NAME.fullmatch(name):
            raise ValueError("use_input must be lowercase snake_case")
        return name

    @model_validator(mode="after")
    def _action_contracts(self) -> ProcedureStep:
        if self.action == "terminal" and not self.target.command_template:
            raise ValueError("terminal step requires target.command_template")
        if self.action == "open_page" and not self.target.page_name:
            raise ValueError("open_page step requires target.page_name")
        return self


class ProcedureSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_of: tuple[str, ...] = Field(min_length=1)


class ProcedureRecord(BaseModel):
    """Reusable successful-path SOP. Instance results belong in episodic memory."""

    model_config = ConfigDict(extra="forbid")

    memory_type: Literal["procedure"] = "procedure"
    name: str = Field(min_length=1)
    sop_id: str | None = None
    entry: ProcedureEntry
    inputs: tuple[ProcedureInput, ...] = ()
    abort_when: tuple[str, ...] = ()
    steps: tuple[ProcedureStep, ...] = Field(min_length=1)
    success: ProcedureSuccess

    @model_validator(mode="after")
    def _orders_inputs_and_slots(self) -> ProcedureRecord:
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(self.steps) + 1)):
            raise ValueError("procedure step orders must be continuous from 1")
        input_names = {item.name for item in self.inputs}
        if len(input_names) != len(self.inputs):
            raise ValueError("procedure input names must be unique")
        for step in self.steps:
            if step.use_input is not None and step.use_input not in input_names:
                raise ValueError(f"use_input {step.use_input!r} is not a declared input")
        dumped = self.model_dump(mode="json")
        _reject_forbidden_keys(dumped)
        return self


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_TARGET_KEYS:
                raise ValueError(f"procedure must not include {key}")
            _reject_forbidden_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_forbidden_keys(child)


MemoryRecord = Annotated[FactRecord | ProcedureRecord, Field(discriminator="memory_type")]
MEMORY_RECORD_ADAPTER = TypeAdapter(MemoryRecord)


__all__ = [
    "ExpectField",
    "ExpectTerminal",
    "FactRecord",
    "MEMORY_RECORD_ADAPTER",
    "MemoryRecord",
    "MemorySource",
    "ProcedureAction",
    "ProcedureEntry",
    "ProcedureExpect",
    "ProcedureInput",
    "ProcedureRecord",
    "ProcedureStep",
    "ProcedureSuccess",
    "SemanticTarget",
    "Subject",
]
