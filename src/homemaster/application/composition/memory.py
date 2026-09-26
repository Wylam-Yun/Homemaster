"""Memory composition metadata and readiness helpers."""

from __future__ import annotations

from homemaster.config import HomeMasterConfig


def memory_runtime_is_configured(config: HomeMasterConfig) -> bool:
    """Return whether managed Neo4j has the inputs required for live startup."""

    neo4j = config.memory.neo4j
    return bool(
        neo4j.home
        and neo4j.java_home
        and neo4j.password.get_secret_value()
    )


__all__ = ["memory_runtime_is_configured"]
