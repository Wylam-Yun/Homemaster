"""AgentSession — message container for a multi-turn agent conversation."""

from __future__ import annotations

from homemaster.agent.messages import Message


class AgentSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def append(self, message: Message) -> None:
        self._messages.append(message)

    def clear(self) -> None:
        self._messages.clear()
