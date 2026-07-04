"""Provider transport interface.

Transports convert between HomeMaster normalized messages and provider SDK
request/response shapes. They do not own SDK clients and do not perform HTTP.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from homemaster.agent.messages import AssistantMessage, Message
from homemaster.providers.transports.types import TransportDelta


class ProviderTransport(ABC):
    """Pure protocol conversion for one provider API format."""

    @abstractmethod
    def build_create_kwargs(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Build kwargs accepted by the selected SDK create method."""

    @abstractmethod
    def normalize_response(self, response: Any) -> AssistantMessage:
        """Normalize a non-streaming SDK response."""

    @abstractmethod
    def iter_stream_deltas(self, stream: Any) -> Iterator[TransportDelta]:
        """Convert provider SDK stream events into normalized deltas."""
