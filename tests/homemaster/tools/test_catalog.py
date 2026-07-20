from __future__ import annotations

import pytest

from homemaster.tools.catalog import (
    CatalogOverrideAuthorization,
    ToolCatalog,
    ToolCatalogError,
)
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


class Executor:
    def __init__(self, label: str) -> None:
        self.label = label

    async def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, text=self.label)


def _tool(
    internal_id: str,
    alias: str,
    *,
    source: str,
    version: str = "1.0.0",
) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id=internal_id,
            model_alias=alias,
            description=f"{source} {alias}",
            input_schema={"type": "object"},
            output_schema={},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(
                source=source,
                reference=f"{source}.tools.{alias}",
            ),
            version=version,
        ),
        executor=Executor(source),
    )


def test_catalog_preserves_registration_order_and_queries_provenance() -> None:
    catalog = ToolCatalog()
    home = _tool("home.observe.v1", "observe", source="home")
    alfworld = _tool("alfworld.observe.v1", "observe", source="alfworld")
    coworker = _tool("coworker.click.v1", "click", source="coworker")
    for tool in (home, alfworld, coworker):
        catalog.register(tool)

    assert catalog.list_tools() == (home, alfworld, coworker)
    assert catalog.get("alfworld.observe.v1") is alfworld
    assert catalog.get("missing.tool.v1") is None
    assert catalog.definitions(source="home") == (home.definition,)
    assert [definition.internal_id for definition in catalog.definitions()] == [
        "home.observe.v1",
        "alfworld.observe.v1",
        "coworker.click.v1",
    ]


def test_catalog_allows_alias_variants_but_rejects_duplicate_stable_id() -> None:
    catalog = ToolCatalog()
    first = _tool("home.observe.v1", "observe", source="home")
    variant = _tool("alfworld.observe.v1", "observe", source="alfworld")
    duplicate = _tool("home.observe.v1", "observe_home", source="plugin")
    catalog.register(first)
    catalog.register(variant)

    with pytest.raises(ToolCatalogError) as caught:
        catalog.register(duplicate)
    message = str(caught.value)
    assert "duplicate internal id" in message
    assert "home:home.tools.observe" in message
    assert "plugin:plugin.tools.observe_home" in message
    assert catalog.list_tools() == (first, variant)


def test_override_requires_exact_snapshots_and_both_provenances() -> None:
    catalog = ToolCatalog()
    original = _tool("home.echo.v1", "echo", source="builtin")
    replacement = _tool(
        "home.echo.v1",
        "echo",
        source="project",
        version="1.1.0",
    )
    catalog.register(original)
    authorization = CatalogOverrideAuthorization(
        internal_id="home.echo.v1",
        existing_snapshot_sha256=original.definition.snapshot_sha256,
        replacement_snapshot_sha256=replacement.definition.snapshot_sha256,
        existing_provenance=original.definition.provenance,
        replacement_provenance=replacement.definition.provenance,
        authorized_by="release-config",
        reason="locked project override",
    )
    catalog.register(replacement, override=authorization)
    assert catalog.list_tools() == (replacement,)

    stale_replacement = _tool(
        "home.echo.v1",
        "echo_new",
        source="project",
        version="1.2.0",
    )
    with pytest.raises(ToolCatalogError, match="stale existing snapshot"):
        catalog.register(stale_replacement, override=authorization)


def test_override_authorization_cannot_create_or_target_another_id() -> None:
    catalog = ToolCatalog()
    first = _tool("home.echo.v1", "echo", source="builtin")
    authorization = CatalogOverrideAuthorization(
        internal_id="home.echo.v1",
        existing_snapshot_sha256=first.definition.snapshot_sha256,
        replacement_snapshot_sha256=first.definition.snapshot_sha256,
        existing_provenance=first.definition.provenance,
        replacement_provenance=first.definition.provenance,
        authorized_by="release-config",
        reason="test",
    )
    with pytest.raises(ToolCatalogError, match="cannot create"):
        catalog.register(first, override=authorization)

    catalog.register(first)
    wrong = CatalogOverrideAuthorization(
        internal_id="home.other.v1",
        existing_snapshot_sha256=first.definition.snapshot_sha256,
        replacement_snapshot_sha256=first.definition.snapshot_sha256,
        existing_provenance=first.definition.provenance,
        replacement_provenance=first.definition.provenance,
        authorized_by="release-config",
        reason="test",
    )
    with pytest.raises(ToolCatalogError, match="internal id mismatch"):
        catalog.register(first, override=wrong)
