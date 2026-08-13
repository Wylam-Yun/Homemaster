from mindmemos.prompts import get_add_prompts
from mindmemos.prompts.ZH.add.vanilla import EXTRACTION_SYSTEM_PROMPT_ZH
from mindmemos.prompts.ZH.add.vanilla_entity import (
    EXTRACTION_SYSTEM_PROMPT_ENTITY_ZH,
)


def test_add_prompt_selector_keeps_english_and_chinese_prompts() -> None:
    en_prompts = get_add_prompts("EN")
    zh_prompts = get_add_prompts("ZH")

    assert "conversation analysis expert" in en_prompts.conv_boundary_detection
    assert "professional entity and relationship extraction expert" in en_prompts.entity_generation
    assert "higher-order personal traits" in en_prompts.higher_order_generation
    assert "memory property merge expert" in en_prompts.property_merge_decision
    assert "search optimization expert" in en_prompts.search_field_generation
    assert zh_prompts.conv_boundary_detection
    assert zh_prompts.entity_generation
    assert zh_prompts.higher_order_generation
    assert zh_prompts.property_merge_decision
    assert zh_prompts.search_field_generation
    assert zh_prompts.conv_boundary_detection != en_prompts.conv_boundary_detection


def test_chinese_add_prompts_aggregate_dependent_tool_operations() -> None:
    for prompt in (
        EXTRACTION_SYSTEM_PROMPT_ZH,
        EXTRACTION_SYSTEM_PROMPT_ENTITY_ZH,
    ):
        assert "先按任务或事件组织信息" in prompt
        assert "不得按单次工具调用拆分" in prompt
        assert "完整工具操作链" in prompt
        assert "单次普通工具调用通常只是任务证据" in prompt
        assert "同一因果链或验证链的候选合并" in prompt
        assert "通常只产生 1～3 条记忆" in prompt
