"""HomeMaster ↔ AgentScope adaptation boundary.

This package is the ONLY place in HomeMaster that may import ``agentscope``.
Everything outside must consume canonical HomeMaster contracts
(``homemaster.agent.messages``, ``homemaster.tools.contracts``).
"""

from homemaster.substrate.as_llm_client import AsLLMClient
from homemaster.substrate.messages import (
    MessageConversionError,
    assert_semantic_equal,
    from_agent_scope,
    to_agent_scope,
)
from homemaster.substrate.models import chat_model_from_profile
from homemaster.substrate.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    ParsedSnapshot,
    build_snapshot_payload,
    parse_snapshot_payload,
)
from homemaster.substrate.toolkit import (
    HomeToolAdapter,
    RunScope,
    RunScopeMiddleware,
    current_tool_call_id,
    result_to_chunk,
)

__all__ = [
    "AsLLMClient",
    "HomeToolAdapter",
    "MessageConversionError",
    "ParsedSnapshot",
    "RunScope",
    "SNAPSHOT_SCHEMA_VERSION",
    "RunScopeMiddleware",
    "build_snapshot_payload",
    "parse_snapshot_payload",
    "assert_semantic_equal",
    "chat_model_from_profile",
    "current_tool_call_id",
    "from_agent_scope",
    "result_to_chunk",
    "to_agent_scope",
]
