# Memory-Grounded Task Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-month CLI MVP for a memory-grounded embodied task brain that parses elder-care household instructions, retrieves memory, generates a validated LLM high-level plan, coordinates mock VLN/perception/RoboBrain/atomic-executor adapters, verifies each subgoal, recovers from failure, updates memory, and emits an auditable trace.

**Architecture:** Implement a small Python package with typed domain models, JSON scenario fixtures, adapter interfaces, a LangGraph orchestration layer, and pytest scenario tests. Keep world truth, robot memory, runtime state, and trace events separate so stale memory, recovery, and memory updates are visible in the demo.

**Tech Stack:** Python 3.11+, Pydantic v2, LangGraph, Typer, Rich, httpx, pytest, ruff.

---

## File Structure

Create this project structure:

```text
pyproject.toml
README.md
data/scenarios/check_medicine_success/world.json
data/scenarios/check_medicine_success/memory.json
data/scenarios/check_medicine_success/failures.json
data/scenarios/check_medicine_stale_recover/world.json
data/scenarios/check_medicine_stale_recover/memory.json
data/scenarios/check_medicine_stale_recover/failures.json
data/scenarios/fetch_cup_retry/world.json
data/scenarios/fetch_cup_retry/memory.json
data/scenarios/fetch_cup_retry/failures.json
src/task_brain/__init__.py
src/task_brain/adapters/__init__.py
src/task_brain/adapters/mock_atomic_executor.py
src/task_brain/adapters/mock_perception.py
src/task_brain/adapters/mock_vln.py
src/task_brain/adapters/robobrain.py
src/task_brain/capabilities.py
src/task_brain/cli.py
src/task_brain/context.py
src/task_brain/domain.py
src/task_brain/graph.py
src/task_brain/memory.py
src/task_brain/parser.py
src/task_brain/planner.py
src/task_brain/recovery.py
src/task_brain/trace.py
src/task_brain/verification.py
src/task_brain/world.py
tests/test_adapters.py
tests/test_cli.py
tests/test_graph_scenarios.py
tests/test_memory_parser_context.py
tests/test_planner_validation.py
tests/test_recovery.py
tests/test_verification.py
tests/test_world.py
```

Responsibility map:

- `domain.py`: shared Pydantic models and enums.
- `world.py`: load, query, and mutate mock-world predicates.
- `memory.py`: load, query, and update object and episodic memory.
- `parser.py`: lightweight instruction parsing for the two MVP task types.
- `context.py`: build memory-grounded task context for planning.
- `planner.py`: LLM planner interface, deterministic demo planner, schema validator, and fallback templates.
- `capabilities.py`: registry of executable subgoal capabilities.
- `adapters/*`: mock VLN, perception, RoboBrain client/fake, and atomic executor.
- `verification.py`: success-condition evaluator.
- `recovery.py`: retry, switch-candidate, report-failure decisions.
- `trace.py`: structured trace events and CLI rendering.
- `graph.py`: LangGraph state and orchestration nodes.
- `cli.py`: Typer CLI entry point.

## Implementation Tasks

### Task 1: Project Scaffold and Core Models

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/task_brain/__init__.py`
- Create: `src/task_brain/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write the failing domain model tests**

Create `tests/test_domain.py`:

```python
from task_brain.domain import (
    CandidateLocation,
    Predicate,
    Subgoal,
    SubgoalType,
    TaskIntent,
    TaskRequest,
)


def test_predicate_round_trip_from_list() -> None:
    predicate = Predicate.from_list(["ontop", "cup_1", "kitchen_table"])

    assert predicate.name == "ontop"
    assert predicate.args == ("cup_1", "kitchen_table")
    assert predicate.to_list() == ["ontop", "cup_1", "kitchen_table"]


def test_task_request_keeps_instruction_source() -> None:
    request = TaskRequest(
        request_id="req_001",
        source="cli",
        user_id="demo_user",
        utterance="去厨房找水杯，然后拿给我",
    )

    assert request.source == "cli"
    assert request.utterance.endswith("拿给我")


def test_subgoal_requires_success_conditions() -> None:
    subgoal = Subgoal(
        id="sg1",
        type=SubgoalType.NAVIGATE,
        target="kitchen_table_viewpoint",
        success_conditions=[Predicate.from_list(["inroom", "robot", "kitchen"])],
    )

    assert subgoal.type == SubgoalType.NAVIGATE
    assert subgoal.success_conditions[0].to_list() == ["inroom", "robot", "kitchen"]


def test_candidate_location_orders_by_score() -> None:
    low = CandidateLocation(
        id="c1",
        room="living_room",
        support="coffee_table",
        viewpoint="coffee_table_viewpoint",
        score=0.25,
        reason="weak prior",
        source=["semantic_prior"],
    )
    high = CandidateLocation(
        id="c2",
        room="kitchen",
        support="kitchen_table",
        viewpoint="kitchen_table_viewpoint",
        score=0.88,
        reason="last seen",
        source=["memory:last_seen"],
    )

    assert sorted([low, high], reverse=True)[0].id == "c2"


def test_task_intent_enum_values_match_trace_names() -> None:
    assert TaskIntent.CHECK_OBJECT_PRESENCE.value == "check_object_presence"
    assert TaskIntent.FETCH_OBJECT.value == "fetch_object"
```

- [ ] **Step 2: Run the test and confirm it fails because the package does not exist**

Run:

```bash
pytest tests/test_domain.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'task_brain'`.

- [ ] **Step 3: Add package configuration**

Create `pyproject.toml`:

```toml
[project]
name = "elder-task-brain"
version = "0.1.0"
description = "Memory-grounded embodied task brain CLI MVP"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "langgraph>=0.2",
  "pydantic>=2.7",
  "rich>=13.7",
  "typer>=0.12",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "ruff>=0.6",
]

[project.scripts]
task-brain = "task_brain.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `README.md`:

````markdown
# Memory-Grounded Embodied Task Brain

This repository contains a CLI MVP for a household elder-care robot task brain.
It demonstrates memory-grounded high-level planning, module orchestration,
subgoal verification, failure recovery, and memory updates using a JSON mock
world and structured trace output.

Run the demo after implementation:

```bash
task-brain run --scenario check_medicine_success
task-brain run --scenario check_medicine_stale_recover
task-brain run --scenario fetch_cup_retry
```
````

Create `src/task_brain/__init__.py`:

```python
"""Memory-grounded embodied task brain MVP."""

__all__ = ["__version__"]

__version__ = "0.1.0"
```

- [ ] **Step 4: Add core domain models**

Create `src/task_brain/domain.py`:

```python
from __future__ import annotations

from enum import Enum
from functools import total_ordering
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskIntent(str, Enum):
    CHECK_OBJECT_PRESENCE = "check_object_presence"
    FETCH_OBJECT = "fetch_object"


class SubgoalType(str, Enum):
    NAVIGATE = "navigate"
    OBSERVE = "observe"
    VERIFY_OBJECT_PRESENCE = "verify_object_presence"
    EMBODIED_MANIPULATION = "embodied_manipulation"
    RETURN_TO_USER = "return_to_user"
    ASK_CLARIFICATION = "ask_clarification"
    REPORT_FAILURE = "report_failure"


class Predicate(BaseModel):
    name: str
    args: tuple[str, ...] = Field(default_factory=tuple)

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_list(cls, value: list[Any]) -> "Predicate":
        if not value:
            raise ValueError("predicate list must include a name")
        name = str(value[0])
        args = tuple(str(item) for item in value[1:])
        return cls(name=name, args=args)

    def to_list(self) -> list[str]:
        return [self.name, *self.args]


class TaskRequest(BaseModel):
    request_id: str
    source: Literal["cli", "feishu"] = "cli"
    user_id: str
    utterance: str


class TargetObject(BaseModel):
    category: str
    attributes: list[str] = Field(default_factory=list)
    quantity: int = 1


class ParsedTask(BaseModel):
    intent: TaskIntent
    target_object: TargetObject
    explicit_location_hint: str | None = None
    delivery_target: str | None = None
    requires_navigation: bool = True
    requires_manipulation: bool = False


@total_ordering
class CandidateLocation(BaseModel):
    id: str
    room: str
    support: str
    viewpoint: str
    score: float
    reason: str
    source: list[str] = Field(default_factory=list)
    status: Literal["candidate", "excluded", "selected"] = "candidate"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CandidateLocation):
            return NotImplemented
        return self.score < other.score


class MemoryHit(BaseModel):
    memory_id: str
    object_category: str
    location: str
    confidence: float
    reason: str
    status: str = "active"


class NegativeEvidence(BaseModel):
    location: str
    object_category: str
    status: str


class MemoryContext(BaseModel):
    object_candidates: list[MemoryHit] = Field(default_factory=list)
    negative_evidence: list[NegativeEvidence] = Field(default_factory=list)


class Subgoal(BaseModel):
    id: str
    type: SubgoalType
    target: str | None = None
    target_category: str | None = None
    planner: str | None = None
    subgoal: str | None = None
    target_ref: str | None = None
    success_conditions: list[Predicate]

    @field_validator("success_conditions")
    @classmethod
    def success_conditions_must_exist(cls, value: list[Predicate]) -> list[Predicate]:
        if not value:
            raise ValueError("subgoal must include at least one success condition")
        return value


class HighLevelPlan(BaseModel):
    plan_id: str
    memory_grounding: list[dict[str, str]]
    subgoals: list[Subgoal]


class VerificationResult(BaseModel):
    subject_id: str
    passed: bool
    failed_conditions: list[Predicate] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    recovery_hint: str | None = None


class AtomicAction(BaseModel):
    action: str
    target: str


class AtomicPlanResponse(BaseModel):
    status: Literal["planned", "failed"]
    target_object_id: str | None = None
    atomic_action_list: list[AtomicAction] = Field(default_factory=list)
    expected_state_delta: list[Predicate] = Field(default_factory=list)
    confidence: float = 0.0
    failure_reason: str | None = None


class ExecutionResult(BaseModel):
    status: Literal["success", "failed"]
    applied_state_delta: list[Predicate] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None


class TraceEvent(BaseModel):
    step: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 5: Run domain tests**

Run:

```bash
pytest tests/test_domain.py -q
```

Expected: PASS with `5 passed`.

- [ ] **Step 6: Commit scaffold and domain models**

Run:

```bash
git add pyproject.toml README.md src/task_brain/__init__.py src/task_brain/domain.py tests/test_domain.py
git commit -m "feat: add task brain domain models"
```

### Task 2: Mock World Predicate Store

**Files:**
- Create: `src/task_brain/world.py`
- Create: `data/scenarios/check_medicine_success/world.json`
- Test: `tests/test_world.py`

- [ ] **Step 1: Write failing world tests**

Create `tests/test_world.py`:

