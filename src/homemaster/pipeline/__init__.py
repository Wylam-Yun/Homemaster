"""Pipeline — compatibility layer for the legacy stage loop.

This package is the legacy Stage02-06 pipeline runner. It is NOT the default
entrypoint — AgentRuntime (in agent/) is the default. The pipeline path is
activated via use_agent_runtime=False for backward compatibility.

Re-exports PipelineContext, StageRegistry, build_default_registry, and
Stage01-06 adapters for legacy import paths.
"""

from homemaster.pipeline.adapters import (
    Stage02Adapter,
    Stage03Adapter,
    Stage04Adapter,
    Stage05Adapter,
    Stage06Adapter,
)
from homemaster.pipeline.core import (
    PipelineContext,
    Stage,
    StageRegistry,
    build_default_registry,
)
from homemaster.pipeline.stage_01_smoke import (
    DEFAULT_STAGE_01_UTTERANCE,
    STAGE_01_RETRY_INSTRUCTION,
    Stage01SmokeError,
    Stage01SmokeResult,
    build_stage_01_task_card_prompt,
    run_stage_01_contract_smoke,
    validate_stage_01_task_card,
)

__all__ = [
    "DEFAULT_STAGE_01_UTTERANCE",
    "PipelineContext",
    "STAGE_01_RETRY_INSTRUCTION",
    "Stage",
    "Stage01SmokeError",
    "Stage01SmokeResult",
    "Stage02Adapter",
    "Stage03Adapter",
    "Stage04Adapter",
    "Stage05Adapter",
    "Stage06Adapter",
    "StageRegistry",
    "adapters",
    "build_default_registry",
    "build_stage_01_task_card_prompt",
    "core",
    "run_stage_01_contract_smoke",
    "validate_stage_01_task_card",
]
