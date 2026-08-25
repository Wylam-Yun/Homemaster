from homemaster.prompts.loader import PromptId, load_prompt


def test_browser_gateway_prompt_is_independent_and_requires_review_images() -> None:
    prompt = load_prompt(PromptId.BROWSER_GATEWAY)

    assert "browser_inspect" in prompt
    assert "browser_backfill" in prompt
    assert "snapshot_id" in prompt
    assert "load_skill" in prompt
    assert "You may call `observe`" in prompt
    assert "semantic text and controls are insufficient" in prompt
    assert "returns no actionable" in prompt
    assert "call `browser_inspect` before any interaction" in prompt
    assert "review" in prompt.casefold()
    assert "Mock UI" in prompt
    assert "automatically captures one review image" in prompt
    assert "`browser_navigate` are read-only and do not" in prompt
    assert "must be the only tool call in its model response" in prompt
    assert "Call `task_progress_check` in a separate model response" in prompt
    assert "browser_inspect accepts filters only" in prompt
    assert "never pass snapshot_id or element_id to it" in prompt
    assert "Before every browser write or interaction, call browser_inspect" in prompt
    assert "Treat next_snapshot as review context only" in prompt
    assert "action directly" not in prompt
    assert "call observe exactly once" not in prompt
    for case_specific in (
        "svc_cfg_cli_runner",
        "TenantId",
        "ItemCode",
        "SpecCode",
        "ExtensionName",
    ):
        assert case_specific not in prompt
