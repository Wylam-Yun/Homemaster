from __future__ import annotations

from homemaster.token_budget import initial_max_tokens, max_tokens_for_attempt


def test_stage_token_budget_starts_at_doubled_schedule_and_doubles_on_retry() -> None:
    assert initial_max_tokens("stage_02_task_card") == 4096
    assert initial_max_tokens("stage_03_memory_query") == 4096
    assert initial_max_tokens("stage_05_orchestration") == 16384
    assert initial_max_tokens("stage_05_step_decision") == 4096
    assert initial_max_tokens("stage_05_recovery") == 8192
    assert initial_max_tokens("stage_06_summary") == 16384

    assert max_tokens_for_attempt(4096, 1) == 4096
    assert max_tokens_for_attempt(4096, 2) == 8192
    assert max_tokens_for_attempt(4096, 3) == 16384
