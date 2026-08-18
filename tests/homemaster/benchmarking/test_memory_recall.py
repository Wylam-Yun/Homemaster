from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from homemaster.benchmarking.memory_recall import (
    CompletedCommand,
    build_dataset,
    build_write_prompt,
    evaluate_search_events,
    evaluation_cases,
    generate_run,
    load_dataset,
    parse_stream_events,
    status_run,
    write_run,
)


def _tool_events(job_id: str = "job-1") -> str:
    payload = {
        "success": True,
        "status": "success",
        "domain_status": "accepted",
        "verified_terminal_state": False,
        "backend_attempted": True,
        "job_id": job_id,
    }
    return "\n".join(
        (
            json.dumps({"type": "tool_started", "tool_name": "mindmemos_add"}),
            json.dumps(
                {
                    "type": "tool_completed",
                    "tool_name": "mindmemos_add",
                    "output": json.dumps(payload, ensure_ascii=False),
                },
                ensure_ascii=False,
            ),
            json.dumps({"type": "result", "status": "replied", "final_reply": "完成"}),
        )
    )


def test_dataset_is_deterministic_and_has_locked_distribution() -> None:
    first = build_dataset("run-test")
    second = build_dataset("run-test")

    assert first == second
    assert len(first) == 100
    assert len({item.subject for item in first}) == 100
    assert {item.website for item in first} == {
        "星河商城",
        "云桥邮箱",
        "北辰文档",
        "灯塔工单",
        "青禾CRM",
        "天穹分析",
        "松果人事",
        "银湾财务",
        "远帆旅行",
        "云峰平台",
    }
    assert sum(item.kind == "target" for item in first) == 70
    assert sum(item.kind == "near_distractor" for item in first) == 20
    assert sum(item.kind == "unrelated_distractor" for item in first) == 10
    assert all(item.predicate == "web_operation_steps" for item in first)
    assert all(item.source == "user_statement" for item in first)
    assert all(item.subject.startswith("HM100::run-test::") for item in first)


def test_generate_run_uses_private_permissions(tmp_path: Path) -> None:
    paths = generate_run(base=tmp_path, run_id="run-test")

    assert len(load_dataset(paths.dataset)) == 100
    assert os.stat(paths.root).st_mode & 0o777 == 0o700
    assert os.stat(paths.raw).st_mode & 0o777 == 0o700
    assert os.stat(paths.dataset).st_mode & 0o777 == 0o600
    assert os.stat(paths.checkpoint).st_mode & 0o777 == 0o600


def test_parse_stream_events_ignores_non_json_lines() -> None:
    events = parse_stream_events('noise\n{"type":"tool_started","tool_name":"mindmemos_add"}\n')
    assert events == ({"type": "tool_started", "tool_name": "mindmemos_add"},)


def test_write_prompt_locks_fact_and_forbids_other_tools() -> None:
    record = build_dataset("run-test")[0]
    prompt = build_write_prompt(record)

    assert record.subject in prompt
    assert "mindmemos_add" in prompt
    assert "只调用一次" in prompt
    assert "不要调用 mindmemos_search" in prompt


def test_write_run_confirms_receipt_and_resume_skips_success(tmp_path: Path) -> None:
    paths = generate_run(base=tmp_path, run_id="run-test")
    records = load_dataset(paths.dataset)
    calls: list[list[str]] = []

    def verify(job_id, record):
        return {
            "job_id": job_id,
            "status": "completed",
            "verified_terminal_state": True,
            "memory_id": f"memory-{record.index}",
            "record": record.tool_record,
        }

    def runner(command, *, cwd, env, timeout):
        calls.append(list(command))
        return CompletedCommand(
            returncode=0,
            stdout=_tool_events(f"job-{len(calls)}"),
            stderr="",
            elapsed_seconds=1.5,
            timed_out=False,
        )

    first = write_run(
        paths=paths,
        repo_root=tmp_path,
        timeout_seconds=10,
        max_records=1,
        runner=runner,
        terminal_verifier=verify,
    )
    second = write_run(
        paths=paths,
        repo_root=tmp_path,
        timeout_seconds=10,
        max_records=1,
        runner=runner,
        terminal_verifier=verify,
    )

    assert first["confirmed"] == 1
    assert second["confirmed"] == 1
    assert second["confirmed_total"] == 2
    assert len(calls) == 2
    assert records[0].subject in calls[0][4]
    assert records[1].subject in calls[1][4]
    assert records[0].subject not in calls[1][4]
    assert calls[0][1:4] == ["-m", "homemaster.cli", "-p"]
    assert calls[0][-2:] == ["--output-format", "stream-json"]


