from pathlib import Path


def _skill() -> str:
    content = Path(
        "src/homemaster/skills/builtin/change-ticket-executor/SKILL.md"
    ).read_text(
        encoding="utf-8",
    )
    return " ".join(content.split())


def test_ticket_and_ui_decide_structured_evidence_and_image_backfill_authority() -> None:
    skill = _skill()

    assert "`browser_backfill` is required only" in skill
    assert "structured EvidenceDrawer" in skill
    assert "do not require image backfill" in skill
    assert "not a substitute for `browser_screenshot` or `browser_upload`" in skill


def test_missing_semantic_control_stops_without_browser_bypass() -> None:
    skill = _skill()

    assert "stop and report the missing evidence" in skill
    for forbidden_fallback in (
        "terminal command",
        "raw JavaScript",
        "CDP",
        "coordinates",
        "second browser session",
    ):
        assert forbidden_fallback in skill
    assert "`browser_eval` is deliberately absent from this Skill" in skill


def test_semantic_targets_replace_the_v21_snapshot_write_pair() -> None:
    skill = _skill()

    assert "known unique semantic target" in skill
    assert "Use a returned `target_ref`" in skill
    assert "On `stale_ref`, `target_ambiguous`" in skill
    assert "inspect again or stop" in skill
    for legacy_protocol in (
        "snapshot_id",
        "element_id",
        "next_snapshot",
        "immediately preceding inspection",
        "Before every browser write",
        "`observe`",
    ):
        assert legacy_protocol not in skill


def test_browser_navigate_uses_policy_allowed_absolute_http_urls() -> None:
    skill = _skill()

    assert "absolute policy-allowed `http://` or `https://` URL" in skill
    assert "Do not navigate again when the browser is already on the required page" in skill


def test_browser_wait_is_bounded_and_does_not_claim_prior_success() -> None:
    skill = _skill()

    assert "Use `browser_wait` for one explicit bounded condition" in skill
    assert (
        "event listeners through the typed dialog/network/download flows before triggering" in skill
    )
    assert "A timeout never proves that a previous write succeeded" in skill


def test_write_actions_do_not_require_screenshots_for_routine_confirmation() -> None:
    skill = _skill()

    assert "Browser writes do not force an observe or screenshot round trip" in skill
    assert "This Skill imposes no mandatory screenshot checkpoints" in skill
    assert "Do not take routine confirmation screenshots" in skill
    assert "ticket explicitly requires image evidence" in skill
    assert "Screenshots do not grant action references" in skill
    assert "runtime captures `browser_screenshot` after write tools" not in skill
    assert "`observe`" not in skill


def test_task_progress_and_browser_writes_each_use_a_separate_model_response() -> None:
    skill = _skill()

    assert "Every browser write or interaction must be the sole tool call" in skill
    assert "`task_planner` and `task_progress_check` are bookkeeping tools" in skill
    assert "Call `task_progress_check` as the sole tool call" in skill
    assert "wait for its result before issuing a browser write" in skill
