"""Definition for browser_tabs."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    schema = object_schema(
        {
            "action": {
                "type": "string",
                "enum": ["list", "new", "select", "close"],
                "description": "Tab operation.",
            },
            "tab_ref": {"type": "string", "description": "Run-owned tab ref for select/close."},
            "url": {"type": "string", "description": "Absolute allowed URL for a new tab."},
        },
        ("action",),
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"action": {"enum": ["select", "close"]}}},
            "then": {"required": ["tab_ref"]},
        }
    ]
    return registered(
        session=session,
        name="browser_tabs",
        operation="tabs",
        effects=("browser.navigate",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "List, create, select, or close tabs owned by the current HomeMaster browser session. "
            "Use this when work must continue in a separate tab or a click opened a popup; use "
            "`browser_navigate` to change the URL in the active tab. Success returns the complete "
            "run-owned tab list with stable tab refs, URL, title, and the active tab."
        ),
        schema=schema,
    )
