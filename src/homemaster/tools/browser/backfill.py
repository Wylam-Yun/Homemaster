"""Definition for browser_backfill."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_backfill",
        operation="backfill",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Capture the current browser page as a PNG and paste those exact image bytes into one "
            "editable image-backfill control that explicitly accepts clipboard images. Use this "
            "only when the workflow asks you to place a fresh screenshot of the current page into "
            "such a control. Use `browser_screenshot` when you only need to inspect or preserve an "
            "image, and use `browser_upload` for an existing file artifact. Success requires the "
            "target preview/content to match the pasted PNG bytes and reports both source and "
            "rendered SHA-256 values."
        ),
        schema=target_object(
            {
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full page instead of viewport, within policy bounds.",
                },
                "width": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4096,
                    "description": "Optional bounded screenshot viewport width in pixels.",
                },
                "height": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4096,
                    "description": "Optional bounded viewport height; ignored for full-page capture.",
                },
            }
        ),
    )
