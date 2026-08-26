"""Definition for browser_dialog."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_dialog",
        operation="dialog",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Accept or dismiss a JavaScript alert, confirm, or prompt and report its type and "
            "message. When an action is expected to open a blocking dialog, provide that action as "
            "`trigger` so HomeMaster arms the dialog listener before executing it; otherwise handle "
            "the currently captured pending dialog. Use `prompt_text` only when accepting a prompt. "
            "Success means the exact dialog was handled, not merely that a listener was installed."
        ),
        schema=object_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["accept", "dismiss"],
                    "description": "Dialog action.",
                },
                "prompt_text": {
                    "type": "string",
                    "description": "Prompt value for accept on a prompt dialog.",
                },
                "trigger": TARGET_SCHEMA,
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Bounded timeout for a trigger-created dialog in milliseconds.",
                },
            },
            ("action",),
        ),
    )
