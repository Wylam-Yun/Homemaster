"""Definition for browser_upload."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_upload",
        operation="upload",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Attach one or more approved artifacts to an exact file input and verify the "
            "page-visible file state. Use this for ordinary file upload; it reads only "
            "artifact-store references and never opens an uncontrolled native file chooser or "
            "accepts an arbitrary server path. Use `browser_backfill` when the required source is "
            "a fresh screenshot of the current page pasted through the clipboard protocol."
        ),
        schema=target_object(
            {
                "artifact_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Approved artifact references, never uncontrolled paths.",
                }
            },
            required=("target", "artifact_refs"),
        ),
    )
