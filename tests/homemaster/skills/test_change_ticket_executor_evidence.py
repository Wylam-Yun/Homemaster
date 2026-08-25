from pathlib import Path


def test_structured_evidence_drawer_can_replace_image_backfill() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "browser_backfill is required only" in skill
    assert "EvidenceDrawer" in skill
    assert "Do not require browser_backfill" in skill


def test_missing_semantic_control_stops_without_terminal_browser_fallback() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Stop the run when a required page control is not exposed" in skill
    for forbidden_fallback in (
        "terminal",
        "raw JavaScript",
        "CDP",
        "alternate Playwright",
    ):
        assert forbidden_fallback in skill


def test_browser_inspect_never_receives_snapshot_or_element_ids() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`browser_inspect` never accepts `snapshot_id` or `element_id`" in skill
    assert "supported filters and `limit`" in skill
    assert "action tools such as `browser_click`, `browser_select`, and `browser_fill`" in skill
    assert "`element_id` values are local to one `snapshot_id`" in skill
    assert "copy both values from the same `browser_inspect` result" in skill
    assert "never combine a new `snapshot_id` with an `element_id` from an older result" in skill
    assert "confirm `enabled=true` and `obscured=false`" in skill
    assert "wait or inspect again instead of calling an action tool" in skill
    assert "`next_snapshot` captured after the action" in skill
    assert "Treat `next_snapshot` as review context only" in skill
    assert "Before every browser write, call `browser_inspect`" in skill
    assert "immediately preceding inspection" in skill
    assert "call `browser_inspect` without `snapshot_id` or `element_id`" in skill
    assert "use that matching pair directly for the next action" not in skill
    assert "Never reuse the consumed input snapshot" in skill


def test_browser_navigate_uses_only_absolute_http_urls() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`browser_navigate` accepts only an absolute `http://` or `https://` URL" in skill
    assert "Do not pass relative paths such as `/`" in skill
    assert "If the browser already starts on the required page, inspect it directly" in skill


def test_browser_wait_keeps_timeout_inside_condition() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`browser_wait` accepts exactly one top-level argument: `condition`" in skill
    assert "put `timeout_ms` inside the `condition` object" in skill
    assert "never pass `timeout_ms` beside `condition`" in skill


def test_write_actions_are_observed_by_runtime_without_model_scheduling() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "runtime automatically captures a review image" in skill
    assert "attached to that action's tool result" in skill
    assert "You may call `observe` yourself" in skill
    assert "semantic text and controls are insufficient" in skill
    assert "no actionable element reference" in skill
    assert "call `browser_inspect` before any interaction" in skill
    assert "Do not immediately duplicate the automatic image" in skill
    assert "Call `observe` after the important action" not in skill
    assert "call `observe` after confirmation" not in skill


def test_task_todo_updates_do_not_break_the_inspect_write_pair() -> None:
    skill = Path("src/homemaster/skills/builtin/change-ticket-executor/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "must be the only tool call in that model response" in skill
    assert "`task_progress_check` updates selected TODO items" in skill
    assert "It does not determine the next tool" in skill
    assert "never place task-state bookkeeping between an inspection" in skill
    assert "call `task_progress_check` alone to" in skill
    assert "Only after its result returns may you issue the browser write" not in skill