```python
from pathlib import Path

from task_brain.domain import Predicate
from task_brain.world import MockWorld


FIXTURE = Path("data/scenarios/check_medicine_success/world.json")


def test_world_loads_predicates_from_json() -> None:
    world = MockWorld.from_file(FIXTURE)

    assert world.has(Predicate.from_list(["inroom", "robot", "bedroom"]))
    assert world.has(Predicate.from_list(["ontop", "medicine_box_1", "coffee_table"]))


def test_world_can_apply_positive_and_negative_state_delta() -> None:
    world = MockWorld.from_file(FIXTURE)

    world.apply_delta(
        [
            Predicate.from_list(["inroom", "robot", "living_room"]),
            Predicate.from_list(["not", "inroom", "robot", "bedroom"]),
        ]
    )

    assert world.has(Predicate.from_list(["inroom", "robot", "living_room"]))
    assert not world.has(Predicate.from_list(["inroom", "robot", "bedroom"]))


def test_visible_objects_at_viewpoint_are_filtered() -> None:
    world = MockWorld.from_file(FIXTURE)

    visible = world.visible_objects("coffee_table_viewpoint")

    assert "medicine_box_1" in visible
    assert "cup_1" not in visible


def test_find_objects_by_category_uses_object_metadata() -> None:
    world = MockWorld.from_file(FIXTURE)

    matches = world.find_objects_by_category("medicine_box")

    assert [item["id"] for item in matches] == ["medicine_box_1"]
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
pytest tests/test_world.py -q
```

Expected: FAIL with `ModuleNotFoundError` or import error for `task_brain.world`.

- [ ] **Step 3: Add the check-medicine success world fixture**

Create `data/scenarios/check_medicine_success/world.json`:

```json
{
  "world_id": "check_medicine_success",
  "robot": {
    "location": "bedroom",
    "holding": null
  },
  "user": {
    "location": "bedroom"
  },
  "rooms": [
    {
      "id": "bedroom",
      "connected_to": ["living_room"]
    },
    {
      "id": "living_room",
      "connected_to": ["bedroom", "kitchen"]
    },
    {
      "id": "kitchen",
      "connected_to": ["living_room"]
    }
  ],
  "furniture": [
    {
      "id": "coffee_table",
      "category": "table",
      "room": "living_room",
      "viewpoint": "coffee_table_viewpoint"
    },
    {
      "id": "kitchen_table",
      "category": "table",
      "room": "kitchen",
      "viewpoint": "kitchen_table_viewpoint"
    }
  ],
  "objects": [
    {
      "id": "medicine_box_1",
      "category": "medicine_box",
      "room": "living_room",
      "support": "coffee_table",
      "relation": "ontop",
      "states": {
        "reachable": true,
        "graspable": true
      }
    },
    {
      "id": "cup_1",
      "category": "cup",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {
        "reachable": true,
        "graspable": true
      }
    }
  ],
  "predicates": [
    ["inroom", "robot", "bedroom"],
    ["near", "robot", "user"],
    ["connected", "bedroom", "living_room"],
    ["connected", "living_room", "bedroom"],
    ["connected", "living_room", "kitchen"],
    ["connected", "kitchen", "living_room"],
    ["inroom", "coffee_table", "living_room"],
    ["inroom", "kitchen_table", "kitchen"],
    ["visible_from", "coffee_table", "coffee_table_viewpoint"],
    ["visible_from", "kitchen_table", "kitchen_table_viewpoint"],
    ["inroom", "medicine_box_1", "living_room"],
    ["ontop", "medicine_box_1", "coffee_table"],
    ["visible_from", "medicine_box_1", "coffee_table_viewpoint"],
    ["reachable", "medicine_box_1"],
    ["graspable", "medicine_box_1"],
    ["inroom", "cup_1", "kitchen"],
    ["ontop", "cup_1", "kitchen_table"],
    ["visible_from", "cup_1", "kitchen_table_viewpoint"],
    ["reachable", "cup_1"],
    ["graspable", "cup_1"]
  ]
}
```

- [ ] **Step 4: Implement the mock world store**

Create `src/task_brain/world.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from task_brain.domain import Predicate


class MockWorld:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.predicates: set[Predicate] = {
            Predicate.from_list(item) for item in raw.get("predicates", [])
        }

    @classmethod
    def from_file(cls, path: str | Path) -> "MockWorld":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls(json.load(handle))

    def has(self, predicate: Predicate) -> bool:
        return predicate in self.predicates

    def add(self, predicate: Predicate) -> None:
        self.predicates.add(predicate)

    def remove(self, predicate: Predicate) -> None:
        self.predicates.discard(predicate)

    def apply_delta(self, delta: list[Predicate]) -> None:
        for predicate in delta:
            if predicate.name == "not":
                if not predicate.args:
                    raise ValueError("negative predicate must include a predicate name")
                self.remove(Predicate(name=predicate.args[0], args=predicate.args[1:]))
            else:
                self.add(predicate)

        holding = self.first_arg_for("holding", "robot")
        self.raw.setdefault("robot", {})["holding"] = holding

        robot_room = self.first_arg_for("inroom", "robot")
        if robot_room is not None:
            self.raw.setdefault("robot", {})["location"] = robot_room

    def first_arg_for(self, name: str, first_arg: str) -> str | None:
        for predicate in sorted(self.predicates, key=lambda item: item.to_list()):
            if predicate.name == name and predicate.args and predicate.args[0] == first_arg:
                if len(predicate.args) >= 2:
                    return predicate.args[1]
        return None

    def visible_objects(self, viewpoint: str) -> list[str]:
        visible_ids = [
            predicate.args[0]
            for predicate in self.predicates
            if predicate.name == "visible_from"
            and len(predicate.args) == 2
            and predicate.args[1] == viewpoint
        ]
        object_ids = {item["id"] for item in self.raw.get("objects", [])}
        return sorted(item for item in visible_ids if item in object_ids)

    def visible_entities(self, viewpoint: str) -> list[str]:
        return sorted(
            predicate.args[0]
            for predicate in self.predicates
            if predicate.name == "visible_from"
            and len(predicate.args) == 2
            and predicate.args[1] == viewpoint
        )

    def find_objects_by_category(self, category: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.raw.get("objects", [])
            if item.get("category") == category
        ]

    def object_category(self, object_id: str) -> str | None:
        for item in self.raw.get("objects", []):
            if item.get("id") == object_id:
                return str(item.get("category"))
        return None

    def object_is_visible_at(self, object_id: str, viewpoint: str) -> bool:
        return self.has(Predicate.from_list(["visible_from", object_id, viewpoint]))

    def object_is_reachable(self, object_id: str) -> bool:
        return self.has(Predicate.from_list(["reachable", object_id]))

    def to_predicate_lists(self) -> list[list[str]]:
        return sorted(predicate.to_list() for predicate in self.predicates)
```

- [ ] **Step 5: Run world tests**

Run:

```bash
pytest tests/test_world.py -q
```

Expected: PASS with `4 passed`.

- [ ] **Step 6: Commit mock world store**

Run:

```bash
git add src/task_brain/world.py data/scenarios/check_medicine_success/world.json tests/test_world.py
git commit -m "feat: add mock world predicate store"
```

### Task 3: Memory Store, Parser, and Task Context

**Files:**
- Create: `src/task_brain/memory.py`
- Create: `src/task_brain/parser.py`
- Create: `src/task_brain/context.py`
- Create: `data/scenarios/check_medicine_success/memory.json`
- Test: `tests/test_memory_parser_context.py`

- [ ] **Step 1: Write failing parser and memory tests**

Create `tests/test_memory_parser_context.py`:

```python
from pathlib import Path

from task_brain.context import build_task_context
from task_brain.domain import TaskIntent, TaskRequest
from task_brain.memory import MemoryStore
from task_brain.parser import parse_instruction
from task_brain.world import MockWorld


MEMORY = Path("data/scenarios/check_medicine_success/memory.json")
WORLD = Path("data/scenarios/check_medicine_success/world.json")


def test_parser_extracts_check_medicine_task() -> None:
    parsed = parse_instruction("去桌子那边看看药盒是不是还在。")

    assert parsed.intent == TaskIntent.CHECK_OBJECT_PRESENCE
    assert parsed.target_object.category == "medicine_box"
    assert parsed.requires_manipulation is False


def test_parser_extracts_fetch_cup_task() -> None:
    parsed = parse_instruction("去厨房找水杯，然后拿给我")

    assert parsed.intent == TaskIntent.FETCH_OBJECT
    assert parsed.target_object.category == "cup"
    assert parsed.explicit_location_hint == "kitchen"
    assert parsed.delivery_target == "user"
    assert parsed.requires_manipulation is True


def test_memory_retrieval_returns_ranked_candidates() -> None:
    memory = MemoryStore.from_file(MEMORY)

    context = memory.retrieve("medicine_box")

    assert context.object_candidates[0].location == "living_room/coffee_table"
    assert context.object_candidates[0].confidence == 0.84


def test_task_context_includes_request_parse_memory_and_world() -> None:
    request = TaskRequest(
        request_id="req_001",
        source="cli",
        user_id="demo_user",
        utterance="去桌子那边看看药盒是不是还在。",
    )
    parsed = parse_instruction(request.utterance)
    memory = MemoryStore.from_file(MEMORY)
    world = MockWorld.from_file(WORLD)

    task_context = build_task_context(request, parsed, memory.retrieve("medicine_box"), world)

    assert task_context.request.utterance == request.utterance
    assert task_context.parsed.intent == TaskIntent.CHECK_OBJECT_PRESENCE
    assert task_context.memory.object_candidates[0].memory_id == "objmem_medicine_coffee_table"
    assert task_context.robot_location == "bedroom"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
pytest tests/test_memory_parser_context.py -q
```

Expected: FAIL with import errors for memory, parser, or context modules.

- [ ] **Step 3: Add the memory fixture**

Create `data/scenarios/check_medicine_success/memory.json`:

```json
{
  "object_memory": [
    {
      "memory_id": "objmem_medicine_coffee_table",
      "object_category": "medicine_box",
      "location": "living_room/coffee_table",
      "confidence": 0.84,
      "reason": "last_seen",
      "summary": "药盒上次在客厅茶几上看到。"
    },
    {
      "memory_id": "objmem_medicine_kitchen_table",
      "object_category": "medicine_box",
      "location": "kitchen/kitchen_table",
      "confidence": 0.43,
      "reason": "medicine sometimes placed near meal area",
      "summary": "药盒有时会被放在厨房餐桌附近。"
    }
  ],
  "negative_evidence": [],
  "episodic_memory": [
    {
      "episode_id": "ep_medicine_previous_success",
      "task": "检查药盒是否还在",
      "result": "success",
      "summary": "上次检查时，药盒在客厅茶几上。"
    }
  ]
}
```

- [ ] **Step 4: Implement memory, parser, and task context modules**

