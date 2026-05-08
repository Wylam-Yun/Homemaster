"""HomeMaster pipeline framework sub-package.

Backward compatibility: re-export key symbols so that
  from homemaster.pipeline import PipelineContext, build_default_registry
continues to work.

NOTE: does NOT re-export stage_runtime to avoid namespace collision
with homemaster.runtime. Use full path:
  from homemaster.pipeline.stage_runtime import RuntimeMode
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
