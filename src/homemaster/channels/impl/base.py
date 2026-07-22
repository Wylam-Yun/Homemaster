"""Base channel boundary adapted from OpenHarness channels.impl.base."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import OutboundMessage


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
    async def send(self, message: OutboundMessage) -> None: ...


__all__ = ["BaseChannel"]
