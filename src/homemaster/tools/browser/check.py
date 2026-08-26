"""Definition for browser_check."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_check",
        operation="check",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Ensure one checkbox, switch, radio, or supported aria-checked control ends selected. "
            "Use this idempotent state action instead of clicking and guessing: an already selected "
            "target succeeds with `changed=false`. The receipt verifies checked, aria-checked, or "
            "selected state on the exact resolved control."
        ),
        schema=target_object(),
    )
