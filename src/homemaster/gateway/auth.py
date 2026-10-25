"""Credential-to-principal trust boundary for future remote channels."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from homemaster.events.token_patterns import SHA256_RE as _SHA256_RE

# The principal DTO lives in the leaf contract module (tools.contracts) so
# channels can consume it without importing the gateway package; re-exported
# here for backward compatibility.
from homemaster.tools.contracts import AuthenticatedPrincipal


class AuthenticationError(RuntimeError):
    """Remote credentials did not resolve to one configured principal."""


@dataclass(frozen=True)
class TokenPrincipal:
    token_sha256: str
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        if _SHA256_RE.fullmatch(self.token_sha256) is None:
            raise ValueError("token credential must be a lowercase SHA-256 digest")
        if not isinstance(self.principal, AuthenticatedPrincipal):
            raise TypeError("token credential principal must be AuthenticatedPrincipal")

    @classmethod
    def from_token(cls, token: str, principal: AuthenticatedPrincipal) -> TokenPrincipal:
        if not isinstance(token, str) or not token:
            raise ValueError("token must be non-empty")
        return cls(hashlib.sha256(token.encode()).hexdigest(), principal)


class BearerTokenAuthenticator:
    """Resolve a Bearer credential without retaining its raw value."""

    def __init__(self, credentials: tuple[TokenPrincipal, ...]) -> None:
        by_digest: dict[str, AuthenticatedPrincipal] = {}
        for credential in credentials:
            if not isinstance(credential, TokenPrincipal):
                raise TypeError("credentials must contain TokenPrincipal values")
            if credential.token_sha256 in by_digest:
                raise ValueError("duplicate token credential digest")
            by_digest[credential.token_sha256] = credential.principal
        self._principals = by_digest

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        if not isinstance(authorization, str):
            raise AuthenticationError("missing bearer credential")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token.strip():
            raise AuthenticationError("invalid bearer credential")
        candidate = hashlib.sha256(token.strip().encode()).hexdigest()
        matched: AuthenticatedPrincipal | None = None
        for digest, principal in self._principals.items():
            if hmac.compare_digest(candidate, digest):
                matched = principal
        if matched is None:
            raise AuthenticationError("invalid bearer credential")
        return matched


__all__ = [
    "AuthenticatedPrincipal",
    "AuthenticationError",
    "BearerTokenAuthenticator",
    "TokenPrincipal",
]
