from __future__ import annotations

import textwrap

import pytest

from homemaster.skills._frontmatter import parse_skill_metadata


def test_frontmatter_supports_folded_literal_boolean_and_colons() -> None:
    parsed = parse_skill_metadata(
        "fallback",
        textwrap.dedent(
            """\
            ---
            name: deploy
            description: >
              Deploy safely across
              all environments.
            enabled: true
            note: |
              first: value
              second: value
            ---
            body
            """
        ),
    )

    assert parsed["description"] == "Deploy safely across all environments."
    assert parsed["frontmatter"]["enabled"] is True
    assert "first: value\n" in parsed["frontmatter"]["note"]


def test_frontmatter_rejects_invalid_yaml() -> None:
    with pytest.raises(ValueError, match="invalid SKILL.md YAML"):
        parse_skill_metadata("bad", "---\nname: [broken\n---\nbody")


def test_frontmatter_body_fallback_does_not_treat_metadata_as_description() -> None:
    parsed = parse_skill_metadata("fallback", "---\nname: demo\n---\n# Demo\nBody text.\n")
    assert parsed["name"] == "demo"
    assert parsed["description"] == "Body text."
