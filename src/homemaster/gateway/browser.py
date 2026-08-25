"""Compatibility imports for the channel-independent browser binding."""

from __future__ import annotations

from homemaster.browser.application import BrowserApplication, create_browser_application

BrowserGatewayApplication = BrowserApplication
create_browser_gateway_application = create_browser_application


__all__ = ["BrowserGatewayApplication", "create_browser_gateway_application"]
