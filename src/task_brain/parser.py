"""Rule-first instruction parser for Phase A."""

from __future__ import annotations

from task_brain.domain import ParsedTask, TargetObject, TaskIntent, TaskRequest

_MEDICINE_KEYWORDS = ("药盒",)
_CHECK_KEYWORDS = ("看看", "还在", "在不在")

_CUP_KEYWORDS = ("水杯", "杯子")
_FETCH_ACTION_KEYWORDS = ("拿给我", "拿给", "给我", "拿来", "拿一下")
_LOCATION_HINTS = ("厨房", "客厅", "卧室", "卫生间")


def parse_instruction(request: TaskRequest) -> ParsedTask:
    """Parse one user request into a structured task.

    Phase A is intentionally rule-first and only supports:
    - check_object_presence
    - fetch_object
    """
    text = request.utterance.strip()
    normalized = text.lower()

    if _is_fetch_cup(normalized):
        return ParsedTask(
            intent=TaskIntent.FETCH_OBJECT,
            target_object=TargetObject(
                category="cup",
                aliases=[alias for alias in _CUP_KEYWORDS if alias in normalized] or ["水杯"],
                attributes=[],
            ),
            quantity=1,
            explicit_location_hint=_extract_location_hint(normalized),
            delivery_target=_extract_delivery_target(normalized),
            requires_navigation=True,
            requires_manipulation=True,
        )

    if _is_check_medicine(normalized):
        return ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(
                category="medicine_box",
                aliases=[alias for alias in _MEDICINE_KEYWORDS if alias in normalized] or ["药盒"],
                attributes=[],
            ),
            quantity=1,
            explicit_location_hint=_extract_location_hint(normalized),
            delivery_target=None,
            requires_navigation=True,
            requires_manipulation=False,
        )

    raise ValueError(f"unsupported instruction for Phase A parser: {text}")


def _is_check_medicine(text: str) -> bool:
    return any(keyword in text for keyword in _MEDICINE_KEYWORDS) and any(
        keyword in text for keyword in _CHECK_KEYWORDS
    )


def _is_fetch_cup(text: str) -> bool:
    has_cup = any(keyword in text for keyword in _CUP_KEYWORDS)
    has_action = any(keyword in text for keyword in _FETCH_ACTION_KEYWORDS)
    return has_cup and has_action


def _extract_location_hint(text: str) -> str | None:
    for hint in _LOCATION_HINTS:
        if hint in text:
            return hint
    return None


def _extract_delivery_target(text: str) -> str | None:
    if "给我" in text or "拿给我" in text:
        return "user"
    return None


__all__ = ["parse_instruction"]
