"""Definition for browser_navigate."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_navigate",
        operation="navigate",
        effects=("browser.navigate",),
        capabilities=("device.control", "network.http"),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        description=(
            "Open one policy-allowed absolute HTTP(S) URL in the current browser tab. Use this "
            "when the required page is not already open; use `browser_history` for back, forward, "
            "or reload and `browser_tabs` when a separate tab is required. Success reports the "
            "requested and final URL, title, response status when observable, redirects, active "
            "tab, page generation, and evidence; it does not by itself select an element for a "
            "later action."
        ),
        schema=object_schema(
            {
                "url": {
                    "type": "string",
                    "description": "Absolute http:// or https:// URL allowed by browser policy.",
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["domcontentloaded", "load", "networkidle"],
                    "description": "Playwright load fence.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Bounded navigation timeout in milliseconds.",
                },
            },
            ("url",),
        ),
    )
