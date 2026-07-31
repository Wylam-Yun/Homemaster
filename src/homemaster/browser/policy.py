"""Injected policy for the phase-one trusted browser capability."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from homemaster.browser.contracts import BrowserSessionError


@dataclass(frozen=True)
class BrowserPolicy:
    allowed_origins: tuple[str, ...]
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 20_000
    wait_timeout_ms: int = 10_000
    max_elements: int = 120
    max_text_chars: int = 12_000

    def __post_init__(self) -> None:
        normalized = tuple(_origin(value) for value in self.allowed_origins)
        if not normalized:
            raise ValueError("allowed_origins must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_origins must be unique")
        object.__setattr__(self, "allowed_origins", normalized)
        for name in ("action_timeout_ms", "navigation_timeout_ms", "wait_timeout_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= self.max_elements <= 500:
            raise ValueError("max_elements must be between 1 and 500")
        if not 256 <= self.max_text_chars <= 100_000:
            raise ValueError("max_text_chars must be between 256 and 100000")

    def validate_initial_url(self, url: str) -> str:
        try:
            origin = _origin(url)
        except ValueError as exc:
            raise BrowserSessionError("url_not_allowed", str(exc)) from exc
        if origin not in self.allowed_origins:
            raise BrowserSessionError(
                "origin_not_allowed",
                f"origin {origin!r} is not allowed for this run",
                details={"origin": origin, "allowed_origins": list(self.allowed_origins)},
            )
        return url

    def validate_final_url(self, url: str) -> str:
        try:
            origin = _origin(url)
        except ValueError as exc:
            raise BrowserSessionError(
                "origin_not_allowed", str(exc), backend_attempted=True
            ) from exc
        if origin not in self.allowed_origins:
            raise BrowserSessionError(
                "origin_not_allowed",
                f"final origin {origin!r} is not allowed for this run",
                details={"origin": origin, "allowed_origins": list(self.allowed_origins)},
                backend_attempted=True,
            )
        return url


def _origin(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL or origin must be a non-empty string")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only absolute http/https URLs are allowed")
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


__all__ = ["BrowserPolicy"]