@pytest.mark.parametrize(
    ("stdout", "timed_out", "expected_state"),
    [
        ("", True, "safe_to_retry"),
        (
            json.dumps({"type": "tool_started", "tool_name": "mindmemos_add"}),
            True,
            "outcome_unknown",
        ),
        (json.dumps({"type": "result", "final_reply": "完成"}), False, "safe_to_retry"),
    ],
)
def test_write_run_classifies_unconfirmed_mutations(
    tmp_path: Path, stdout: str, timed_out: bool, expected_state: str
) -> None:
    paths = generate_run(base=tmp_path, run_id="run-test")

    def runner(command, *, cwd, env, timeout):
        return CompletedCommand(
            returncode=124 if timed_out else 0,
            stdout=stdout,
            stderr="failed",
            elapsed_seconds=10,
            timed_out=timed_out,
        )

    result = write_run(
        paths=paths,
        repo_root=tmp_path,
        timeout_seconds=10,
        max_records=1,
        runner=runner,
        terminal_verifier=lambda job_id, record: None,
    )

    assert result["state"] == expected_state
    assert result["confirmed"] == 0


def test_evaluate_search_events_scores_rank_and_step_order() -> None:
    record = build_dataset("run-test")[0]
    wrong = build_dataset("run-test")[1]
    stdout = "\n".join(
        (
            json.dumps(
                {
                    "type": "tool_started",
                    "tool_name": "mindmemos_search",
                    "tool_input": {"query": record.website},
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "tool_completed",
                    "tool_name": "mindmemos_search",
                    "output": json.dumps(
                        {
                            "success": True,
                            "records": [
                                {"memory_id": "wrong", "record": wrong.tool_record},
                                {"memory_id": "expected", "record": record.tool_record},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "result",
                    "final_reply": "；".join(record.value["steps"]),
                },
                ensure_ascii=False,
            ),
        )
    )

    score = evaluate_search_events(
        parse_stream_events(stdout), expected=record, expected_memory_id="expected"
    )

    assert score["rank"] == 2
    assert score["recall_at_1"] is False
    assert score["recall_at_5"] is True
    assert score["reciprocal_rank"] == 0.5
    assert score["exact_record"] is True
    assert score["final_answer_steps_in_order"] is True
    assert score["called_search_memories"] is True
    assert score["incorrect_environment_tool"] is False


def test_evaluation_case_distribution_and_natural_sampling() -> None:
    cases = evaluation_cases(build_dataset("run-test"))

    assert sum(case.suite == "exact" for case in cases) == 100
    assert sum(case.suite == "paraphrase" for case in cases) == 70
    assert sum(case.suite == "distractor" for case in cases) == 20
    assert sum(case.suite == "natural" for case in cases) == 30
    assert all("mindmemos_search" not in case.prompt for case in cases if case.suite == "natural")


def test_status_never_exposes_cleanup(tmp_path: Path) -> None:
    paths = generate_run(base=tmp_path, run_id="run-test")
    status = status_run(paths)

    assert status["records"] == 100
    assert status["confirmed_total"] == 0
    assert status["cleanup_available"] is False