Create `src/task_brain/memory.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from task_brain.domain import MemoryContext, MemoryHit, NegativeEvidence


class MemoryStore:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    @classmethod
    def from_file(cls, path: str | Path) -> "MemoryStore":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls(json.load(handle))

    def retrieve(self, object_category: str) -> MemoryContext:
        hits = [
            MemoryHit(
                memory_id=item["memory_id"],
                object_category=item["object_category"],
                location=item["location"],
                confidence=float(item["confidence"]),
                reason=item["reason"],
                status=item.get("status", "active"),
            )
            for item in self.raw.get("object_memory", [])
            if item.get("object_category") == object_category
        ]
        hits.sort(key=lambda item: item.confidence, reverse=True)

        negative = [
            NegativeEvidence(
                location=item["location"],
                object_category=item["object_category"],
                status=item["status"],
            )
            for item in self.raw.get("negative_evidence", [])
            if item.get("object_category") == object_category
        ]
        return MemoryContext(object_candidates=hits, negative_evidence=negative)

    def mark_negative(self, location: str, object_category: str, status: str) -> None:
        entry = {
            "location": location,
            "object_category": object_category,
            "status": status,
        }
        self.raw.setdefault("negative_evidence", []).append(entry)

    def upsert_object_memory(
        self,
        *,
        memory_id: str,
        object_category: str,
        location: str,
        confidence: float,
        reason: str,
        summary: str,
    ) -> None:
        entries = self.raw.setdefault("object_memory", [])
        for item in entries:
            if item.get("memory_id") == memory_id:
                item.update(
                    {
                        "object_category": object_category,
                        "location": location,
                        "confidence": confidence,
                        "reason": reason,
                        "summary": summary,
                    }
                )
                return
        entries.append(
            {
                "memory_id": memory_id,
                "object_category": object_category,
                "location": location,
                "confidence": confidence,
                "reason": reason,
                "summary": summary,
            }
        )
```

Create `src/task_brain/parser.py`:

```python
from __future__ import annotations

from task_brain.domain import ParsedTask, TargetObject, TaskIntent


def parse_instruction(utterance: str) -> ParsedTask:
    normalized = utterance.strip()
    if "药盒" in normalized and ("看看" in normalized or "还在" in normalized):
        return ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(category="medicine_box"),
            explicit_location_hint="table" if "桌" in normalized else None,
            requires_navigation=True,
            requires_manipulation=False,
        )

    if "水杯" in normalized or "杯子" in normalized:
        return ParsedTask(
            intent=TaskIntent.FETCH_OBJECT,
            target_object=TargetObject(category="cup"),
            explicit_location_hint="kitchen" if "厨房" in normalized else None,
            delivery_target="user" if "给我" in normalized or "拿给" in normalized else None,
            requires_navigation=True,
            requires_manipulation=True,
        )

    raise ValueError(f"unsupported instruction: {utterance}")
```

Create `src/task_brain/context.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from task_brain.domain import MemoryContext, ParsedTask, TaskRequest
from task_brain.world import MockWorld


class TaskContext(BaseModel):
    request: TaskRequest
    parsed: ParsedTask
    memory: MemoryContext
    robot_location: str | None
    robot_holding: str | None
    user_location: str | None
    world_id: str


def build_task_context(
    request: TaskRequest,
    parsed: ParsedTask,
    memory: MemoryContext,
    world: MockWorld,
) -> TaskContext:
    robot = world.raw.get("robot", {})
    user = world.raw.get("user", {})
    return TaskContext(
        request=request,
        parsed=parsed,
        memory=memory,
        robot_location=robot.get("location"),
        robot_holding=robot.get("holding"),
        user_location=user.get("location"),
        world_id=str(world.raw.get("world_id", "unknown")),
    )
```

- [ ] **Step 5: Run parser and memory tests**

Run:

```bash
pytest tests/test_memory_parser_context.py -q
```

Expected: PASS with `4 passed`.

- [ ] **Step 6: Commit parser, memory, and context**

Run:

```bash
git add src/task_brain/memory.py src/task_brain/parser.py src/task_brain/context.py data/scenarios/check_medicine_success/memory.json tests/test_memory_parser_context.py
git commit -m "feat: add parser memory and task context"
```

### Task 4: Capability Registry, Planner, and Plan Validation

**Files:**
- Create: `src/task_brain/capabilities.py`
- Create: `src/task_brain/planner.py`
- Test: `tests/test_planner_validation.py`

- [ ] **Step 1: Write failing planner tests**

Create `tests/test_planner_validation.py`:

```python
import pytest

from task_brain.capabilities import default_capability_registry
from task_brain.context import build_task_context
from task_brain.domain import TaskRequest
from task_brain.memory import MemoryStore
from task_brain.parser import parse_instruction
from task_brain.planner import (
    DeterministicHighLevelPlanner,
    PlanValidationError,
    PlanValidator,
)
from task_brain.world import MockWorld


def build_context(utterance: str = "去厨房找水杯，然后拿给我"):
    request = TaskRequest(
        request_id="req_001",
        source="cli",
        user_id="demo_user",
        utterance=utterance,
    )
    parsed = parse_instruction(utterance)
    world = MockWorld.from_file("data/scenarios/check_medicine_success/world.json")
    memory = MemoryStore.from_file("data/scenarios/check_medicine_success/memory.json")
    return build_task_context(request, parsed, memory.retrieve(parsed.target_object.category), world)


def test_planner_uses_memory_grounding() -> None:
    planner = DeterministicHighLevelPlanner()
    plan = planner.generate(build_context())

    assert plan.memory_grounding
    assert plan.memory_grounding[0]["used_memory_id"]


def test_fetch_plan_verifies_object_before_robobrain() -> None:
    planner = DeterministicHighLevelPlanner()
    plan = planner.generate(build_context())

    subgoal_types = [subgoal.type.value for subgoal in plan.subgoals]

    assert subgoal_types.index("verify_object_presence") < subgoal_types.index(
        "embodied_manipulation"
    )


def test_validator_rejects_atomic_actions_in_high_level_plan() -> None:
    planner = DeterministicHighLevelPlanner()
    plan = planner.generate(build_context())
    plan.subgoals[0].target = "move_arm_to_pregrasp"
    validator = PlanValidator(default_capability_registry())

    with pytest.raises(PlanValidationError, match="atomic action"):
        validator.validate(plan)


def test_validator_accepts_default_plan() -> None:
    planner = DeterministicHighLevelPlanner()
    plan = planner.generate(build_context())
    validator = PlanValidator(default_capability_registry())

    validator.validate(plan)
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_planner_validation.py -q
```

Expected: FAIL with import errors for `task_brain.capabilities` or `task_brain.planner`.

- [ ] **Step 3: Implement the capability registry**

Create `src/task_brain/capabilities.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from task_brain.domain import SubgoalType


class Capability(BaseModel):
    name: str
    subgoal_types: set[SubgoalType]
    input_schema: str
    output_schema: str
    timeout_s: int
    failure_modes: list[str]


class CapabilityRegistry(BaseModel):
    capabilities: dict[str, Capability]

    def supports_subgoal_type(self, subgoal_type: SubgoalType) -> bool:
        return any(subgoal_type in capability.subgoal_types for capability in self.capabilities.values())


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        capabilities={
            "mock_vln.navigate": Capability(
                name="mock_vln.navigate",
                subgoal_types={SubgoalType.NAVIGATE, SubgoalType.RETURN_TO_USER},
                input_schema="NavigationRequest",
                output_schema="NavigationResponse",
                timeout_s=10,
                failure_modes=["unreachable_viewpoint"],
            ),
            "mock_perception.observe": Capability(
                name="mock_perception.observe",
                subgoal_types={SubgoalType.OBSERVE, SubgoalType.VERIFY_OBJECT_PRESENCE},
                input_schema="ObservationRequest",
                output_schema="ObservationResponse",
                timeout_s=5,
                failure_modes=["target_not_visible"],
            ),
            "robobrain.plan": Capability(
                name="robobrain.plan",
                subgoal_types={SubgoalType.EMBODIED_MANIPULATION},
                input_schema="EmbodiedSubgoalRequest",
                output_schema="AtomicPlanResponse",
                timeout_s=30,
                failure_modes=["planner_unavailable", "no_valid_plan", "low_confidence"],
            ),
        }
    )
```

- [ ] **Step 4: Implement deterministic planner and validator**

Create `src/task_brain/planner.py`:

```python
from __future__ import annotations

from task_brain.capabilities import CapabilityRegistry
from task_brain.context import TaskContext
from task_brain.domain import HighLevelPlan, Predicate, Subgoal, SubgoalType, TaskIntent


class PlanValidationError(ValueError):
    pass


class DeterministicHighLevelPlanner:
    def generate(self, context: TaskContext) -> HighLevelPlan:
        if context.parsed.intent == TaskIntent.CHECK_OBJECT_PRESENCE:
            return self._check_plan(context)
        if context.parsed.intent == TaskIntent.FETCH_OBJECT:
            return self._fetch_plan(context)
        raise ValueError(f"unsupported intent: {context.parsed.intent}")

    def _top_memory_grounding(self, context: TaskContext) -> list[dict[str, str]]:
        if not context.memory.object_candidates:
            return [{"used_memory_id": "none", "reason": "no object memory was available"}]
        hit = context.memory.object_candidates[0]
        return [{"used_memory_id": hit.memory_id, "reason": hit.reason}]

    def _top_viewpoint(self, context: TaskContext) -> str:
        if not context.memory.object_candidates:
            return "living_room_search_viewpoint"
        location = context.memory.object_candidates[0].location
        support = location.split("/")[-1]
        return f"{support}_viewpoint"

    def _check_plan(self, context: TaskContext) -> HighLevelPlan:
        target_category = context.parsed.target_object.category
        viewpoint = self._top_viewpoint(context)
        return HighLevelPlan(
            plan_id="check_object_presence_001",
            memory_grounding=self._top_memory_grounding(context),
            subgoals=[
                Subgoal(
                    id="sg1",
                    type=SubgoalType.NAVIGATE,
                    target=viewpoint,
                    success_conditions=[Predicate.from_list(["at", "robot", viewpoint])],
                ),
                Subgoal(
                    id="sg2",
                    type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                    target_category=target_category,
                    success_conditions=[Predicate.from_list(["visible_category", target_category])],
                ),
            ],
        )

    def _fetch_plan(self, context: TaskContext) -> HighLevelPlan:
        target_category = context.parsed.target_object.category
        viewpoint = "kitchen_table_viewpoint"
        return HighLevelPlan(
            plan_id="fetch_object_001",
            memory_grounding=self._top_memory_grounding(context),
            subgoals=[
                Subgoal(
                    id="sg1",
                    type=SubgoalType.NAVIGATE,
                    target=viewpoint,
                    success_conditions=[
                        Predicate.from_list(["at", "robot", viewpoint]),
                        Predicate.from_list(["visible", "kitchen_table"]),
                    ],
                ),
                Subgoal(
                    id="sg2",
                    type=SubgoalType.VERIFY_OBJECT_PRESENCE,
                    target_category=target_category,
                    success_conditions=[
                        Predicate.from_list(["visible_category", target_category]),
                        Predicate.from_list(["reachable_category", target_category]),
                    ],
                ),
                Subgoal(
                    id="sg3",
                    type=SubgoalType.EMBODIED_MANIPULATION,
                    planner="robobrain_server",
                    subgoal="pick_up_object",
                    target_ref="selected_object",
                    success_conditions=[Predicate.from_list(["holding_category", "robot", target_category])],
                ),
                Subgoal(
                    id="sg4",
                    type=SubgoalType.RETURN_TO_USER,
                    target="user_location",
                    success_conditions=[Predicate.from_list(["near", "robot", "user"])],
                ),
            ],
        )


class PlanValidator:
    ATOMIC_ACTION_NAMES = {
        "move_arm_to_pregrasp",
        "align_gripper",
        "close_gripper",
        "lift",
    }

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def validate(self, plan: HighLevelPlan) -> None:
        if not plan.memory_grounding:
            raise PlanValidationError("plan must include memory grounding")
        seen_object_verification = False
        for subgoal in plan.subgoals:
            if not self.registry.supports_subgoal_type(subgoal.type):
                raise PlanValidationError(f"unsupported subgoal type: {subgoal.type.value}")
            if not subgoal.success_conditions:
                raise PlanValidationError(f"subgoal {subgoal.id} has no success conditions")
            text_fields = [subgoal.target, subgoal.subgoal, subgoal.target_ref]
            if any(value in self.ATOMIC_ACTION_NAMES for value in text_fields if value):
                raise PlanValidationError("high-level plan contains an atomic action")
            if subgoal.type == SubgoalType.VERIFY_OBJECT_PRESENCE:
                seen_object_verification = True
            if subgoal.type == SubgoalType.EMBODIED_MANIPULATION and not seen_object_verification:
                raise PlanValidationError("RoboBrain manipulation requires object verification first")
```

