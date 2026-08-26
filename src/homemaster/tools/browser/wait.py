"""Definition for browser_wait."""

from homemaster.tools.browser._common import (
    CONDITION_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_wait",
        operation="wait",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        description=(
            "Wait for one bounded browser condition and return the last observed state. Supported "
            "conditions include text, semantic or read-only CSS selector, fixed time, matching "
            "XHR/response, DOM stability, URL, element state, popup, dialog, or an already initiated "
            "download. A timeout means only that the condition was not reached; it never proves a "
            "preceding action succeeded and never authorizes an unrelated later action."
        ),
        schema=object_schema({"condition": CONDITION_SCHEMA}, ("condition",)),
    )
