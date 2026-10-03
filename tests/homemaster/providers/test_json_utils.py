"""Surviving-helper coverage salvaged from the retired legacy
``test_llm_client.py``: ``extract_json_payload`` still feeds
``AsLLMClient.complete_json``."""

from homemaster.providers.json_utils import extract_json_payload


def test_extract_json_payload_accepts_plain_json() -> None:
    payload = extract_json_payload('{"task_type": "check_presence", "target": "药盒"}')

    assert payload == {"task_type": "check_presence", "target": "药盒"}
