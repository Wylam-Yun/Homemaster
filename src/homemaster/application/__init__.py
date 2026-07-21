"""Application contracts and run-scoped resource lifecycle."""

from homemaster.application.contracts import (
    EnvironmentBackend,
    ResourceBinding,
    ResourceLifetime,
    ResourceOwnership,
    RunPolicy,
    RunRequest,
    RunResult,
    RunStatus,
    TerminalPolicy,
)
from homemaster.application.resources import (
    ResourceCleanupError,
    ResourceHandle,
    ResourceScopeError,
    RunResourceScope,
)
from homemaster.application.session import (
    CancellationSource,
    CompactionRequest,
    SessionBackend,
    SessionConflictError,
    SessionError,
    SessionFileBackend,
    SessionGenerationError,
    SessionManager,
    SessionRuntime,
    SessionSnapshot,
)

__all__ = [
    "EnvironmentBackend",
    "CancellationSource",
    "CompactionRequest",
    "ResourceBinding",
    "ResourceCleanupError",
    "ResourceHandle",
    "ResourceLifetime",
    "ResourceOwnership",
    "ResourceScopeError",
    "RunPolicy",
    "RunRequest",
    "RunResourceScope",
    "RunResult",
    "RunStatus",
    "SessionConflictError",
    "SessionBackend",
    "SessionError",
    "SessionFileBackend",
    "SessionGenerationError",
    "SessionManager",
    "SessionRuntime",
    "SessionSnapshot",
    "TerminalPolicy",
]
