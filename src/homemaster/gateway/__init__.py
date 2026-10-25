"""Gateway security and runtime foundations."""

from homemaster.gateway.auth import (
    AuthenticationError,
    BearerTokenAuthenticator,
    TokenPrincipal,
)
from homemaster.tools.contracts import AuthenticatedPrincipal

__all__ = [
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "BearerTokenAuthenticator",
    "TokenPrincipal",
]