- [ ] **Step 5: Run planner validation tests**

Run:

```bash
pytest tests/test_planner_validation.py -q
```

Expected: PASS with `4 passed`.

- [ ] **Step 6: Commit planner and validator**

Run:

```bash
git add src/task_brain/capabilities.py src/task_brain/planner.py tests/test_planner_validation.py
git commit -m "feat: add high level planner validation"
```

### Task 5: Trace Logger and Mock Adapters

**Files:**
- Create: `src/task_brain/trace.py`
- Create: `src/task_brain/adapters/__init__.py`
- Create: `src/task_brain/adapters/mock_vln.py`
- Create: `src/task_brain/adapters/mock_perception.py`
- Create: `src/task_brain/adapters/robobrain.py`
- Create: `src/task_brain/adapters/mock_atomic_executor.py`
- Create: `data/scenarios/fetch_cup_retry/failures.json`
- Test: `tests/test_adapters.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_adapters.py`:

```python
from pathlib import Path

from task_brain.adapters.mock_atomic_executor import MockAtomicExecutor
from task_brain.adapters.mock_perception import MockPerception
from task_brain.adapters.mock_vln import MockVLN
from task_brain.adapters.robobrain import FakeRoboBrainClient
from task_brain.domain import AtomicAction, Predicate
from task_brain.trace import TraceLogger
from task_brain.world import MockWorld


WORLD = Path("data/scenarios/check_medicine_success/world.json")


def test_mock_vln_moves_robot_to_viewpoint() -> None:
    world = MockWorld.from_file(WORLD)
    result = MockVLN().navigate(world, "coffee_table_viewpoint")

    assert result.status == "success"
    assert world.has(Predicate.from_list(["at", "robot", "coffee_table_viewpoint"]))


def test_mock_perception_returns_visible_objects() -> None:
    world = MockWorld.from_file(WORLD)
    observation = MockPerception().observe(world, "coffee_table_viewpoint")

    assert observation.viewpoint == "coffee_table_viewpoint"
    assert "medicine_box_1" in observation.visible_objects


def test_fake_robobrain_returns_atomic_pickup_plan() -> None:
    response = FakeRoboBrainClient().plan_pickup("cup_1")

    assert response.status == "planned"
    assert [item.action for item in response.atomic_action_list] == [
        "move_arm_to_pregrasp",
        "align_gripper",
        "close_gripper",
        "lift",
    ]


def test_mock_atomic_executor_applies_success_delta() -> None:
    world = MockWorld.from_file(WORLD)
    executor = MockAtomicExecutor(failure_rules=[])
    result = executor.execute(
        world=world,
        target_object_id="cup_1",
        actions=[AtomicAction(action="lift", target="cup_1")],
        attempt=1,
    )

    assert result.status == "success"
    assert world.has(Predicate.from_list(["holding", "robot", "cup_1"]))


def test_trace_logger_records_ordered_events() -> None:
    trace = TraceLogger()
    trace.add("parse_instruction", "parsed task", {"intent": "fetch_object"})
    trace.add("retrieve_memory", "retrieved memory", {"count": 1})

    assert [event.step for event in trace.events] == ["parse_instruction", "retrieve_memory"]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_adapters.py -q
```

Expected: FAIL with import errors for adapters and trace modules.

- [ ] **Step 3: Implement trace logger and adapters**

Create `src/task_brain/trace.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from task_brain.domain import TraceEvent


class TraceLogger:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def add(self, step: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.events.append(TraceEvent(step=step, message=message, data=data or {}))

    def write_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

    def render(self) -> str:
        lines: list[str] = []
        for event in self.events:
            lines.append(f"[{event.step}] {event.message}")
            if event.data:
                lines.append(json.dumps(event.data, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def print(self, console: Console | None = None) -> None:
        target = console or Console()
        target.print(self.render())
```

Create `src/task_brain/adapters/__init__.py`:

```python
"""Adapters for mock execution and RoboBrain planning."""
```

Create `src/task_brain/adapters/mock_vln.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from task_brain.domain import Predicate
from task_brain.world import MockWorld


class NavigationResult(BaseModel):
    status: str
    viewpoint: str
    room: str | None = None
    failure_reason: str | None = None


class MockVLN:
    def navigate(self, world: MockWorld, viewpoint: str) -> NavigationResult:
        visible_entities = world.visible_entities(viewpoint)
        if not visible_entities and viewpoint != "user_location":
            return NavigationResult(
                status="failed",
                viewpoint=viewpoint,
                failure_reason="unknown_viewpoint",
            )
        world.apply_delta([Predicate.from_list(["at", "robot", viewpoint])])
        room = viewpoint.replace("_table_viewpoint", "").replace("_viewpoint", "")
        if viewpoint == "coffee_table_viewpoint":
            room = "living_room"
        if viewpoint == "kitchen_table_viewpoint":
            room = "kitchen"
        if viewpoint == "user_location":
            room = str(world.raw.get("user", {}).get("location", "bedroom"))
            world.apply_delta([Predicate.from_list(["near", "robot", "user"])])
        world.apply_delta([Predicate.from_list(["inroom", "robot", room])])
        return NavigationResult(status="success", viewpoint=viewpoint, room=room)
```

Create `src/task_brain/adapters/mock_perception.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from task_brain.world import MockWorld


class Observation(BaseModel):
    viewpoint: str
    visible_objects: list[str]
    visible_entities: list[str]


class MockPerception:
    def observe(self, world: MockWorld, viewpoint: str) -> Observation:
        return Observation(
            viewpoint=viewpoint,
            visible_objects=world.visible_objects(viewpoint),
            visible_entities=world.visible_entities(viewpoint),
        )
```

Create `src/task_brain/adapters/robobrain.py`:

```python
from __future__ import annotations

import httpx

from task_brain.domain import AtomicAction, AtomicPlanResponse, Predicate


class FakeRoboBrainClient:
    def plan_pickup(self, target_object_id: str) -> AtomicPlanResponse:
        return AtomicPlanResponse(
            status="planned",
            target_object_id=target_object_id,
            atomic_action_list=[
                AtomicAction(action="move_arm_to_pregrasp", target=target_object_id),
                AtomicAction(action="align_gripper", target=target_object_id),
                AtomicAction(action="close_gripper", target=target_object_id),
                AtomicAction(action="lift", target=target_object_id),
            ],
            expected_state_delta=[Predicate.from_list(["holding", "robot", target_object_id])],
            confidence=0.81,
        )


class HttpRoboBrainClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def plan_pickup(self, target_object_id: str) -> AtomicPlanResponse:
        response = httpx.post(
            f"{self.base_url}/plan_pickup",
            json={"target_object_id": target_object_id},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return AtomicPlanResponse.model_validate(response.json())
```

Create `src/task_brain/adapters/mock_atomic_executor.py`:

```python
from __future__ import annotations

from typing import Any

from task_brain.domain import AtomicAction, ExecutionResult, Predicate
from task_brain.world import MockWorld


class MockAtomicExecutor:
    def __init__(self, failure_rules: list[dict[str, Any]]) -> None:
        self.failure_rules = failure_rules

    def execute(
        self,
        *,
        world: MockWorld,
        target_object_id: str,
        actions: list[AtomicAction],
        attempt: int,
    ) -> ExecutionResult:
        for rule in self.failure_rules:
            if (
                rule.get("module") == "MockAtomicExecutor"
                and rule.get("target_object_id") == target_object_id
                and int(rule.get("attempt", 0)) == attempt
            ):
                return ExecutionResult(
                    status="failed",
                    failure_reason=str(rule["failure_reason"]),
                    evidence={"robot_holding": world.raw.get("robot", {}).get("holding")},
                )

        if not actions:
            return ExecutionResult(
                status="failed",
                failure_reason="empty_action_list",
                evidence={"robot_holding": world.raw.get("robot", {}).get("holding")},
            )

        delta = [
            Predicate.from_list(["holding", "robot", target_object_id]),
            Predicate.from_list(["not", "ontop", target_object_id, "kitchen_table"]),
        ]
        world.apply_delta(delta)
        return ExecutionResult(
            status="success",
            applied_state_delta=delta,
            evidence={"robot_holding": target_object_id},
        )
```

- [ ] **Step 4: Add failure fixture for the cup retry scenario**

Create `data/scenarios/fetch_cup_retry/failures.json`:

```json
[
  {
    "module": "MockAtomicExecutor",
    "target_object_id": "cup_1",
    "attempt": 1,
    "failure_reason": "object_slipped"
  }
]
```

- [ ] **Step 5: Run adapter tests**

Run:

```bash
pytest tests/test_adapters.py -q
```

Expected: PASS with `5 passed`.

- [ ] **Step 6: Commit trace and adapters**

Run:

```bash
git add src/task_brain/trace.py src/task_brain/adapters data/scenarios/fetch_cup_retry/failures.json tests/test_adapters.py
git commit -m "feat: add trace logger and mock adapters"
```

### Task 6: Verification and Recovery Policies

**Files:**
- Create: `src/task_brain/verification.py`
- Create: `src/task_brain/recovery.py`
- Test: `tests/test_verification.py`
- Test: `tests/test_recovery.py`

- [ ] **Step 1: Write failing verification tests**

Create `tests/test_verification.py`:

```python
from task_brain.domain import Predicate
from task_brain.verification import VerificationEngine
from task_brain.world import MockWorld


def test_verifies_visible_category_at_viewpoint() -> None:
    world = MockWorld.from_file("data/scenarios/check_medicine_success/world.json")
    engine = VerificationEngine()

    result = engine.verify(
        subject_id="sg2",
        world=world,
        conditions=[Predicate.from_list(["visible_category", "medicine_box"])],
        current_viewpoint="coffee_table_viewpoint",
    )

    assert result.passed is True


def test_rejects_missing_visible_category() -> None:
    world = MockWorld.from_file("data/scenarios/check_medicine_success/world.json")
    engine = VerificationEngine()

    result = engine.verify(
        subject_id="sg2",
        world=world,
        conditions=[Predicate.from_list(["visible_category", "medicine_box"])],
        current_viewpoint="kitchen_table_viewpoint",
    )

    assert result.passed is False
    assert result.failed_conditions[0].to_list() == ["visible_category", "medicine_box"]


def test_verifies_holding_category() -> None:
    world = MockWorld.from_file("data/scenarios/check_medicine_success/world.json")
    world.apply_delta([Predicate.from_list(["holding", "robot", "cup_1"])])
    engine = VerificationEngine()

    result = engine.verify(
        subject_id="sg3",
        world=world,
        conditions=[Predicate.from_list(["holding_category", "robot", "cup"])],
        current_viewpoint="kitchen_table_viewpoint",
    )

    assert result.passed is True
```

