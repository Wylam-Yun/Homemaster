"""Definition for browser_console."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_console",
        operation="console",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Read recent browser console messages as bounded structured records. Use this to "
            "diagnose page errors, warnings, failed scripts, or application logs; it is not a "
            "substitute for DOM state or network responses. Filter by level or time window and "
            "continue with the returned cursor instead of requesting an unbounded live stream. "
            "This tool never executes console JavaScript."
        ),
        schema=object_schema(
            {
                "level": {
                    "type": "string",
                    "enum": ["all", "error", "warning", "log", "info", "debug"],
                    "description": "Console level filter.",
                },
                "since_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Lower timestamp bound.",
                },
                "until_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Upper timestamp bound in milliseconds.",
                },
                "cursor": {
                    "type": "string",
                    "description": "Cursor returned by the previous read.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Record limit.",
                },
            }
        ),
    )
