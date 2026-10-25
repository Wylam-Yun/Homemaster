"""Shared wiring handed to the per-domain route modules.

``create_web_app`` builds exactly one ``WebAppContext`` per app; every
module under ``homemaster.web.routes`` receives it so their handlers reach
the same runtime objects the old monolithic factory closed over. Optional
services (memory, compile jobs, provider config) are still read off
``app.state`` inside the handlers — they are injected per app and may be
``None`` on lower tiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.event_hub import WebEventHub
from homemaster.web.pending_questions import PendingQuestionRegistry
from homemaster.web.run_registry import WebRunRegistry


@dataclass
class WebAppContext:
    """Runtime objects shared by the Web Console route registrations."""

    application: Any
    confirmation_handler: WebConfirmationHandler
    run_registry: WebRunRegistry
    pending_questions: PendingQuestionRegistry
    hub: WebEventHub
    config: Any | None
    environment_label: str
    permission_store: Any | None = None

    def web_store(self) -> Any | None:
        """Resolve the permission store at request time.

        An explicit store wins; otherwise fall back to whatever store the
        confirmation handler is bound to — ``bind_store`` may run after
        this context is built, so the lookup must stay lazy.
        """

        if self.permission_store is not None:
            return self.permission_store
        return self.confirmation_handler.store


__all__ = ["WebAppContext"]
