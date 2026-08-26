"""Definition for browser_uncheck."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_uncheck",
        operation="uncheck",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Ensure one checkbox, switch, or supported aria-checked control ends unselected. Use "
            "this idempotent state action instead of clicking and guessing: an already unselected "
            "target succeeds with `changed=false`. Radio controls are rejected because a selected "
            "radio cannot be cleared independently in the normal group model."
        ),
        schema=target_object(),
    )
