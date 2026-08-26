"""Definition for browser_screenshot."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_screenshot",
        operation="screenshot",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Take a PNG screenshot of the current browser page without changing it. Use this to "
            "inspect layout, images, charts, canvas content, visual obstruction, or controls whose "
            "DOM semantics are insufficient. Set `annotate_refs=true` to overlay visible target-ref "
            "labels that correspond to the returned ref map; otherwise the image alone grants no "
            "action reference. Use `browser_backfill` only when the current page screenshot must be "
            "pasted into an editable image-backfill control."
        ),
        schema=object_schema(
            {
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the full page within policy bounds.",
                },
                "width": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4096,
                    "description": "Optional viewport width bound.",
                },
                "height": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4096,
                    "description": "Optional viewport height bound; ignored for full_page.",
                },
                "annotate_refs": {
                    "type": "boolean",
                    "description": "Overlay visible target_ref labels and return the mapping.",
                },
                "frame_ref": {"type": "string", "description": "Optional frame identity."},
            }
        ),
    )
