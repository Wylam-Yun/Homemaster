"""Live DOM inspection for the phase-one browser contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homemaster.browser.contracts import BrowserElement

INTERACTIVE_SELECTOR = ",".join(
    (
        "input:not([type=hidden])",
        "textarea",
        "select",
        "button",
        "a[href]",
        "[contenteditable=true]",
        "[role=button]",
        "[role=link]",
        "[role=textbox]",
        "[role=combobox]",
        "[role=checkbox]",
        "[role=switch]",
        "[role=radio]",
        "[role=tab]",
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
    const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2));
    const top = document.elementFromPoint(x, y);
    obscured = Boolean(top && top !== el && !el.contains(top) && !top.contains(el));
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
    stableId: el.id || '', testId: el.getAttribute('data-testid') || ''
  };
}
"""


async def collect_elements(
    page: Any, *, limit: int
) -> tuple[list[BrowserElement], int, list[dict[str, object]]]:
    elements: list[BrowserElement] = []
    total = 0
    frames: list[dict[str, object]] = []
    for frame_index, frame in enumerate(page.frames):
        frame_id = f"f{frame_index}"
        handles = await frame.query_selector_all(INTERACTIVE_SELECTOR)
        frames.append(
            {
                "frame_id": frame_id,
                "url": frame.url,
                "is_main": frame is page.main_frame,
                "element_count": len(handles),
            }
        )
        for handle in handles:
            state = await handle.evaluate(_ELEMENT_STATE_JS)
            if not bool(state["visible"]):
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
                )
            )
    return elements, total, frames


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
        frame_id,
    )


def filter_elements(
    elements: list[BrowserElement], filters: Mapping[str, object]
) -> list[BrowserElement]:
    selected = elements
    for attribute in ("role", "name", "label", "text"):
        query = filters.get(attribute)
        if isinstance(query, str) and query.strip():
            needle = query.strip().casefold()
            selected = [
                element
                for element in selected
                if needle in str(getattr(element, attribute)).casefold()
            ]
    return selected


def _control_type(state: Mapping[str, object]) -> str:
    role = str(state.get("role", ""))
    tag = str(state.get("tag", ""))
    input_type = str(state.get("type", ""))
    if role in {"checkbox", "switch", "radio", "button", "link", "tab", "combobox"}:
        return role
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return input_type or "input"
    return "contenteditable" if bool(state.get("editable")) else role or tag


__all__ = [
    "INTERACTIVE_SELECTOR",
    "collect_elements",
    "current_state",
    "filter_elements",
    "fingerprint_from_state",
]
