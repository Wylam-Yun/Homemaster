"""Typed V2.1 fact and procedure memory records."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MemorySource = Literal["user_statement", "environment_observation"]
SubjectType = Literal["object", "device", "room", "place", "service", "account", "other"]
ProcedureAction = Literal["open", "click", "fill", "select", "wait", "extract"]
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_FORBIDDEN_QUERY_NAMES = re.compile(
    r"(?:token|secret|password|credential|session|cookie|signature|api[_-]?key)",
    re.IGNORECASE,
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


class ProcedureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    action: ProcedureAction
    target: dict[str, Any]
    expect: dict[str, Any] | None = None
    output: str | None = None

    @model_validator(mode="after")
    def _has_verification_contract(self) -> ProcedureStep:
        if not self.target:
            raise ValueError("procedure target must not be empty")
        if self.action in {"open", "click", "fill", "select", "wait"} and not self.expect:
            raise ValueError(f"{self.action} step requires expect")
        if self.action == "extract" and not self.output:
            raise ValueError("extract step requires output")
        return self


class ProcedureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    memory_type: Literal["procedure"] = "procedure"
    name: str = Field(min_length=1)
    entry_url: str
    steps: tuple[ProcedureStep, ...] = Field(min_length=1)
    success: dict[str, Any]
    source: Literal["environment_observation"] = "environment_observation"
    inputs: tuple[ProcedureInput, ...] = ()
    preconditions: tuple[dict[str, Any], ...] = ()

    @field_validator("entry_url")
    @classmethod
    def _safe_absolute_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("entry_url must be an absolute http/https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("entry_url must not contain userinfo")
        if any(_FORBIDDEN_QUERY_NAMES.search(name) for name, _ in parse_qsl(parsed.query)):
            raise ValueError("entry_url must not contain credential/session query parameters")
        return value

    @model_validator(mode="after")
    def _orders_and_success_are_valid(self) -> ProcedureRecord:
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(self.steps) + 1)):
            raise ValueError("procedure step orders must be continuous from 1")
        if not self.success:
            raise ValueError("procedure success must not be empty")
        return self


MemoryRecord = Annotated[FactRecord | ProcedureRecord, Field(discriminator="memory_type")]
MEMORY_RECORD_ADAPTER = TypeAdapter(MemoryRecord)


__all__ = [
    "FactRecord",
    "MEMORY_RECORD_ADAPTER",
    "MemoryRecord",
    "MemorySource",
    "ProcedureRecord",
    "ProcedureStep",
    "Subject",
]
