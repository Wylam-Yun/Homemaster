"""Definition for browser_fill."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_fill",
        operation="fill",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Set one editable input, textarea, contenteditable, or supported date/time control to "
            "an exact value and verify the final DOM value. Use this when the final value matters "
            "and existing content should be replaced without simulating every keystroke. Use "
            "`browser_type` when the page must receive real key/input events or when text should be "
            "appended. Readonly, format-incompatible, or non-editable targets fail explicitly."
        ),
        schema=target_object(
            {
                "value": {
                    "type": "string",
                    "description": "Complete final value to write and read back.",
                }
            },
            required=("target", "value"),
        ),
    )