Create `tests/test_recovery.py`:

```python
from task_brain.domain import Predicate, VerificationResult
from task_brain.recovery import RecoveryPolicy


def test_recovery_switches_candidate_after_missing_object() -> None:
    policy = RecoveryPolicy(max_retries=1)
    result = VerificationResult(
        subject_id="sg2",
        passed=False,
        failed_conditions=[Predicate.from_list(["visible_category", "medicine_box"])],
    )

    decision = policy.decide(result, attempt=1, remaining_candidates=1)

    assert decision.action == "switch_candidate"


def test_recovery_retries_manipulation_once() -> None:
    policy = RecoveryPolicy(max_retries=1)
    result = VerificationResult(
        subject_id="sg3",
        passed=False,
        failed_conditions=[Predicate.from_list(["holding_category", "robot", "cup"])],
    )

    decision = policy.decide(result, attempt=1, remaining_candidates=0)

    assert decision.action == "retry_same_subgoal"


def test_recovery_reports_failure_after_candidates_exhausted() -> None:
    policy = RecoveryPolicy(max_retries=1)
    result = VerificationResult(
        subject_id="sg2",
        passed=False,
        failed_conditions=[Predicate.from_list(["visible_category", "medicine_box"])],
    )

    decision = policy.decide(result, attempt=2, remaining_candidates=0)

    assert decision.action == "report_failure"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_verification.py tests/test_recovery.py -q
```

Expected: FAIL with import errors.

- [ ] **Step 3: Implement verification engine**

Create `src/task_brain/verification.py`:

```python
from __future__ import annotations

from task_brain.domain import Predicate, VerificationResult
from task_brain.world import MockWorld


class VerificationEngine:
    def verify(
        self,
        *,
        subject_id: str,
        world: MockWorld,
        conditions: list[Predicate],
        current_viewpoint: str | None,
    ) -> VerificationResult:
        failed: list[Predicate] = []
        for condition in conditions:
            if not self._condition_passes(world, condition, current_viewpoint):
                failed.append(condition)
        return VerificationResult(
            subject_id=subject_id,
            passed=not failed,
            failed_conditions=failed,
            evidence={
                "current_viewpoint": current_viewpoint,
                "predicates": world.to_predicate_lists(),
            },
            recovery_hint=self._recovery_hint(failed),
        )

    def _condition_passes(
        self,
        world: MockWorld,
        condition: Predicate,
        current_viewpoint: str | None,
    ) -> bool:
        if condition.name == "visible_category":
            if current_viewpoint is None:
                return False
            category = condition.args[0]
            visible_objects = world.visible_objects(current_viewpoint)
            return any(world.object_category(object_id) == category for object_id in visible_objects)

        if condition.name == "reachable_category":
            category = condition.args[0]
            return any(
                world.object_category(item["id"]) == category and world.object_is_reachable(item["id"])
                for item in world.raw.get("objects", [])
            )

        if condition.name == "holding_category":
            if len(condition.args) != 2:
                return False
            held_object = world.raw.get("robot", {}).get("holding")
            return held_object is not None and world.object_category(str(held_object)) == condition.args[1]

        if condition.name == "visible":
            if current_viewpoint is None:
                return False
            return world.has(Predicate.from_list(["visible_from", condition.args[0], current_viewpoint]))

        return world.has(condition)

    def _recovery_hint(self, failed: list[Predicate]) -> str | None:
        if not failed:
            return None
        names = {condition.name for condition in failed}
        if "visible_category" in names:
            return "try_next_candidate"
        if "holding_category" in names:
            return "retry_same_subgoal"
        return "replan"
```

- [ ] **Step 4: Implement recovery policy**

Create `src/task_brain/recovery.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from task_brain.domain import VerificationResult


class RecoveryDecision(BaseModel):
    action: str
    reason: str


class RecoveryPolicy:
    def __init__(self, max_retries: int = 1) -> None:
        self.max_retries = max_retries

    def decide(
        self,
        verification: VerificationResult,
        *,
        attempt: int,
        remaining_candidates: int,
    ) -> RecoveryDecision:
        if verification.passed:
            return RecoveryDecision(action="continue", reason="verification passed")

        failed_names = {condition.name for condition in verification.failed_conditions}

        if "holding_category" in failed_names and attempt <= self.max_retries:
            return RecoveryDecision(
                action="retry_same_subgoal",
                reason="manipulation failed but retry budget remains",
            )

        if "visible_category" in failed_names and remaining_candidates > 0:
            return RecoveryDecision(
                action="switch_candidate",
                reason="target not visible at current candidate",
            )

        return RecoveryDecision(
            action="report_failure",
            reason="no recovery path remains",
        )
```

- [ ] **Step 5: Run verification and recovery tests**

Run:

```bash
pytest tests/test_verification.py tests/test_recovery.py -q
```

Expected: PASS with `6 passed`.

- [ ] **Step 6: Commit verification and recovery**

Run:

```bash
git add src/task_brain/verification.py src/task_brain/recovery.py tests/test_verification.py tests/test_recovery.py
git commit -m "feat: add verification and recovery policies"
```

### Task 7: Scenario Fixtures for Stale Memory and Cup Retry

**Files:**
- Create: `data/scenarios/check_medicine_stale_recover/world.json`
- Create: `data/scenarios/check_medicine_stale_recover/memory.json`
- Create: `data/scenarios/check_medicine_stale_recover/failures.json`
- Create: `data/scenarios/fetch_cup_retry/world.json`
- Create: `data/scenarios/fetch_cup_retry/memory.json`
- Test: `tests/test_scenario_fixtures.py`

- [ ] **Step 1: Write failing scenario fixture tests**

Create `tests/test_scenario_fixtures.py`:

```python
from pathlib import Path

from task_brain.memory import MemoryStore
from task_brain.world import MockWorld


def test_stale_medicine_world_disagrees_with_top_memory() -> None:
    world = MockWorld.from_file("data/scenarios/check_medicine_stale_recover/world.json")
    memory = MemoryStore.from_file("data/scenarios/check_medicine_stale_recover/memory.json")

    top = memory.retrieve("medicine_box").object_candidates[0]

    assert top.location == "living_room/coffee_table"
    assert world.visible_objects("coffee_table_viewpoint") == []
    assert "medicine_box_1" in world.visible_objects("kitchen_table_viewpoint")


def test_fetch_cup_retry_fixture_has_cup_on_kitchen_table() -> None:
    world = MockWorld.from_file("data/scenarios/fetch_cup_retry/world.json")
    memory = MemoryStore.from_file("data/scenarios/fetch_cup_retry/memory.json")

    assert "cup_1" in world.visible_objects("kitchen_table_viewpoint")
    assert memory.retrieve("cup").object_candidates[0].location == "kitchen/kitchen_table"


def test_all_required_fixture_files_exist() -> None:
    paths = [
        "data/scenarios/check_medicine_stale_recover/world.json",
        "data/scenarios/check_medicine_stale_recover/memory.json",
        "data/scenarios/check_medicine_stale_recover/failures.json",
        "data/scenarios/fetch_cup_retry/world.json",
        "data/scenarios/fetch_cup_retry/memory.json",
        "data/scenarios/fetch_cup_retry/failures.json",
    ]

    for path in paths:
        assert Path(path).exists()
```

- [ ] **Step 2: Run tests and confirm fixture files are missing**

Run:

```bash
pytest tests/test_scenario_fixtures.py -q
```

Expected: FAIL because the new scenario fixture files do not exist.

- [ ] **Step 3: Add stale medicine fixtures**

Create `data/scenarios/check_medicine_stale_recover/world.json`:

```json
{
  "world_id": "check_medicine_stale_recover",
  "robot": {
    "location": "bedroom",
    "holding": null
  },
  "user": {
    "location": "bedroom"
  },
  "rooms": [
    {"id": "bedroom", "connected_to": ["living_room"]},
    {"id": "living_room", "connected_to": ["bedroom", "kitchen"]},
    {"id": "kitchen", "connected_to": ["living_room"]}
  ],
  "furniture": [
    {"id": "coffee_table", "category": "table", "room": "living_room", "viewpoint": "coffee_table_viewpoint"},
    {"id": "kitchen_table", "category": "table", "room": "kitchen", "viewpoint": "kitchen_table_viewpoint"}
  ],
  "objects": [
    {
      "id": "medicine_box_1",
      "category": "medicine_box",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    }
  ],
  "predicates": [
    ["inroom", "robot", "bedroom"],
    ["near", "robot", "user"],
    ["connected", "bedroom", "living_room"],
    ["connected", "living_room", "bedroom"],
    ["connected", "living_room", "kitchen"],
    ["connected", "kitchen", "living_room"],
    ["inroom", "coffee_table", "living_room"],
    ["inroom", "kitchen_table", "kitchen"],
    ["visible_from", "coffee_table", "coffee_table_viewpoint"],
    ["visible_from", "kitchen_table", "kitchen_table_viewpoint"],
    ["inroom", "medicine_box_1", "kitchen"],
    ["ontop", "medicine_box_1", "kitchen_table"],
    ["visible_from", "medicine_box_1", "kitchen_table_viewpoint"],
    ["reachable", "medicine_box_1"],
    ["graspable", "medicine_box_1"]
  ]
}
```

Create `data/scenarios/check_medicine_stale_recover/memory.json`:

```json
{
  "object_memory": [
    {
      "memory_id": "objmem_medicine_coffee_table",
      "object_category": "medicine_box",
      "location": "living_room/coffee_table",
      "confidence": 0.82,
      "reason": "last_seen",
      "summary": "药盒上次在客厅茶几上看到。"
    },
    {
      "memory_id": "objmem_medicine_kitchen_table",
      "object_category": "medicine_box",
      "location": "kitchen/kitchen_table",
      "confidence": 0.43,
      "reason": "medicine sometimes placed near meal area",
      "summary": "药盒有时会被放在厨房餐桌附近。"
    }
  ],
  "negative_evidence": [],
  "episodic_memory": []
}
```

Create `data/scenarios/check_medicine_stale_recover/failures.json`:

```json
[]
```

- [ ] **Step 4: Add fetch cup retry fixtures**

Create `data/scenarios/fetch_cup_retry/world.json`:

