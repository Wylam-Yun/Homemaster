from __future__ import annotations

from homemaster.token_budget import initial_max_tokens, max_tokens_for_attempt


def test_token_budget_initial_values() -> None:
    assert initial_max_tokens("agent_response") == 4096
    assert initial_max_tokens("tool_task_interpreter") == 4096
    assert initial_max_tokens("tool_memory_query") == 4096
    assert initial_max_tokens("tool_task_summarizer") == 8192

    assert max_tokens_for_attempt(4096, 1) == 4096
    assert max_tokens_for_attempt(4096, 2) == 8192
    assert max_tokens_for_attempt(4096, 3) == 16384
