"""Definition for browser_focus."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_focus",
        operation="focus",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Focus one exact element without clicking or entering text. Use this before keyboard "
            "shortcuts or when the page behavior depends on focus; use `browser_type` when text "
            "entry is the goal. Success verifies the active element or equivalent focus state "
            "points to the resolved target."
        ),
        schema=target_object(),
    )
