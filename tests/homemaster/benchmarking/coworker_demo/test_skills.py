from __future__ import annotations

from homemaster.benchmarking.coworker_demo.skills import load_coworker_skills


def test_only_two_generic_skills_are_loaded_without_answers() -> None:
    registry = load_coworker_skills()
    assert registry.all_names() == ["change_execution", "evidence_discipline"]
    text = "\n".join(spec.content for spec in registry.all())
    forbidden = (
        "tenanttenanttenant000198",
        "ext.read.type1",
        "read-ext",
        "post_change_anomaly",
        "PRE_ALARM",
        "A-9001201-metric-delay",
        "api_key",
    )
    for value in forbidden:
        assert value not in text