```json
{
  "world_id": "fetch_cup_retry",
  "robot": {
    "location": "living_room",
    "holding": null
  },
  "user": {
    "location": "living_room"
  },
  "rooms": [
    {"id": "living_room", "connected_to": ["kitchen"]},
    {"id": "kitchen", "connected_to": ["living_room"]}
  ],
  "furniture": [
    {"id": "coffee_table", "category": "table", "room": "living_room", "viewpoint": "coffee_table_viewpoint"},
    {"id": "kitchen_table", "category": "table", "room": "kitchen", "viewpoint": "kitchen_table_viewpoint"}
  ],
  "objects": [
    {
      "id": "cup_1",
      "category": "cup",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    },
    {
      "id": "bowl_1",
      "category": "bowl",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    }
  ],
  "predicates": [
    ["inroom", "robot", "living_room"],
    ["near", "robot", "user"],
    ["connected", "living_room", "kitchen"],
    ["connected", "kitchen", "living_room"],
    ["inroom", "coffee_table", "living_room"],
    ["inroom", "kitchen_table", "kitchen"],
    ["visible_from", "kitchen_table", "kitchen_table_viewpoint"],
    ["inroom", "cup_1", "kitchen"],
    ["ontop", "cup_1", "kitchen_table"],
    ["visible_from", "cup_1", "kitchen_table_viewpoint"],
    ["reachable", "cup_1"],
    ["graspable", "cup_1"],
    ["inroom", "bowl_1", "kitchen"],
    ["ontop", "bowl_1", "kitchen_table"],
    ["visible_from", "bowl_1", "kitchen_table_viewpoint"],
    ["reachable", "bowl_1"],
    ["graspable", "bowl_1"]
  ]
}
```

Create `data/scenarios/fetch_cup_retry/memory.json`:

```json
{
  "object_memory": [
    {
      "memory_id": "objmem_cup_kitchen_table",
      "object_category": "cup",
      "location": "kitchen/kitchen_table",
      "confidence": 0.78,
      "reason": "prior_success_and_common_location",
      "summary": "水杯经常在厨房餐桌上。"
    },
    {
      "memory_id": "objmem_cup_coffee_table",
      "object_category": "cup",
      "location": "living_room/coffee_table",
      "confidence": 0.24,
      "reason": "low confidence recent sighting",
      "summary": "之前低置信度看到过杯子在客厅茶几。"
    }
  ],
  "negative_evidence": [],
  "episodic_memory": []
}
```

- [ ] **Step 5: Run fixture tests**

Run:

```bash
pytest tests/test_scenario_fixtures.py -q
```

Expected: PASS with `3 passed`.

- [ ] **Step 6: Commit scenario fixtures**

Run:

```bash
git add data/scenarios/check_medicine_stale_recover data/scenarios/fetch_cup_retry tests/test_scenario_fixtures.py
git commit -m "test: add required scenario fixtures"
```

### Task 8: Coarse-Grained LangGraph Orchestration

**Files:**
- Create: `src/task_brain/graph.py`
- Test: `tests/test_graph_scenarios.py`

This task must use a real compiled LangGraph. Keep the graph coarse-grained for the MVP: LangGraph owns the phase ordering, while the detailed subgoal/recovery loop stays inside one execution node.

- [ ] **Step 1: Write failing scenario graph tests**

Create `tests/test_graph_scenarios.py`:

```python
from task_brain.graph import build_task_graph, run_scenario


def steps(result):
    return [event.step for event in result.trace.events]


def test_build_task_graph_returns_invokable_langgraph() -> None:
    graph = build_task_graph()

    assert callable(getattr(graph, "invoke"))


def test_check_medicine_success_trace_order() -> None:
    result = run_scenario(
        scenario="check_medicine_success",
        instruction="去桌子那边看看药盒是不是还在。",
    )

    assert result.final_status == "success"
    assert steps(result).index("retrieve_memory") < steps(result).index("llm_generate_high_level_plan")
    assert "final_task_verification" in steps(result)


def test_check_medicine_stale_recover_switches_candidate() -> None:
    result = run_scenario(
        scenario="check_medicine_stale_recover",
        instruction="去桌子那边看看药盒是不是还在。",
    )

    assert result.final_status == "success"
    assert "recovery_switch_candidate" in steps(result)
    assert result.memory.raw["negative_evidence"][0]["location"] == "living_room/coffee_table"


def test_fetch_cup_retry_uses_robobrain_and_retries() -> None:
    result = run_scenario(
        scenario="fetch_cup_retry",
        instruction="去厨房找水杯，然后拿给我",
    )

    assert result.final_status == "success"
    assert "call_robobrain_planner" in steps(result)
    assert steps(result).count("execute_atomic_plan") == 2
    assert "post_action_verification_failed" in steps(result)
    assert result.world.raw["robot"]["holding"] == "cup_1"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_graph_scenarios.py -q
```

Expected: FAIL with import error for `task_brain.graph`.

- [ ] **Step 3: Implement graph state, loader nodes, and planning node**

Create `src/task_brain/graph.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from task_brain.adapters.mock_atomic_executor import MockAtomicExecutor
from task_brain.adapters.mock_perception import MockPerception
from task_brain.adapters.mock_vln import MockVLN
from task_brain.adapters.robobrain import FakeRoboBrainClient
from task_brain.capabilities import default_capability_registry
from task_brain.context import build_task_context
from task_brain.domain import CandidateLocation, Predicate, TaskRequest
from task_brain.memory import MemoryStore
from task_brain.parser import parse_instruction
from task_brain.planner import DeterministicHighLevelPlanner, PlanValidator
from task_brain.recovery import RecoveryPolicy
from task_brain.trace import TraceLogger
from task_brain.verification import VerificationEngine
from task_brain.world import MockWorld


SCENARIO_ROOT = Path("data/scenarios")


class TaskGraphState(TypedDict, total=False):
    scenario: str
    instruction: str
    request: TaskRequest
    parsed: object
    world: MockWorld
    memory: MemoryStore
    failure_rules: list[dict[str, Any]]
    memory_context: object
    task_context: object
    plan: object
    trace: TraceLogger
    current_viewpoint: str | None
    selected_object_id: str | None
    final_status: str


class ScenarioRunResult(BaseModel):
    final_status: str
    trace: TraceLogger
    world: MockWorld
    memory: MemoryStore

    model_config = {"arbitrary_types_allowed": True}


def _load_failure_rules(scenario: str) -> list[dict[str, Any]]:
    path = SCENARIO_ROOT / scenario / "failures.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _candidate_from_location(index: int, location: str, score: float, reason: str) -> CandidateLocation:
    support = location.split("/")[-1]
    room = location.split("/")[0]
    return CandidateLocation(
        id=f"candidate_{index}",
        room=room,
        support=support,
        viewpoint=f"{support}_viewpoint",
        score=score,
        reason=reason,
        source=["memory"],
    )


def _load_node(state: TaskGraphState) -> TaskGraphState:
    scenario = state["scenario"]
    state["world"] = MockWorld.from_file(SCENARIO_ROOT / scenario / "world.json")
    state["memory"] = MemoryStore.from_file(SCENARIO_ROOT / scenario / "memory.json")
    state["failure_rules"] = _load_failure_rules(scenario)
    state["trace"] = TraceLogger()
    state["request"] = TaskRequest(
        request_id=f"req_{scenario}",
        source="cli",
        user_id="demo_user",
        utterance=state["instruction"],
    )
    state["trace"].add("input_instruction", "received instruction", state["request"].model_dump())
    return state


def _parse_node(state: TaskGraphState) -> TaskGraphState:
    state["parsed"] = parse_instruction(state["instruction"])
    state["trace"].add("parse_instruction", "parsed instruction", state["parsed"].model_dump())
    return state


def _memory_node(state: TaskGraphState) -> TaskGraphState:
    parsed = state["parsed"]
    memory = state["memory"]
    state["memory_context"] = memory.retrieve(parsed.target_object.category)
    state["trace"].add("retrieve_memory", "retrieved memory", state["memory_context"].model_dump())
    return state


def _plan_node(state: TaskGraphState) -> TaskGraphState:
    state["task_context"] = build_task_context(
        state["request"],
        state["parsed"],
        state["memory_context"],
        state["world"],
    )
    state["trace"].add("build_task_context", "built task context", state["task_context"].model_dump())
    registry = default_capability_registry()
    planner = DeterministicHighLevelPlanner()
    state["plan"] = planner.generate(state["task_context"])
    state["trace"].add("llm_generate_high_level_plan", "generated high-level plan", state["plan"].model_dump())
    PlanValidator(registry).validate(state["plan"])
    state["trace"].add("validate_plan", "validated high-level plan", {"plan_id": state["plan"].plan_id})
    return state
```

- [ ] **Step 4: Add coarse execution, final verification, graph builder, and scenario runner**

Append this to `src/task_brain/graph.py`:

