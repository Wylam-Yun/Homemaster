"""``events.trace.json_compatible_copy`` coverage salvaged from the retired
legacy ``test_llm_client.py`` — the helper is still used by trace sinks and
must pass secret-shaped fields through verbatim (V2.0 exactness)."""

import json

from homemaster.events.trace import json_compatible_copy


def test_json_compatible_copy_preserves_secret_shaped_fields() -> None:
    copied = json_compatible_copy(
        {
            "api_key": "secret-one",
            "headers": {
                "Authorization": "Bearer secret-one",
                "x-api-key": "secret-one",
            },
            "safe": "visible",
        }
    )
    encoded = json.dumps(copied, ensure_ascii=False)

    assert encoded.count("secret-one") == 3
    assert copied["safe"] == "visible"
