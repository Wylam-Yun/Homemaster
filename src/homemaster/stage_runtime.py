"""Backward-compatibility shim — moved to homemaster.pipeline.stage_runtime.

Only re-exports live/runtime-safe symbols.  Deterministic/test-double providers
have been removed from production code.
"""

from homemaster.pipeline.stage_runtime import (
    ComponentMode,
    LiveStepDecisionProvider,
    RuntimeMode,
    ServiceCheckResult,
    model_boundary,
    run_stage02,
    run_stage03,
    run_stage05_plan,
    run_stage06_summary,
    validate_runtime_services,
)

__all__ = [
    "ComponentMode",
    "LiveStepDecisionProvider",
    "RuntimeMode",
    "ServiceCheckResult",
    "model_boundary",
    "run_stage02",
    "run_stage03",
    "run_stage05_plan",
    "run_stage06_summary",
    "validate_runtime_services",
]
