# -*- coding: utf-8 -*-
"""Middleware system for AgentScope agents.

HOMEMASTER PATCH (see src/agentscope/VENDORED_SOURCE.md): upstream eagerly
imported every middleware here, which transitively pulled the optional
``agentscope.tts``/``agentscope.classifier``/``mem0`` subpackages whenever
``agentscope.middleware`` was imported (e.g. just for ``MiddlewareBase``).
This version resolves the public names lazily via PEP 562 ``__getattr__``;
behaviour is identical for callers, but extras-gated subpackages are only
loaded when their middleware is actually requested.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._base import MiddlewareBase
    from ._budget import ReplyBudgetControlMiddleware
    from ._longterm_memory import (
        AgenticMemoryMiddleware,
        Mem0Middleware,
        ReMeMiddleware,
    )
    from ._model_router import ChatModelCandidate, ModelRouterMiddleware
    from ._rag import RAGMiddleware
    from ._tracing import TracingMiddleware
    from ._tts_middleware import TTSMiddleware

_LAZY: dict[str, str] = {
    "MiddlewareBase": "._base",
    "ReplyBudgetControlMiddleware": "._budget",
    "AgenticMemoryMiddleware": "._longterm_memory",
    "Mem0Middleware": "._longterm_memory",
    "ReMeMiddleware": "._longterm_memory",
    "ChatModelCandidate": "._model_router",
    "ModelRouterMiddleware": "._model_router",
    "RAGMiddleware": "._rag",
    "TracingMiddleware": "._tracing",
    "TTSMiddleware": "._tts_middleware",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    import importlib

    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "MiddlewareBase",
    "AgenticMemoryMiddleware",
    "Mem0Middleware",
    "ReMeMiddleware",
    "RAGMiddleware",
    "TracingMiddleware",
    "ReplyBudgetControlMiddleware",
    "TTSMiddleware",
    "ChatModelCandidate",
    "ModelRouterMiddleware",
]
