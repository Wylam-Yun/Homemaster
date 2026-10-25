"""DTOs and parser for entity canonicalization arbitration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class EntityMergeVerdict(BaseModel):
    """One arbitration verdict for a candidate entity pair."""

    pair_index: int
    same_entity: bool
    canonical_entity_id: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    reason: str = ""


class EntityMergeBatch(BaseModel):
    """Batch of arbitration verdicts for the candidate pairs of one run."""

    verdicts: list[EntityMergeVerdict] = Field(default_factory=list)

    @field_validator("verdicts", mode="before")
    @classmethod
    def _coerce_single_verdict(cls, value):
        """接受模型把单个判定裸输出为对象而非数组的情况。"""

        if isinstance(value, dict):
            return [value]
        return value


def entity_merge_parser(content: str) -> EntityMergeBatch:
    """Parse entity merge arbitration JSON output."""

    text = content.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    return EntityMergeBatch.model_validate_json(text)


__all__ = [
    "EntityMergeBatch",
    "EntityMergeVerdict",
    "entity_merge_parser",
]
