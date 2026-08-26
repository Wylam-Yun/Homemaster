"""Definition for browser_analyze."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_analyze",
        operation="analyze",
        effects=("browser.navigate",),
        capabilities=("device.control",),
        proof=ExecutionProof.NONE,
        description=(
            "Analyze an unfamiliar page and return evidence-backed signals about rendering pattern, "
            "known anti-bot challenges, likely real-data API responses, and the next browser "
            "observation to try. Use this for diagnosis when ordinary inspect, extract, or network "
            "observation cannot explain an empty, blocked, or dynamically loaded page. It does not "
            "bypass anti-bot checks, choose a site-specific adapter, or modify the page beyond the "
            "requested navigation."
        ),
        schema=object_schema(
            {
                "url": {
                    "type": "string",
                    "description": "Optional policy-allowed absolute URL to analyze before settling.",
                },
                "settle_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Bounded settle period before diagnosis.",
                },
                "network_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Network observation budget.",
                },
            }
        ),
    )
