"""Canonical tool interface checks for the V3.5 migration."""

from __future__ import annotations

import inspect

from homemaster.alfworld.tools import (
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_verify,
)
from homemaster.domain.tools import (
    make_load_skill,
    make_memory_retriever,
    make_memory_writer,
    make_robot_go_to,
    make_robot_manipulate,
    make_robot_verify,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.task_state.tools import make_task_planner_tool, make_task_progress_check_tool
from homemaster.tools.contracts import RegisteredTool


def _factories():
    return (
        make_task_interpreter,
        make_memory_retriever,
        make_target_grounder,
        make_load_skill,
        make_robot_go_to,
        make_robot_manipulate,
        make_robot_verify,
        make_memory_writer,
        make_task_summarizer,
        make_task_planner_tool,
        make_task_progress_check_tool,
        make_alfworld_robot_go_to,
        make_alfworld_robot_manipulate,
        make_alfworld_robot_verify,
    )


def test_all_formal_factories_return_registered_tools() -> None:
    for factory in _factories():
        value = factory()
        assert isinstance(value, RegisteredTool), factory.__name__
        assert inspect.iscoroutinefunction(value.executor.execute), factory.__name__
        if value.verifier is not None:
            assert inspect.iscoroutinefunction(value.verifier.verify), factory.__name__


def test_formal_factories_expose_only_canonical_model_aliases() -> None:
    names = {factory().definition.model_alias for factory in _factories()}
    assert "robot_go_to" in names
    assert "robot_navigate" not in names
    assert "robot_find_object" not in names