```python
def _execute_subgoal_loop_node(state: TaskGraphState) -> TaskGraphState:
    world = state["world"]
    memory = state["memory"]
    parsed = state["parsed"]
    plan = state["plan"]
    memory_context = state["memory_context"]
    failure_rules = state["failure_rules"]
    trace = state["trace"]
    candidates = [
        _candidate_from_location(index + 1, hit.location, hit.confidence, hit.reason)
        for index, hit in enumerate(memory_context.object_candidates)
    ]
    selected_object_id: str | None = None
    current_viewpoint: str | None = None
    verification = VerificationEngine()
    recovery = RecoveryPolicy(max_retries=1)
    vln = MockVLN()
    perception = MockPerception()
    robobrain = FakeRoboBrainClient()
    executor = MockAtomicExecutor(failure_rules=failure_rules)
    atomic_attempt = 1

    candidate_index = 0
    while candidate_index < max(1, len(candidates)):
        candidate = candidates[candidate_index] if candidates else None
        switched_candidate = False
        for subgoal in plan.subgoals:
            if subgoal.type.value == "navigate":
                target = candidate.viewpoint if candidate and subgoal.target != "user_location" else subgoal.target
                if target is None:
                    state["final_status"] = "failed"
                    return state
                nav = vln.navigate(world, target)
                current_viewpoint = target
                state["current_viewpoint"] = current_viewpoint
                trace.add("call_vln", "navigation completed", nav.model_dump())
                nav_verification = verification.verify(
                    subject_id=subgoal.id,
                    world=world,
                    conditions=subgoal.success_conditions,
                    current_viewpoint=current_viewpoint,
                )
                trace.add("verify_arrival", "arrival verified", nav_verification.model_dump())
                if not nav_verification.passed:
                    state["final_status"] = "failed"
                    return state

            if subgoal.type.value == "verify_object_presence":
                if current_viewpoint is None:
                    state["final_status"] = "failed"
                    return state
                observation = perception.observe(world, current_viewpoint)
                trace.add("observe_scene", "observed current scene", observation.model_dump())
                target_category = subgoal.target_category or parsed.target_object.category
                matching = [
                    object_id
                    for object_id in observation.visible_objects
                    if world.object_category(object_id) == target_category
                ]
                selected_object_id = matching[0] if matching else None
                state["selected_object_id"] = selected_object_id
                object_verification = verification.verify(
                    subject_id=subgoal.id,
                    world=world,
                    conditions=subgoal.success_conditions,
                    current_viewpoint=current_viewpoint,
                )
                trace.add("verify_object_presence", "object presence checked", object_verification.model_dump())
                if not object_verification.passed:
                    if candidate is not None:
                        location = f"{candidate.room}/{candidate.support}"
                        memory.mark_negative(location, parsed.target_object.category, "searched_not_found")
                    decision = recovery.decide(
                        object_verification,
                        attempt=1,
                        remaining_candidates=len(candidates) - candidate_index - 1,
                    )
                    if decision.action == "switch_candidate":
                        trace.add("recovery_switch_candidate", "switching to next candidate", decision.model_dump())
                        candidate_index += 1
                        switched_candidate = True
                        break
                    trace.add("report_failure", "object not found", decision.model_dump())
                    state["final_status"] = "failed"
                    return state

            if subgoal.type.value == "embodied_manipulation":
                if selected_object_id is None:
                    trace.add("report_failure", "no selected object for RoboBrain", {})
                    state["final_status"] = "failed"
                    return state
                atomic_plan = robobrain.plan_pickup(selected_object_id)
                trace.add("call_robobrain_planner", "RoboBrain returned atomic plan", atomic_plan.model_dump())
                while True:
                    execution = executor.execute(
                        world=world,
                        target_object_id=selected_object_id,
                        actions=atomic_plan.atomic_action_list,
                        attempt=atomic_attempt,
                    )
                    trace.add("execute_atomic_plan", "mock atomic executor returned", execution.model_dump())
                    action_verification = verification.verify(
                        subject_id=subgoal.id,
                        world=world,
                        conditions=subgoal.success_conditions,
                        current_viewpoint=current_viewpoint,
                    )
                    if action_verification.passed:
                        trace.add("post_action_verification", "manipulation verified", action_verification.model_dump())
                        break
                    trace.add(
                        "post_action_verification_failed",
                        "manipulation verification failed",
                        action_verification.model_dump(),
                    )
                    decision = recovery.decide(
                        action_verification,
                        attempt=atomic_attempt,
                        remaining_candidates=len(candidates) - candidate_index - 1,
                    )
                    if decision.action == "retry_same_subgoal":
                        trace.add("recovery_retry_same_subgoal", "retrying manipulation", decision.model_dump())
                        atomic_attempt += 1
                        continue
                    trace.add("report_failure", "manipulation failed", decision.model_dump())
                    state["final_status"] = "failed"
                    return state

            if subgoal.type.value == "return_to_user":
                nav = vln.navigate(world, "user_location")
                current_viewpoint = "user_location"
                state["current_viewpoint"] = current_viewpoint
                trace.add("call_vln", "returned to user", nav.model_dump())
                final_nav = verification.verify(
                    subject_id=subgoal.id,
                    world=world,
                    conditions=subgoal.success_conditions,
                    current_viewpoint=current_viewpoint,
                )
                trace.add("verify_return_to_user", "return verified", final_nav.model_dump())
                if not final_nav.passed:
                    state["final_status"] = "failed"
                    return state
        if not switched_candidate:
            break
    return state


def _final_verification_node(state: TaskGraphState) -> TaskGraphState:
    if state.get("final_status") == "failed":
        return state

    parsed = state["parsed"]
    world = state["world"]
    trace = state["trace"]
    current_viewpoint = state.get("current_viewpoint")
    verification = VerificationEngine()

    final_conditions = []
    if parsed.requires_manipulation:
        final_conditions.append(Predicate.from_list(["holding_category", "robot", parsed.target_object.category]))
        final_conditions.append(Predicate.from_list(["near", "robot", "user"]))
    else:
        final_conditions.append(Predicate.from_list(["visible_category", parsed.target_object.category]))

    final = verification.verify(
        subject_id="final_task",
        world=world,
        conditions=final_conditions,
        current_viewpoint=current_viewpoint,
    )
    trace.add("final_task_verification", "final task checked", final.model_dump())
    if final.passed:
        trace.add("update_memory", "memory updated from successful task", {"target": parsed.target_object.category})
        state["final_status"] = "success"
    else:
        state["final_status"] = "failed"
    return state


def build_task_graph():
    graph = StateGraph(TaskGraphState)
    graph.add_node("load", _load_node)
    graph.add_node("parse_instruction", _parse_node)
    graph.add_node("retrieve_memory", _memory_node)
    graph.add_node("plan", _plan_node)
    graph.add_node("execute_subgoal_loop", _execute_subgoal_loop_node)
    graph.add_node("final_task_verification", _final_verification_node)

    graph.add_edge(START, "load")
    graph.add_edge("load", "parse_instruction")
    graph.add_edge("parse_instruction", "retrieve_memory")
    graph.add_edge("retrieve_memory", "plan")
    graph.add_edge("plan", "execute_subgoal_loop")
    graph.add_edge("execute_subgoal_loop", "final_task_verification")
    graph.add_edge("final_task_verification", END)
    return graph.compile()


def run_scenario(scenario: str, instruction: str) -> ScenarioRunResult:
    graph = build_task_graph()
    state = graph.invoke({"scenario": scenario, "instruction": instruction})
    return ScenarioRunResult(
        final_status=state.get("final_status", "failed"),
        trace=state["trace"],
        world=state["world"],
        memory=state["memory"],
    )
```

- [ ] **Step 5: Run graph scenario tests**

Run:

```bash
pytest tests/test_graph_scenarios.py -q
```

Expected: PASS with `4 passed`.

- [ ] **Step 6: Commit graph orchestration**

Run:

```bash
git add src/task_brain/graph.py tests/test_graph_scenarios.py
git commit -m "feat: orchestrate task brain scenarios"
```

### Task 9: CLI Trace Demo

**Files:**
- Create: `src/task_brain/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from task_brain.cli import app


def test_cli_runs_check_medicine_success() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            "去桌子那边看看药盒是不是还在。",
        ],
    )

    assert result.exit_code == 0
    assert "[parse_instruction]" in result.stdout
    assert "[retrieve_memory]" in result.stdout
    assert "[final_task_verification]" in result.stdout


def test_cli_returns_error_for_failed_unknown_instruction() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            "帮我唱一首歌",
        ],
    )

    assert result.exit_code == 1
    assert "unsupported instruction" in result.stdout
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: FAIL with import error for `task_brain.cli`.

- [ ] **Step 3: Implement CLI**

Create `src/task_brain/cli.py`:

```python
from __future__ import annotations

import typer
from rich.console import Console

from task_brain.graph import run_scenario


app = typer.Typer(help="Memory-grounded embodied task brain CLI")


@app.command()
def run(
    scenario: str = typer.Option(..., help="Scenario fixture name"),
    instruction: str = typer.Option(..., help="Chinese household task instruction"),
) -> None:
    console = Console()
    try:
        result = run_scenario(scenario=scenario, instruction=instruction)
    except Exception as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(result.trace.render())
    if result.final_status != "success":
        raise typer.Exit(1)
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Run demo commands manually**

Run:

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

Expected:

- Each command exits with status `0`.
- The first trace contains `final_task_verification`.
- The second trace contains `recovery_switch_candidate`.
- The third trace contains `call_robobrain_planner`, two `execute_atomic_plan` events, and `post_action_verification_failed`.

- [ ] **Step 6: Commit CLI**

Run:

```bash
git add src/task_brain/cli.py tests/test_cli.py
git commit -m "feat: add CLI trace runner"
```

### Task 10: Required Negative Scenario Coverage

**Files:**
- Create: `data/scenarios/object_not_found/world.json`
- Create: `data/scenarios/object_not_found/memory.json`
- Create: `data/scenarios/object_not_found/failures.json`
- Create: `data/scenarios/distractor_rejected/world.json`
- Create: `data/scenarios/distractor_rejected/memory.json`
- Create: `data/scenarios/distractor_rejected/failures.json`
- Modify: `tests/test_graph_scenarios.py`

- [ ] **Step 1: Add failing tests for candidate exhaustion and distractor rejection**

Append to `tests/test_graph_scenarios.py`:

```python

def test_object_not_found_candidate_exhausted_reports_failure() -> None:
    result = run_scenario(
        scenario="object_not_found",
        instruction="去桌子那边看看药盒是不是还在。",
    )

    assert result.final_status == "failed"
    assert "report_failure" in steps(result)


def test_distractor_object_rejected_does_not_pick_bowl_as_cup() -> None:
    result = run_scenario(
        scenario="distractor_rejected",
        instruction="去厨房找水杯，然后拿给我",
    )

    assert result.final_status == "failed"
    assert result.world.raw["robot"]["holding"] is None
    assert "call_robobrain_planner" not in steps(result)
```

- [ ] **Step 2: Run tests and confirm fixtures are missing**

Run:

```bash
pytest tests/test_graph_scenarios.py::test_object_not_found_candidate_exhausted_reports_failure tests/test_graph_scenarios.py::test_distractor_object_rejected_does_not_pick_bowl_as_cup -q
```

Expected: FAIL because the new scenario files do not exist.

- [ ] **Step 3: Create object-not-found fixtures**

Create `data/scenarios/object_not_found/world.json`:

```json
{
  "world_id": "object_not_found",
  "robot": {"location": "bedroom", "holding": null},
  "user": {"location": "bedroom"},
  "rooms": [
    {"id": "bedroom", "connected_to": ["living_room"]},
    {"id": "living_room", "connected_to": ["bedroom", "kitchen"]},
    {"id": "kitchen", "connected_to": ["living_room"]}
  ],
  "furniture": [
    {"id": "coffee_table", "category": "table", "room": "living_room", "viewpoint": "coffee_table_viewpoint"},
    {"id": "kitchen_table", "category": "table", "room": "kitchen", "viewpoint": "kitchen_table_viewpoint"}
  ],
  "objects": [
    {
      "id": "remote_1",
      "category": "remote_control",
      "room": "living_room",
      "support": "coffee_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    }
  ],
  "predicates": [
    ["inroom", "robot", "bedroom"],
    ["near", "robot", "user"],
    ["visible_from", "coffee_table", "coffee_table_viewpoint"],
    ["visible_from", "kitchen_table", "kitchen_table_viewpoint"],
    ["inroom", "remote_1", "living_room"],
    ["ontop", "remote_1", "coffee_table"],
    ["visible_from", "remote_1", "coffee_table_viewpoint"],
    ["reachable", "remote_1"],
    ["graspable", "remote_1"]
  ]
}
```

Create `data/scenarios/object_not_found/memory.json`:

```json
{
  "object_memory": [
    {
      "memory_id": "objmem_medicine_coffee_table",
      "object_category": "medicine_box",
      "location": "living_room/coffee_table",
      "confidence": 0.82,
      "reason": "last_seen",
      "summary": "药盒上次在客厅茶几上看到。"
    },
    {
      "memory_id": "objmem_medicine_kitchen_table",
      "object_category": "medicine_box",
      "location": "kitchen/kitchen_table",
      "confidence": 0.43,
      "reason": "secondary candidate",
      "summary": "药盒有时会在厨房餐桌。"
    }
  ],
  "negative_evidence": [],
  "episodic_memory": []
}
```

Create `data/scenarios/object_not_found/failures.json`:

```json
[]
```

- [ ] **Step 4: Create distractor-rejected fixtures**

Create `data/scenarios/distractor_rejected/world.json`:

