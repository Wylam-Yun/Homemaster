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
from homemaster.application.factory import create_application
from homemaster.application.resources import (
    ResourceCleanupError,
    ResourceHandle,
    ResourceScopeError,
    RunResourceScope,
)
from homemaster.application.runtime import (
    ApplicationRuntime,
    CompactionResult,
    SessionStatus,
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
    "ApplicationRuntime",
    "EnvironmentBackend",
    "CancellationSource",
    "CompactionRequest",
    "CompactionResult",
    "create_application",
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
    "SessionStatus",
    "TerminalPolicy",
]
