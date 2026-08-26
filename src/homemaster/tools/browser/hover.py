"""Definition for browser_hover."""

from homemaster.tools.browser._common import (
    CONDITION_SCHEMA,
    ExecutionProof,
    registered,
    target_object,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_hover",
        operation="hover",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Move the browser pointer over one exact target to reveal hover-dependent UI such as a "
            "tooltip, menu, or action region. Use this only when hover itself is required; it does "
            "not click the target. Success verifies the target remains hovered and reports any "
            "requested visible postcondition."
        ),
        schema=target_object(
            {
                "duration_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Bounded hover dwell.",
                },
                "expect": CONDITION_SCHEMA,
            }
        ),
    )
