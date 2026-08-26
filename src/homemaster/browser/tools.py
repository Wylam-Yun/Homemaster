"""Compatibility imports for the model-facing browser tool registry.

Tool definitions live under ``homemaster.tools.browser`` so every public tool
has an isolated module. This module remains as a stable import path for existing
runtime integrations and third-party callers.
"""

from homemaster.tools.browser.registry import (
    build_browser_registered_tools,
    build_browser_run_registry,
)

__all__ = ["build_browser_registered_tools", "build_browser_run_registry"]
