"""Definition for browser_drag."""

from homemaster.tools.browser._common import (
    CONDITION_SCHEMA,
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_drag",
        operation="drag",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Drag one exact DOM target to another exact target and verify the resulting order, "
            "position, or page state. Use this for sortable lists, boards, sliders with DOM handles, "
            "and drop zones; do not use it for file upload when `browser_upload` applies. Both source "
            "and destination must resolve uniquely and pass visibility and hit-testing checks."
        ),
        schema=object_schema(
            {
                "source": TARGET_SCHEMA,
                "destination": TARGET_SCHEMA,
                "expect": CONDITION_SCHEMA,
            },
            ("source", "destination"),
        ),
    )
