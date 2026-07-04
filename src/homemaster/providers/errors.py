"""Provider client error hierarchy."""

from __future__ import annotations


class LLMClientError(RuntimeError):
    """Base provider client error with a stable category."""

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        raw_content: str | None = None,
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.raw_content = raw_content
        super().__init__(message)


class LLMAuthError(LLMClientError):
    """Authentication failed for one provider key."""


class LLMRateLimitError(LLMClientError):
    """Provider rejected the request due to rate limiting."""


class LLMNetworkError(LLMClientError):
    """Provider request failed due to a network error."""


class LLMProviderError(LLMClientError):
    """Provider returned an invalid or unsuccessful response."""
