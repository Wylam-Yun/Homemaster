"""Backward-compatibility shim — moved to homemaster.stages.executor.

Only re-exports live/runtime-safe symbols.  Stub providers for testing
have been moved to the test helpers package.
"""

from homemaster.stages.executor import (
    MINIMUM_MAX_STEPS,
    STEP_MULTIPLIER,
    Stage05ExecutionError,
    Stage05ExecutionResult,
    StepDecisionProvider,
    execute_stage_05_plan,
)

__all__ = [
    "MINIMUM_MAX_STEPS",
    "STEP_MULTIPLIER",
    "Stage05ExecutionError",
    "Stage05ExecutionResult",
    "StepDecisionProvider",
    "execute_stage_05_plan",
]
