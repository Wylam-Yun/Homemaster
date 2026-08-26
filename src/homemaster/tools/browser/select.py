"""Definition for browser_select."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    schema = target_object(
        {
            "option": {"type": "string", "description": "Exact option label or value."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Ordered options for a supported multi-select control.",
            },
            "match": {
                "type": "string",
                "enum": ["label", "value"],
                "description": "Interpret each option as an exact visible label or DOM value.",
            },
        },
        required=("target",),
    )
    schema["oneOf"] = [{"required": ["option"]}, {"required": ["options"]}]
    return registered(
        session=session,
        name="browser_select",
        operation="select",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Select one exact option in a native select or a supported ARIA combobox/listbox and "
            "verify the selected value and visible label. Use this instead of manually clicking a "
            "dropdown and option when inspect/find reports a supported compound control. Supply an "
            "exact option label or value; ambiguous, disabled, missing, or unsupported custom-widget "
            "options fail without guessing."
        ),
        schema=schema,
    )
