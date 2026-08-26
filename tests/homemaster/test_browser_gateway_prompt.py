from homemaster.prompts.loader import PromptId, load_prompt


def test_browser_gateway_prompt_is_independent_and_requires_review_images() -> None:
    prompt = load_prompt(PromptId.BROWSER_GATEWAY)

    assert "browser_inspect" in prompt
    assert "browser_backfill" in prompt
    assert "target_ref" in prompt
    assert "load_skill" in prompt
    assert "browser_screenshot" in prompt
    assert "`browser_eval` is absent by default" in prompt
    assert "fresh screenshot of the" in prompt
    assert "current browser page" in prompt
    assert "stop and report that evidence" in prompt
    assert "terminal command, raw" in prompt
    assert "CDP, coordinates, or a second browser session" in prompt
    assert "must be the only tool call in its model response" in prompt
    assert "independently read the external terminal state" in prompt
    assert "snapshot_id" not in prompt
    assert "element_id" not in prompt
    assert "`observe`" not in prompt
    assert "call observe exactly once" not in prompt
    for case_specific in (
        "svc_cfg_cli_runner",
        "TenantId",
        "ItemCode",
        "SpecCode",
        "ExtensionName",
    ):
        assert case_specific not in prompt
