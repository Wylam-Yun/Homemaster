from __future__ import annotations

from mindmemos.components.extractor.schema.schema_normalizer import (
    SchemaExtractionNormalizer,
)


class FakeEntityManager:
    def list_types(self) -> list[str]:
        return [
            "fact",
            "object_location",
            "search_observation",
            "task_procedure",
            "task_experience",
            "episodes",
        ]

    def get_all_dicts(self) -> list[dict]:
        return [
            {
                "entity_type": entity_type,
                "dynamic_property": {
                    "episode_record": {"type": "string"},
                    "default_property": {"type": "string"},
                },
            }
            for entity_type in self.list_types()
        ]


def _raw(entity_type: str) -> dict:
    return {
        "entities": [
            {
                "name": "candidate",
                "entity_type": entity_type,
                "properties": [
                    {"property_name": "episode_record", "value": "{}"}
                ],
            }
        ],
        "edges": [],
    }


def test_fixed_schema_validation_accepts_only_the_requested_entity_type() -> None:
    normalizer = SchemaExtractionNormalizer(entity_manager=FakeEntityManager())

    assert (
        normalizer.validate(
            _raw("object_location"), fixed_entity_type="object_location"
        )
        is None
    )
    assert normalizer.validate(
        _raw("search_observation"), fixed_entity_type="object_location"
    ) == "Fixed extractor object_location emitted entity type search_observation"


def test_fixed_schema_validation_rejects_unknown_type_without_fallback() -> None:
    normalizer = SchemaExtractionNormalizer(entity_manager=FakeEntityManager())
    raw = _raw("unknown_domain_type")

    error = normalizer.validate(raw, fixed_entity_type="object_location")

    assert error == "Fixed extractor object_location emitted entity type unknown_domain_type"
    assert raw["entities"][0]["entity_type"] == "unknown_domain_type"


def test_legacy_validation_keeps_existing_unknown_type_fallback() -> None:
    normalizer = SchemaExtractionNormalizer(entity_manager=FakeEntityManager())
    raw = _raw("unknown_domain_type")

    assert normalizer.validate(raw) is None
    assert raw["entities"][0]["entity_type"] == "fact"
