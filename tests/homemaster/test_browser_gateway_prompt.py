from homemaster.prompts.loader import PromptId, load_prompt


def test_browser_gateway_prompt_is_independent_and_requires_review_images() -> None:
    prompt = load_prompt(PromptId.BROWSER_GATEWAY)

    assert "browser_inspect" in prompt
    assert "browser_backfill" in prompt
    assert "snapshot_id" in prompt
    assert "load_skill" in prompt
    assert "observe" in prompt
    assert "review" in prompt.casefold()
    assert "Mock UI" in prompt
    for case_specific in (
        "svc_cfg_cli_runner",
        "TenantId",
        "ItemCode",
        "SpecCode",
        "ExtensionName",
    ):
        assert case_specific not in prompt
