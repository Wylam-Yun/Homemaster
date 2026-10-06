"""``map_sdk_error`` classification contract — raw transport exceptions must
land on the retryable network class, not the catch-all provider bucket."""

from __future__ import annotations

import httpx
import pytest

from homemaster.providers._shared import map_sdk_error
from homemaster.providers.errors import (
    LLMClientError,
    LLMNetworkError,
    LLMProviderError,
)


def _transport(exc_name: str, message: str) -> Exception:
    cls = getattr(httpx, exc_name)
    return cls(message, request=httpx.Request("POST", "https://provider.example/v1/messages"))


@pytest.mark.parametrize(
    "exc_name",
    [
        "RemoteProtocolError",
        "ReadError",
        "ConnectError",
        "WriteError",
        "CloseError",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
    ],
)
def test_httpx_transport_errors_map_to_transient_network(exc_name: str) -> None:
    mapped = map_sdk_error(_transport(exc_name, f"synthetic {exc_name}"))
    assert isinstance(mapped, LLMNetworkError)
    assert mapped.error_type == "network_error"
    assert mapped.cause_code == "transient_network"


def test_remote_protocol_error_matches_production_shape() -> None:
    exc = _transport(
        "RemoteProtocolError",
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)",
    )
    mapped = map_sdk_error(exc)
    assert isinstance(mapped, LLMNetworkError)


@pytest.mark.parametrize(
    "exc_name",
    ["LocalProtocolError", "ProxyError", "UnsupportedProtocol"],
)
def test_deterministic_transport_errors_stay_non_retryable(exc_name: str) -> None:
    """Config/client bugs inside the transport family must not be retried."""
    mapped = map_sdk_error(_transport(exc_name, f"synthetic {exc_name}"))
    assert isinstance(mapped, LLMProviderError)
    assert mapped.error_type == "provider_error"


def test_non_transport_errors_still_fall_through_to_provider_error() -> None:
    mapped = map_sdk_error(ValueError("totally broken payload"))
    assert isinstance(mapped, LLMProviderError)
    assert mapped.error_type == "provider_error"


def test_typed_client_errors_pass_through_unchanged() -> None:
    original = LLMClientError(
        error_type="no_keys", message="no API keys configured", cause_code="no_keys"
    )
    assert map_sdk_error(original) is original
