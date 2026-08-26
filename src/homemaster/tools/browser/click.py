"""Definition for browser_click."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_click",
        operation="click",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Click one exact button, link, tab, option, date cell, or other non-editing target and "
            "verify the resulting browser or element state. Use a more specific tool for text entry, "
            "selection, checking, upload, drag, dialog, download, or image backfill. Set "
            "`click_count=2` for a double click; do not issue two separate clicks. Ambiguous, "
            "disabled, or obscured targets fail without choosing the first match."
        ),
        schema=target_object(
            {
                "button": {
                    "type": "string",
                    "enum": ["left", "middle", "right"],
                    "description": "Pointer button.",
                },
                "click_count": {
                    "type": "integer",
                    "enum": [1, 2],
                    "description": "One click or one double-click action.",
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keyboard modifiers held during the click.",
                },
            }
        ),
    )
