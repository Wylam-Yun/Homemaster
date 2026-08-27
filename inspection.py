"""Live DOM inspection for the phase-one browser contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homemaster.browser.contracts import BrowserElement
from homemaster.browser.targets import semantic_text_matches

_CELL_ROLES = frozenset({"cell", "gridcell"})

INTERACTIVE_SELECTOR = ",".join(
    (
        "input:not([type=hidden])",
        "textarea",
        "select",
        "button",
        "a[href]",
        "[contenteditable=true]",
        "[role]",
        "[aria-label]",
        "[role=button]",
        "[role=link]",
        "[role=textbox]",
        "[role=combobox]",
        "[role=checkbox]",
        "[role=switch]",
        "[role=radio]",
        "[role=tab]",
        "[role=option]",
        "[data-browser-action]",
    )
)

_ELEMENT_STATE_JS = r"""
(el) => {
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const implicitRole = tag === 'button' ? 'button'
    : tag === 'a' && el.hasAttribute('href') ? 'link'
    : tag === 'select' ? 'combobox'
    : tag === 'textarea' ? 'textbox'
    : tag === 'input' && ['checkbox', 'radio'].includes(type) ? type
    : tag === 'input' ? 'textbox' : '';
  const role = el.getAttribute('role') || implicitRole;
  const labels = el.labels
    ? Array.from(el.labels).map(x => x.innerText || x.textContent || '')
    : [];
  const closestLabel = el.closest('label');
  if (!labels.length && closestLabel) {
    labels.push(closestLabel.innerText || closestLabel.textContent || '');
  }
  const label = labels.map(x => x.trim()).filter(Boolean).join(' ');
  const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
    .map(id => document.getElementById(id)).filter(Boolean)
    .map(x => (x.innerText || x.textContent || '').trim()).join(' ');
  const text = (el.innerText || el.textContent || '').trim();
  const name = (el.getAttribute('aria-label') || labelledBy || label ||
    el.getAttribute('placeholder') || el.getAttribute('alt') ||
    el.getAttribute('title') || text).trim();
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  const visible = style.display !== 'none' && style.visibility !== 'hidden' &&
    Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
  const ariaDisabled = el.getAttribute('aria-disabled') === 'true';
  const enabled = !el.disabled && !ariaDisabled;
  const editable = enabled && !el.readOnly &&
    (tag === 'input' || tag === 'textarea' || el.isContentEditable);
  const ariaChecked = el.getAttribute('aria-checked');
  const checked = ('checked' in el) ? Boolean(el.checked)
    : ariaChecked === null ? null : ariaChecked === 'true';
  const selected = ('selected' in el) ? Boolean(el.selected) :
    el.getAttribute('aria-selected') === null ? null : el.getAttribute('aria-selected') === 'true';
  const expanded = el.getAttribute('aria-expanded') === null
    ? null : el.getAttribute('aria-expanded') === 'true';
  let obscured = false;
  if (visible) {
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const centerInViewport = x >= 0 && x < innerWidth && y >= 0 && y < innerHeight;
    if (centerInViewport) {
      const top = document.elementFromPoint(x, y);
      obscured = Boolean(top && top !== el && !el.contains(top) && !top.contains(el));
    }
  }
  let value = null;
  if (type !== 'password') {
    if ('value' in el) value = String(el.value);
    else if (el.isContentEditable) value = text;
  }
  const options = tag === 'select' ? Array.from(el.options).map(option => ({
    label: (option.label || option.textContent || '').trim(), value: option.value,
    selected: option.selected, disabled: option.disabled
  })) : [];
  return {
    tag, type, role, name, label, text, value, visible, enabled, editable,
    required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
    readonly: Boolean(el.readOnly || el.getAttribute('aria-readonly') === 'true'),
    checked, selected, expanded, obscured, options,
    selectedValues: tag === 'select'
      ? Array.from(el.selectedOptions).map(option => option.value) : [],
    stableId: el.id || '', testId: el.getAttribute('data-testid') || '',
    browserAction: el.getAttribute('data-browser-action') || '',
    ariaControls: el.getAttribute('aria-controls') || '',
    accept: el.getAttribute('accept') || '', multiple: Boolean(el.multiple),
    min: el.getAttribute('min') || '', max: el.getAttribute('max') || '',
    step: el.getAttribute('step') || ''
  };
}
"""

# Keep the state projection in the page so a large semantic query uses one
# browser round trip per frame instead of one CDP evaluation per element.
_COLLECT_ELEMENT_STATES_JS = r"""
([selector, state_source]) => {
  const state_of = eval(`(${state_source})`);
  return Array.from(document.querySelectorAll(selector)).map(state_of);
}
"""


async def collect_elements(
    page: Any,
    *,
    limit: int,
    filters: Mapping[str, object] | None = None,
) -> tuple[list[BrowserElement], int, list[dict[str, object]]]:
    elements: list[BrowserElement] = []
    total = 0
    semantic_filters = filters or {}
    frames: list[dict[str, object]] = []
    selector = _selector_for_filters(semantic_filters)
    for frame_index, frame in enumerate(page.frames):
        frame_id = f"f{frame_index}"
        handles = await frame.query_selector_all(selector)
        states = await frame.evaluate(
            _COLLECT_ELEMENT_STATES_JS, [selector, _ELEMENT_STATE_JS]
        )
        frames.append(
            {
                "frame_id": frame_id,
                "url": frame.url,
                "is_main": frame is page.main_frame,
                "element_count": len(handles),
            }
        )
        requested_frame = semantic_filters.get("frame_ref")
        if requested_frame is not None and str(requested_frame) != frame_id:
            continue
        for handle, state in zip(handles, states, strict=False):
            if not bool(state["visible"]):
                continue
            if not _state_matches_filters(state, semantic_filters):
                continue
            total += 1
            if len(elements) >= limit:
                continue
            element_id = f"e{len(elements) + 1}"
            fingerprint = fingerprint_from_state(state, frame_id=frame_id)
            elements.append(
                BrowserElement(
                    element_id=element_id,
                    tag=str(state["tag"]),
                    control_type=_control_type(state),
                    role=str(state["role"]),
                    name=str(state["name"]),
                    label=str(state["label"]),
                    text=str(state["text"]),
                    value=state["value"] if state["value"] is None else str(state["value"]),
                    frame_id=frame_id,
                    visible=True,
                    enabled=bool(state["enabled"]),
                    editable=bool(state["editable"]),
                    required=bool(state["required"]),
                    readonly=bool(state["readonly"]),
                    checked=state["checked"],
                    selected=state["selected"],
                    expanded=state["expanded"],
                    obscured=bool(state["obscured"]),
                    options=tuple(dict(option) for option in state["options"]),
                    fingerprint=fingerprint,
                    handle=handle,
                    testid=str(state.get("testId", "")),
                    stable_id=str(state.get("stableId", "")),
                    compound=_compound_info(state),
                )
            )
    return elements, total, frames


def _selector_for_filters(filters: Mapping[str, object]) -> str:
    role = str(filters.get("role", "")).strip()
    if role in _CELL_ROLES:
        return '[role="cell"],[role="gridcell"]'
    if role == "option":
        return '[role="option"]'
    role_selectors = {
        "button": 'button,[role="button"]',
        "link": 'a[href],[role="link"]',
        "textbox": 'input:not([type=hidden]),textarea,[contenteditable=true],[role="textbox"]',
        "combobox": 'select,[role="combobox"]',
        "checkbox": 'input[type="checkbox"],[role="checkbox"]',
        "radio": 'input[type="radio"],[role="radio"]',
        "switch": '[role="switch"]',
        "tab": '[role="tab"]',
    }
    if role in role_selectors:
        return role_selectors[role]
    return INTERACTIVE_SELECTOR


def _state_matches_filters(state: Mapping[str, object], filters: Mapping[str, object]) -> bool:
    if filters.get("actionable_only") is True and (
        not bool(state.get("enabled")) or bool(state.get("obscured"))
    ):
        return False
    match = str(filters.get("match", "contains"))
    for attribute in ("role", "name", "label", "text", "testid"):
        query = filters.get(attribute)
        if isinstance(query, str) and query.strip():
            actual = str(state.get("testId" if attribute == "testid" else attribute, ""))
            if attribute == "role" and {actual, query.strip()} <= _CELL_ROLES:
                continue
            if attribute == "name" and str(state.get("role", "")) in _CELL_ROLES:
                if semantic_text_matches(str(state.get("text", "")), query.strip(), match):
                    continue
            if not semantic_text_matches(actual, query.strip(), match):
                return False
    return True


async def current_state(element: BrowserElement) -> dict[str, object]:
    return dict(await element.handle.evaluate(_ELEMENT_STATE_JS))


def fingerprint_from_state(state: Mapping[str, object], *, frame_id: str) -> tuple[str, ...]:
    return (
        str(state.get("tag", "")),
        str(state.get("role", "")),
        str(state.get("name", "")),
        str(state.get("label", "")),
        str(state.get("stableId", "")),
        str(state.get("testId", "")),
        str(state.get("browserAction", "")),
        str(state.get("type", "")),
        str(state.get("text", ""))[:120],
        frame_id,
    )


def filter_elements(
    elements: list[BrowserElement], filters: Mapping[str, object]
) -> list[BrowserElement]:
    selected = elements
    match = str(filters.get("match", "contains"))
    for attribute in ("role", "name", "label", "text", "testid"):
        query = filters.get(attribute)
        if isinstance(query, str) and query.strip():
            selected = [
                element
                for element in selected
                if _element_matches_filter(element, attribute, query, match)
            ]
    return selected


def _element_matches_filter(
    element: BrowserElement, attribute: str, query: str, match: str
) -> bool:
    actual = str(getattr(element, attribute))
    requested = query.strip()
    if attribute == "role" and {actual, requested} <= _CELL_ROLES:
        return True
    if attribute == "name" and element.role in _CELL_ROLES:
        if semantic_text_matches(element.text, requested, match):
            return True
    return _match_value(actual, requested, match)


def _match_value(actual: str, query: str, match: str) -> bool:
    return semantic_text_matches(actual, query, match)


def _control_type(state: Mapping[str, object]) -> str:
    role = str(state.get("role", ""))
    tag = str(state.get("tag", ""))
    input_type = str(state.get("type", ""))
    if role in {
        "checkbox",
        "switch",
        "radio",
        "button",
        "link",
        "tab",
        "combobox",
        "option",
    }:
        return role
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return input_type or "input"
    return "contenteditable" if bool(state.get("editable")) else role or tag


def _compound_info(state: Mapping[str, object]) -> dict[str, object]:
    """Expose bounded form metadata without returning secrets or arbitrary HTML."""

    tag = str(state.get("tag", ""))
    input_type = str(state.get("type", ""))
    options = state.get("options")
    info: dict[str, object] = {}
    if tag == "select" and isinstance(options, list):
        info.update(
            {
                "kind": "select",
                "multiple": bool(state.get("multiple", False)),
                "options_total": len(options),
                "options": options[:100],
            }
        )
    elif input_type in {"date", "time", "datetime-local", "month", "week"}:
        info.update(
            {
                "kind": input_type,
                "format": {
                    "date": "YYYY-MM-DD",
                    "time": "HH:mm or HH:mm:ss",
                    "datetime-local": "YYYY-MM-DDTHH:mm[:ss]",
                    "month": "YYYY-MM",
                    "week": "YYYY-Www",
                }[input_type],
                "min": str(state.get("min", "")),
                "max": str(state.get("max", "")),
                "step": str(state.get("step", "")),
            }
        )
    elif input_type == "file":
        info.update(
            {
                "kind": "file",
                "accept": str(state.get("accept", "")),
                "multiple": bool(state.get("multiple", False)),
            }
        )
    return info


__all__ = [
    "INTERACTIVE_SELECTOR",
    "collect_elements",
    "current_state",
    "filter_elements",
    "fingerprint_from_state",
]
