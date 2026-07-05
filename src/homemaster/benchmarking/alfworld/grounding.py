"""Internal semantic grounding for ALFWorld tool arguments.

The tested model should not see ALFWorld admissible-command choices. This
module stays inside the harness: it maps the model's semantic target phrases
onto ALFWorld command labels before the command is sent to the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any

import httpx

from homemaster.providers.json_utils import extract_json_payload


@dataclass(frozen=True)
class GroundingCandidate:
    label: str
    kind: str
    source: str = ""


@dataclass(frozen=True)
class GroundingResult:
    value: str
    method: str
    matched_label: str | None = None
    kind: str | None = None


def canonical_command_name(name: str | None) -> str:
    """Convert ALFWorld CamelCase names to command type names."""

    if not isinstance(name, str):
        return ""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def normalized_key(value: str | None, *, drop_instance: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().casefold()
    text = _strip_articles(text)
    if drop_instance:
        text = _strip_trailing_instance(text)
    return "".join(ch for ch in text if ch.isalnum())


def build_grounding_candidates(
    *,
    state: Any,
    subtask: Any = None,
    extra_labels: list[GroundingCandidate] | None = None,
) -> list[GroundingCandidate]:
    candidates: list[GroundingCandidate] = []
    if subtask is not None:
        candidates.extend(_subtask_candidates(subtask))
    observation_parts = [
        getattr(state, "task", None),
        getattr(state, "observation", None),
        getattr(state, "last_feedback", None),
        getattr(state, "inventory", None),
    ]
    for text in observation_parts:
        if isinstance(text, str):
            candidates.extend(_observation_candidates(text))
    if extra_labels:
        candidates.extend(extra_labels)
    return _dedupe_candidates(candidates)


def ground_text(
    value: str,
    *,
    candidates: list[GroundingCandidate],
    allowed_kinds: set[str] | None = None,
    judge_config_path: Path | None = None,
) -> GroundingResult:
    scoped = _filter_candidates(candidates, allowed_kinds)
    deterministic = _deterministic_ground(value, scoped)
    if deterministic is not None:
        return deterministic
    judged = _judge_ground(value, scoped, judge_config_path)
    if judged is not None:
        return judged
    return GroundingResult(value=value.strip(), method="unchanged", matched_label=None)


def _subtask_candidates(subtask: Any) -> list[GroundingCandidate]:
    candidates: list[GroundingCandidate] = []
    obj = getattr(subtask, "object", None)
    parent = getattr(subtask, "parent", None)
    toggle = getattr(subtask, "toggle", None)
    mrecep = getattr(subtask, "mrecep", None)
    if obj:
        candidates.append(GroundingCandidate(canonical_command_name(obj), "object", "subtask"))
        candidates.append(GroundingCandidate(str(obj), "object", "subtask"))
    if parent:
        candidates.append(GroundingCandidate(canonical_command_name(parent), "receptacle", "subtask"))
        candidates.append(GroundingCandidate(str(parent), "receptacle", "subtask"))
    if toggle:
        candidates.append(GroundingCandidate(canonical_command_name(toggle), "toggle", "subtask"))
        candidates.append(GroundingCandidate(str(toggle), "toggle", "subtask"))
    if mrecep:
        candidates.append(GroundingCandidate(canonical_command_name(mrecep), "object", "subtask"))
        candidates.append(GroundingCandidate(str(mrecep), "object", "subtask"))
    for label in ("microwave", "fridge", "sinkbasin", "sink", "bathtubbasin"):
        candidates.append(GroundingCandidate(label, "receptacle", "built_in_tool"))
    return candidates


def _observation_candidates(text: str) -> list[GroundingCandidate]:
    candidates: list[GroundingCandidate] = []
    for match in re.finditer(r"\b(?:On|In)\s+(?:the|it)\s+([^,.]+)", text, re.IGNORECASE):
        label = match.group(1).strip()
        if label and label.casefold() != "it":
            candidates.append(GroundingCandidate(label, "receptacle", "observation"))
    carrying = re.search(r"You are carrying:\s*(?:a|an|the)?\s*([^.\n]+)", text, re.IGNORECASE)
    if carrying:
        candidates.append(GroundingCandidate(carrying.group(1).strip(), "object", "inventory"))
    for match in re.finditer(r"\byou see\s+([^.\n]+)", text, re.IGNORECASE):
        tail = match.group(1).split("Your task is to:", 1)[0]
        tail = tail.replace(", and ", ", ").replace(" and ", ", ")
        for item in tail.split(","):
            label = _strip_articles(item.strip().strip("."))
            if label and label.casefold() != "nothing":
                candidates.append(GroundingCandidate(label, "object", "observation"))
                candidates.append(GroundingCandidate(label, "receptacle", "observation"))
    return candidates


def _dedupe_candidates(candidates: list[GroundingCandidate]) -> list[GroundingCandidate]:
    deduped: list[GroundingCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        label = candidate.label.strip()
        if not label:
            continue
        key = (normalized_key(label), candidate.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(GroundingCandidate(label, candidate.kind, candidate.source))
    return deduped


def _filter_candidates(
    candidates: list[GroundingCandidate],
    allowed_kinds: set[str] | None,
) -> list[GroundingCandidate]:
    if allowed_kinds is None:
        return candidates
    allowed = set(allowed_kinds) | {"any"}
    return [candidate for candidate in candidates if candidate.kind in allowed]


def _deterministic_ground(
    value: str,
    candidates: list[GroundingCandidate],
) -> GroundingResult | None:
    raw = value.strip()
    if not raw:
        return None
    full_key = normalized_key(raw)
    base_key = normalized_key(raw, drop_instance=True)
    raw_has_instance = _has_trailing_instance(raw)
    base_matches = _unique_labels(
        candidate for candidate in candidates
        if normalized_key(candidate.label, drop_instance=True) == base_key
    )
    if not raw_has_instance:
        instance_matches = [candidate for candidate in base_matches if _has_trailing_instance(candidate.label)]
        if len(instance_matches) == 1:
            candidate = instance_matches[0]
            return GroundingResult(
                value=candidate.label,
                method="unique_instance",
                matched_label=candidate.label,
                kind=candidate.kind,
            )

    exact = _unique_by_label(
        candidate for candidate in candidates
        if normalized_key(candidate.label) == full_key
    )
    if exact is not None:
        return GroundingResult(
            value=exact.label,
            method="exact",
            matched_label=exact.label,
            kind=exact.kind,
        )

    if len(base_matches) == 1:
        candidate = base_matches[0]
        if raw_has_instance and not _can_drop_input_instance(candidate):
            return None
        return GroundingResult(
            value=candidate.label,
            method="normalized",
            matched_label=candidate.label,
            kind=candidate.kind,
        )

    suffix_matches = _unique_labels(
        candidate for candidate in candidates
        if base_key and normalized_key(candidate.label, drop_instance=True).endswith(base_key)
    )
    if len(suffix_matches) == 1:
        candidate = suffix_matches[0]
        if raw_has_instance and not _can_drop_input_instance(candidate):
            return None
        return GroundingResult(
            value=candidate.label,
            method="unique_suffix",
            matched_label=candidate.label,
            kind=candidate.kind,
        )
    return None


def _judge_ground(
    value: str,
    candidates: list[GroundingCandidate],
    judge_config_path: Path | None,
) -> GroundingResult | None:
    semantic_candidates = _semantic_judge_candidates(candidates)
    if judge_config_path is None or not judge_config_path.exists() or not semantic_candidates:
        return None
    config = _load_judge_config(judge_config_path)
    if config is None:
        return None
    matches: list[GroundingCandidate] = []
    for candidate in _unique_labels(semantic_candidates):
        if _judge_same_semantic_target(value, candidate.label, config):
            matches.append(candidate)
    candidate = _select_semantic_match(matches)
    if candidate is None:
        return None
    return GroundingResult(
        value=candidate.label,
        method="semantic_judge",
        matched_label=candidate.label,
        kind=candidate.kind,
    )


def _semantic_judge_candidates(candidates: list[GroundingCandidate]) -> list[GroundingCandidate]:
    trusted_sources = {"subtask", "subtask_toggle", "built_in_tool"}
    return [candidate for candidate in candidates if candidate.source in trusted_sources]


def _select_semantic_match(matches: list[GroundingCandidate]) -> GroundingCandidate | None:
    if not matches:
        return None
    base_keys = {normalized_key(candidate.label, drop_instance=True) for candidate in matches}
    if len(base_keys) != 1:
        return None
    instance_matches = [
        candidate for candidate in matches if _has_trailing_instance(candidate.label)
    ]
    if len(instance_matches) == 1:
        return instance_matches[0]
    if len(instance_matches) > 1:
        return None
    return matches[0]


def _can_drop_input_instance(candidate: GroundingCandidate) -> bool:
    """Allow instance-stripping only for non-executable virtual task targets."""

    return candidate.kind == "toggle" and candidate.source in {"subtask", "subtask_toggle"}


def _judge_same_semantic_target(
    value: str,
    canonical_label: str,
    config: dict[str, Any],
) -> bool:
    prompt = (
        "Decide whether two ALFWorld object/receptacle phrases refer to the "
        "same semantic target. Do not choose or rewrite either phrase. Return "
        "only JSON with keys: match (boolean), confidence (0..1).\n"
        f"model_phrase: {json.dumps(value, ensure_ascii=False)}\n"
        f"ground_truth_label: {json.dumps(canonical_label, ensure_ascii=False)}"
    )
    try:
        response = httpx.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": config["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": float(config.get("temperature", 0)),
                "max_tokens": int(config.get("max_tokens", 32)),
            },
            timeout=float(config.get("timeout_s", 30)),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        payload = extract_json_payload(str(content))
    except Exception:
        return False
    if not payload.get("match"):
        return False
    confidence = payload.get("confidence")
    threshold = float(config.get("min_confidence", 0.5))
    if isinstance(confidence, int | float) and confidence < threshold:
        return False
    return True


def _load_judge_config(path: Path) -> dict[str, Any] | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    section = payload.get("semantic_judge") if isinstance(payload, dict) else None
    if not isinstance(section, dict):
        return None
    required = ("base_url", "api_key", "model")
    if not all(isinstance(section.get(key), str) and section[key].strip() for key in required):
        return None
    return dict(section)


def _unique_by_label(candidates: Any) -> GroundingCandidate | None:
    labels = _unique_labels(candidates)
    return labels[0] if len(labels) == 1 else None


def _unique_labels(candidates: Any) -> list[GroundingCandidate]:
    result: list[GroundingCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalized_key(candidate.label)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _strip_articles(text: str) -> str:
    return re.sub(r"^(?:a|an|the)\s+", "", text.strip(), flags=re.IGNORECASE)


def _strip_trailing_instance(text: str) -> str:
    return re.sub(r"[\s#_-]*\d+\s*$", "", text.strip())


def _has_trailing_instance(text: str) -> bool:
    return bool(re.search(r"\d+\s*$", text.strip()))