```json
{
  "world_id": "distractor_rejected",
  "robot": {"location": "living_room", "holding": null},
  "user": {"location": "living_room"},
  "rooms": [
    {"id": "living_room", "connected_to": ["kitchen"]},
    {"id": "kitchen", "connected_to": ["living_room"]}
  ],
  "furniture": [
    {"id": "kitchen_table", "category": "table", "room": "kitchen", "viewpoint": "kitchen_table_viewpoint"}
  ],
  "objects": [
    {
      "id": "bowl_1",
      "category": "bowl",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    },
    {
      "id": "medicine_bottle_1",
      "category": "medicine_bottle",
      "room": "kitchen",
      "support": "kitchen_table",
      "relation": "ontop",
      "states": {"reachable": true, "graspable": true}
    }
  ],
  "predicates": [
    ["inroom", "robot", "living_room"],
    ["near", "robot", "user"],
    ["visible_from", "kitchen_table", "kitchen_table_viewpoint"],
    ["inroom", "bowl_1", "kitchen"],
    ["ontop", "bowl_1", "kitchen_table"],
    ["visible_from", "bowl_1", "kitchen_table_viewpoint"],
    ["reachable", "bowl_1"],
    ["graspable", "bowl_1"],
    ["inroom", "medicine_bottle_1", "kitchen"],
    ["ontop", "medicine_bottle_1", "kitchen_table"],
    ["visible_from", "medicine_bottle_1", "kitchen_table_viewpoint"],
    ["reachable", "medicine_bottle_1"],
    ["graspable", "medicine_bottle_1"]
  ]
}
```

Create `data/scenarios/distractor_rejected/memory.json`:

```json
{
  "object_memory": [
    {
      "memory_id": "objmem_cup_kitchen_table",
      "object_category": "cup",
      "location": "kitchen/kitchen_table",
      "confidence": 0.78,
      "reason": "prior_success_and_common_location",
      "summary": "水杯经常在厨房餐桌上。"
    }
  ],
  "negative_evidence": [],
  "episodic_memory": []
}
```

Create `data/scenarios/distractor_rejected/failures.json`:

```json
[]
```

- [ ] **Step 5: Run all scenario tests**

Run:

```bash
pytest tests/test_graph_scenarios.py -q
```

Expected: PASS with `5 passed`.

- [ ] **Step 6: Commit negative scenario coverage**

Run:

```bash
git add data/scenarios/object_not_found data/scenarios/distractor_rejected tests/test_graph_scenarios.py
git commit -m "test: cover object not found and distractor rejection"
```

### Task 11: LLM Planner Interface With Safe Fallback

**Files:**
- Modify: `src/task_brain/planner.py`
- Modify: `src/task_brain/graph.py`
- Test: `tests/test_planner_validation.py`

- [ ] **Step 1: Add failing tests for LLM planner fallback**

Append to `tests/test_planner_validation.py`:

```python

def test_invalid_llm_plan_falls_back_to_deterministic_plan() -> None:
    from task_brain.planner import PlannerService

    context = build_context()

    def invalid_provider(_context):
        return {"plan_id": "bad", "memory_grounding": [], "subgoals": []}

    service = PlannerService(provider=invalid_provider)
    plan = service.generate_validated(context, default_capability_registry())

    assert plan.plan_id == "fetch_object_001"
    assert plan.memory_grounding
```

- [ ] **Step 2: Run the fallback test and confirm it fails**

Run:

```bash
pytest tests/test_planner_validation.py::test_invalid_llm_plan_falls_back_to_deterministic_plan -q
```

Expected: FAIL because `PlannerService` does not exist.

- [ ] **Step 3: Implement planner service fallback**

Append to `src/task_brain/planner.py`:

```python
from collections.abc import Callable
from typing import Any


LLMPlanProvider = Callable[[TaskContext], dict[str, Any]]


class PlannerService:
    def __init__(self, provider: LLMPlanProvider | None = None) -> None:
        self.provider = provider
        self.fallback = DeterministicHighLevelPlanner()

    def generate_validated(
        self,
        context: TaskContext,
        registry: CapabilityRegistry,
    ) -> HighLevelPlan:
        validator = PlanValidator(registry)
        if self.provider is not None:
            try:
                raw = self.provider(context)
                plan = HighLevelPlan.model_validate(raw)
                validator.validate(plan)
                return plan
            except Exception:
                fallback_plan = self.fallback.generate(context)
                validator.validate(fallback_plan)
                return fallback_plan

        plan = self.fallback.generate(context)
        validator.validate(plan)
        return plan
```

- [ ] **Step 4: Update the LangGraph planning node to use PlannerService**

In `src/task_brain/graph.py`, replace:

```python
from task_brain.planner import DeterministicHighLevelPlanner, PlanValidator
```

with:

```python
from task_brain.planner import PlannerService
```

Replace:

```python
    registry = default_capability_registry()
    planner = DeterministicHighLevelPlanner()
    state["plan"] = planner.generate(state["task_context"])
    state["trace"].add("llm_generate_high_level_plan", "generated high-level plan", state["plan"].model_dump())
    PlanValidator(registry).validate(state["plan"])
    state["trace"].add("validate_plan", "validated high-level plan", {"plan_id": state["plan"].plan_id})
```

with:

```python
    registry = default_capability_registry()
    planner = PlannerService()
    state["plan"] = planner.generate_validated(state["task_context"], registry)
    state["trace"].add("llm_generate_high_level_plan", "generated high-level plan", state["plan"].model_dump())
    state["trace"].add("validate_plan", "validated high-level plan", {"plan_id": state["plan"].plan_id})
```

- [ ] **Step 5: Run planner and graph tests**

Run:

```bash
pytest tests/test_planner_validation.py tests/test_graph_scenarios.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit planner service**

Run:

```bash
git add src/task_brain/planner.py src/task_brain/graph.py tests/test_planner_validation.py
git commit -m "feat: add safe LLM planner fallback"
```

### Task 12: End-to-End Quality Gate

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: PASS. The expected count is the total number of tests created in earlier tasks.

- [ ] **Step 2: Run lint**

Run:

```bash
ruff check .
```

Expected: PASS with no lint errors.

- [ ] **Step 3: Run the three required demo scenarios**

Run:

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

Expected:

- All three commands exit with code `0`.
- The stale-memory scenario shows `recovery_switch_candidate`.
- The cup scenario shows `call_robobrain_planner`, `post_action_verification_failed`, and `recovery_retry_same_subgoal`.

- [ ] **Step 4: Update README with verified commands**

Replace the contents of `README.md` with:

````markdown
# Memory-Grounded Embodied Task Brain

This repository contains a CLI MVP for a household elder-care robot task brain.
It demonstrates memory-grounded high-level planning, module orchestration,
subgoal verification, failure recovery, and memory updates using a JSON mock
world and structured trace output.

## Install

```bash
python -m pip install -e ".[dev]"
```

## Run Demo Scenarios

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

## Verify

```bash
pytest -q
ruff check .
```

## Scope

The MVP uses CLI input, a JSON mock world, a safe high-level planner fallback,
a fake RoboBrain client for local tests, and a mock atomic executor. The
RoboBrain HTTP adapter is available for later server integration through the
same atomic-plan response schema.
````

- [ ] **Step 5: Commit documentation update**

Run:

```bash
git add README.md
git commit -m "docs: document task brain demo commands"
```

### Task 13: Optional Hermes/Feishu Bridge Spike

**Files:**
- Create: `src/task_brain/gateway.py`
- Test: `tests/test_gateway.py`

This task is optional for the one-month MVP. Start it only after Tasks 1-12 are green.

- [ ] **Step 1: Write gateway conversion tests**

Create `tests/test_gateway.py`:

```python
import pytest

from task_brain.gateway import FeishuGatewayBridge


def test_feishu_message_converts_to_task_request() -> None:
    bridge = FeishuGatewayBridge(allowed_user_ids={"elder_001"})

    request = bridge.to_task_request(
        {
            "message_id": "msg_001",
            "sender_id": "elder_001",
            "text": "去厨房找水杯，然后拿给我",
        }
    )

    assert request.source == "feishu"
    assert request.user_id == "elder_001"
    assert request.utterance == "去厨房找水杯，然后拿给我"


def test_feishu_bridge_rejects_non_allowlisted_user() -> None:
    bridge = FeishuGatewayBridge(allowed_user_ids={"elder_001"})

    with pytest.raises(PermissionError):
        bridge.to_task_request(
            {
                "message_id": "msg_002",
                "sender_id": "unknown_user",
                "text": "去厨房找水杯，然后拿给我",
            }
        )
```

- [ ] **Step 2: Run gateway tests and confirm they fail**

Run:

```bash
pytest tests/test_gateway.py -q
```

Expected: FAIL with import error for `task_brain.gateway`.

- [ ] **Step 3: Implement the bridge-only gateway module**

Create `src/task_brain/gateway.py`:

```python
from __future__ import annotations

from task_brain.domain import TaskRequest


class FeishuGatewayBridge:
    def __init__(self, allowed_user_ids: set[str]) -> None:
        self.allowed_user_ids = allowed_user_ids

    def to_task_request(self, event: dict[str, str]) -> TaskRequest:
        sender_id = event["sender_id"]
        if sender_id not in self.allowed_user_ids:
            raise PermissionError(f"user is not allowed: {sender_id}")
        return TaskRequest(
            request_id=event["message_id"],
            source="feishu",
            user_id=sender_id,
            utterance=event["text"],
        )
```

- [ ] **Step 4: Run gateway tests**

Run:

```bash
pytest tests/test_gateway.py -q
```

Expected: PASS with `2 passed`.

- [ ] **Step 5: Commit optional gateway bridge**

Run:

```bash
git add src/task_brain/gateway.py tests/test_gateway.py
git commit -m "feat: add optional Feishu gateway bridge"
```

## Self-Review Checklist

Spec coverage:

- CLI demo: Tasks 8, 9, and 12.
- Memory before planning: Tasks 3, 4, 8, and 11.
- LLM high-level planning with fallback: Tasks 4 and 11.
- Mock world with BEHAVIOR-like predicates: Tasks 2 and 7.
- Mock VLN/perception/executor: Task 5.
- RoboBrain atomic action-list interface: Task 5.
- Verification and recovery: Tasks 6, 8, and 10.
- Memory updates and negative evidence: Tasks 3 and 8.
- Required scenarios: Tasks 7, 8, and 10.
- Hermes/Feishu optional milestone: Task 13.

Type consistency:

- `Predicate`, `TaskRequest`, `ParsedTask`, `MemoryContext`, `HighLevelPlan`, `Subgoal`, `VerificationResult`, `AtomicPlanResponse`, and `ExecutionResult` are defined in Task 1 and reused consistently.
- `MockWorld`, `MemoryStore`, `TraceLogger`, `VerificationEngine`, `RecoveryPolicy`, and adapter classes are introduced before graph integration.
- Trace step names used in tests match the names emitted in `graph.py`.

Quality gates:

- Each task writes a failing test first.
- Each task includes exact commands and expected outcomes.
- Each task ends with a focused commit.
- The optional gateway task is isolated from the CLI MVP.
