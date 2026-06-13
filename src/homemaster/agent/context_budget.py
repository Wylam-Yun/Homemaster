"""ContextBudget — token estimation and compaction threshold decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class BudgetDecision(Enum):
    NO_COMPACT = "no_compact"
    COMPACT = "compact"


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if "一" <= char <= "鿿")
    non_cjk = max(0, len(text) - cjk)
    return max(1, math.ceil(cjk / 2) + math.ceil(non_cjk / 4))


def estimate_json_tokens(value: object) -> int:
    import json

    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int
    max_output_tokens: int
    threshold_ratio: float = 0.50
    recent_tail_ratio: float = 0.20
    safety_buffer_tokens: int = 13_000
    token_estimation_padding: float = 4 / 3
    image_token_estimate: int = 4096

    @property
    def compaction_threshold_tokens(self) -> int:
        ratio_threshold = int(self.context_window_tokens * self.threshold_ratio)
        hard_cap = self.context_window_tokens - self.max_output_tokens - self.safety_buffer_tokens
        return max(1, min(ratio_threshold, hard_cap))

    @property
    def recent_tail_budget_tokens(self) -> int:
        return max(1, int(self.compaction_threshold_tokens * self.recent_tail_ratio))

    def padded(self, tokens: int) -> int:
        return int(tokens * self.token_estimation_padding)

    def should_compact(self, estimated_input_tokens: int) -> BudgetDecision:
        if estimated_input_tokens >= self.compaction_threshold_tokens:
            return BudgetDecision.COMPACT
        return BudgetDecision.NO_COMPACT
