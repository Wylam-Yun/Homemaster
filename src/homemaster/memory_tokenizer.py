"""Tokenization helpers for Stage 03 object memory retrieval."""

from __future__ import annotations

import re
from typing import Any, Protocol

import jieba


class MemoryTokenizer(Protocol):
    def tokenize(self, text: str) -> list[str]:
        """Tokenize text for memory retrieval."""


ROOM_ALIASES: dict[str, tuple[str, ...]] = {
    "kitchen": ("厨房", "kitchen"),
    "living_room": ("客厅", "living room", "living_room"),
    "pantry": ("储物间", "pantry"),
}

ANCHOR_ALIASES: dict[str, tuple[str, ...]] = {
    "table": ("桌子", "餐桌", "table"),
    "cabinet": ("柜子", "药柜", "cabinet"),
    "shelf": ("搁架", "架子", "shelf"),
    "counter": ("台面", "柜台", "counter"),
}

OBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "cup": ("水杯", "杯子", "cup"),
    "medicine_box": ("药盒", "药箱", "medicine_box", "medicine"),
}

_TOKEN_CLEAN_RE = re.compile(r"^[\s,.;:!?，。；：！？、（）()【】\\[\\]{}<>《》\"'`]+$")


class JiebaMemoryTokenizer:
    """Jieba tokenizer with a memory-derived domain dictionary."""

    def __init__(self, *, domain_terms: list[str] | tuple[str, ...] = ()) -> None:
        self.domain_terms = tuple(
            dict.fromkeys(term.strip() for term in domain_terms if term.strip())
        )
        self._tokenizer = jieba.Tokenizer()
        for term in self.domain_terms:
            self._tokenizer.add_word(term, freq=2_000_000)

    def tokenize(self, text: str) -> list[str]:
        normalized = _normalize_text(text)
        tokens = []
        for token in self._tokenizer.lcut(normalized, cut_all=False):
            cleaned = token.strip()
            if not cleaned or _TOKEN_CLEAN_RE.match(cleaned):
                continue
            tokens.append(cleaned)
        return tokens


def build_domain_terms_from_object_memory(memory_payload: dict[str, Any]) -> list[str]:
    """Build tokenizer terms from object memory content and stable domain aliases."""

    terms: list[str] = []
    for memory in _object_memory_items(memory_payload):
        object_category = _as_text(memory.get("object_category"))
        _add_term(terms, object_category)
        for alias in OBJECT_ALIASES.get(object_category, ()):
            _add_term(terms, alias)
        for alias in memory.get("aliases", []):
            _add_term(terms, _as_text(alias))

        anchor = memory.get("anchor")
        if not isinstance(anchor, dict):
            continue
        room_id = _as_text(anchor.get("room_id"))
        anchor_type = _as_text(anchor.get("anchor_type"))
        for raw in (
            room_id,
            _as_text(anchor.get("anchor_id")),
            anchor_type,
            _as_text(anchor.get("viewpoint_id")),
            _as_text(anchor.get("display_text")),
        ):
            _add_term(terms, raw)
        for alias in ROOM_ALIASES.get(room_id, ()):
            _add_term(terms, alias)
        for alias in ANCHOR_ALIASES.get(anchor_type, ()):
            _add_term(terms, alias)

    return list(dict.fromkeys(terms))


def _object_memory_items(memory_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = memory_payload.get("object_memory", [])
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _add_term(terms: list[str], value: str) -> None:
    value = value.strip()
    if value:
        terms.append(value)


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_text(text: str) -> str:
    return text.replace("_", " ")
