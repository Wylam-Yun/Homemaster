"""Base channel boundary adapted from OpenHarness channels.impl.base."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import DeliveryReceipt, OutboundMessage


class ChannelDeliveryError(RuntimeError):
    def __init__(self, receipt: DeliveryReceipt) -> None:
        super().__init__(
            f"{receipt.operation} delivery failed with status={receipt.status.value} "
            f"code={receipt.api_code!r}"
        )
        self.receipt = receipt


class BaseChannel(ABC):
    name = "base"

    def __init__(self, bus: BoundedPriorityBus) -> None:
        self.bus = bus
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> DeliveryReceipt: ...


__all__ = ["BaseChannel", "ChannelDeliveryError"]
