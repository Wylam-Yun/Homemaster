"""Definition for the separately authorized browser_eval tool."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_eval",
        operation="eval",
        effects=("browser.eval",),
        capabilities=("browser.eval",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        description=(
            "Execute JavaScript in the page context of one policy-authorized tab/frame and return a "
            "bounded structured result. Use this gated escape hatch only when the typed browser "
            "tools cannot observe or operate a required general web behavior, and state the expected "
            "external postcondition. Do not use it as a shortcut for find, read, extract, click, "
            "fill, or network. JavaScript has the page's same-origin authority and can mutate state, "
            "access page-visible credentials/storage, or initiate requests, so grant this tool only "
            "to a run trusted with that authority."
        ),
        schema=object_schema(
            {
                "script": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Page-context JavaScript.",
                },
                "arguments": {"description": "JSON-serializable script arguments."},
                "tab_ref": {"type": "string", "description": "Run-owned tab ref."},
                "frame_ref": {"type": "string", "description": "Verified frame ref."},
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Execution timeout.",
                },
                "max_result_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Bounded result size.",
                },
                "expected_postcondition": {
                    "type": "string",
                    "description": "Required external postcondition, or none for read-only eval.",
                },
            },
            ("script", "expected_postcondition"),
        ),
    )
