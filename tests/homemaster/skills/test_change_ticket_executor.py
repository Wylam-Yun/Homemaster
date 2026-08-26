from __future__ import annotations

from homemaster.skills.loader import load_skill_registry


def test_change_ticket_executor_is_one_generic_model_visible_skill() -> None:
    registry = load_skill_registry(allow_project=False, user_dirs=())
    skill = registry.get_model_visible("change-ticket-executor")

    assert skill is not None
    assert "a change ticket" in skill.description.casefold()
    assert skill.disable_model_invocation is False
    assert "load_skill" in skill.content
    assert "browser_inspect" in skill.content
    assert "browser_backfill" in skill.content
    assert "target_ref" in skill.content
    assert "snapshot_id" not in skill.content
    assert "element_id" not in skill.content
    assert "required only when the ticket explicitly requires" in skill.content
    assert "terminal command, raw JavaScript" in skill.content
    assert "CDP, coordinates, or a second browser session" in skill.content
    assert "stop and report the missing evidence" in skill.content
    assert "rollback" in skill.content.casefold()


def test_change_ticket_executor_does_not_embed_case_specific_sop_or_gt() -> None:
    registry = load_skill_registry(allow_project=False, user_dirs=())
    content = registry.get("change-ticket-executor").content

    for forbidden in (
        "svc_cfg_cli_runner",
        "svc_usage_record_fetcher",
        "TenantId",
        "ItemCode",
        "SpecCode",
        "ExtensionName",
        "Case02",
        "TICKET_READ",
        "ADD_SUBMIT",
        "ROLLBACK_GREP",
    ):
        assert forbidden not in content
