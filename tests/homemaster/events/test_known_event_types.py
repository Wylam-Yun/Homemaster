"""Keep ``KNOWN_EVENT_TYPES`` honest against real emission sites.

The list is documentation plus a contract surface for projections; it has
no runtime consumer, so it rots silently. Two directions are checked:

- Every literal event type emitted into the runtime event stream
  (emit-family first positional argument, ``event_type=`` keyword on
  emit-helpers, ``RuntimeEvent(type=...)`` constructions) must be listed.
- Every listed type must appear as a literal in ``src/`` outside
  ``runtime_events.py`` — a type no longer referenced anywhere is dead
  weight that lies about the contract surface.

Device-side ``event_store.append(event_type=...)`` uses a separate typed
audit store, not ``RuntimeEvent`` — emit-family detection keys on the
call name containing ``emit``, which deliberately excludes it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from homemaster.events.runtime_events import KNOWN_EVENT_TYPES

_SRC = Path(__file__).resolve().parents[3] / "src" / "homemaster"
_LIST_FILE = _SRC / "events" / "runtime_events.py"
_EVENT_TYPE_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_.]+$")

# Emit-named helpers whose payload strings are NOT runtime event types
# (e.g. audit-log action names). Extend only after manual classification.
_NON_RUNTIME_EMIT_NAMES = frozenset({"emit_feishu_audit", "_emit_feishu_audit"})


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _literal_strings(node: ast.expr) -> list[str]:
    """String constants that can be the event-type expression itself.

    Walks the literal-shape forms used for type expressions (ternaries,
    tuples, f-strings); ``Call``/``Dict``/``Compare`` payloads are not
    event types and are skipped so payload contents never register.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body) + _literal_strings(node.orelse)
    if isinstance(node, (ast.Tuple, ast.List)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_literal_strings(element))
        return out
    if isinstance(node, ast.JoinedStr):
        out = []
        for value in node.values:
            out.extend(_literal_strings(value))
        return out
    return []


def _emitted_types() -> dict[str, str]:
    """Map literal event type -> first source file that emits it."""

    emitted: dict[str, str] = {}
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name == "RuntimeEvent":
                for keyword in node.keywords:
                    if keyword.arg == "type":
                        for literal in _literal_strings(keyword.value):
                            emitted.setdefault(literal, str(path))
                continue
            if name in _NON_RUNTIME_EMIT_NAMES:
                continue
            if "emit" not in name.lower() and name != "publish_event":
                continue
            literals: list[str] = []
            if node.args:
                literals.extend(_literal_strings(node.args[0]))
            # ``emit_event(event_sink, event_type, ...)`` carries the type
            # in the second positional slot (see providers/_shared.py).
            if name == "emit_event" and len(node.args) > 1:
                literals.extend(_literal_strings(node.args[1]))
            for keyword in node.keywords:
                if keyword.arg == "event_type":
                    literals.extend(_literal_strings(keyword.value))
            for literal in literals:
                emitted.setdefault(literal, str(path))
    return emitted


def test_every_emitted_type_is_listed() -> None:
    emitted = _emitted_types()
    unclassified = {t: p for t, p in emitted.items() if not _EVENT_TYPE_RE.match(t)}
    assert not unclassified, (
        "Emit-site literals that are not namespaced event types "
        "(classify manually): " + repr(sorted(unclassified.items()))
    )
    missing = sorted(t for t in emitted if t not in KNOWN_EVENT_TYPES)
    assert not missing, (
        "Event types emitted but missing from KNOWN_EVENT_TYPES: "
        + repr([(t, emitted[t]) for t in missing])
    )


def test_every_listed_type_is_referenced_in_source() -> None:
    """A KNOWN type with zero source references beyond the list is dead."""

    bodies = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == _LIST_FILE:
            continue
        bodies.append(path.read_text())
    corpus = "\n".join(bodies)
    dead = sorted(
        event_type
        for event_type in KNOWN_EVENT_TYPES
        if f'"{event_type}"' not in corpus and f"'{event_type}'" not in corpus
    )
    assert not dead, "KNOWN_EVENT_TYPES entries never referenced in src: " + repr(dead)


__all__ = []
