"""Dreaming and consolidation algorithm configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DreamingConfig:
    """Configuration for offline memory consolidation."""

    lookback_days: int = field(default=7)
    """Recent-day window used to select hot entity scopes."""

    max_scopes_per_run: int | None = field(default=None)
    """Maximum number of hot scopes processed in one run; None means unlimited."""

    max_seed_memories: int | None = field(default=None)
    """Maximum number of recent memories scanned when selecting hot scopes; None means unlimited."""

    max_memories_per_scope: int = field(default=40)
    """Maximum number of related memories inserted into one scope prompt."""

    min_scope_updates: int = field(default=1)
    """Minimum recent updates required for a scope to be processed."""

    min_cluster_size: int = field(default=2)
    """Clusters smaller than this value skip consolidation LLM calls."""

    max_entity_memory_count: int = field(default=50)
    """Entities with more active memories than this threshold are treated as
    noise and excluded from entity grouping."""

    concurrency: int = field(default=8)
    """Maximum number of unique dreaming clusters processed concurrently."""

    consolidation_model: str | None = field(default=None)
    """Optional model name dedicated to consolidation tasks."""

    scope_batch_size: int = field(default=2000)
    """Number of seed memories processed per neo4j query when selecting graph
    scopes.  Larger batches reduce round trips but consume more transaction
    memory.  Reduce for large graphs (e.g. 262k contexts)."""

    entity_canonicalization_enabled: bool = field(default=False)
    """When enabled, hot-scope entities are deduplicated before clustering:
    same-type entity pairs with high name/vector similarity are arbitrated by
    the consolidation LLM and, when confirmed to share one referent, merged
    (MENTIONS edges re-pointed to the canonical node, loser archived)."""

    entity_merge_dry_run: bool = field(default=False)
    """Emit merge suggestion receipts without mutating the graph.  Used to
    audit arbitration precision before enabling real merges."""

    entity_merge_name_similarity: float = field(default=0.5)
    """Minimum token-level Jaccard similarity between normalized entity names
    for a pair to become an LLM arbitration candidate."""

    entity_merge_vector_similarity: float | None = field(default=None)
    """Optional cosine threshold on entity embeddings that independently
    qualifies a candidate pair.  ``None`` disables the vector leg."""

    entity_merge_max_pairs: int = field(default=32)
    """Maximum candidate pairs sent to LLM arbitration per dreaming run."""

    entity_merge_sample_memories: int = field(default=3)
    """Memory content snippets sampled per entity as arbitration evidence."""


__all__ = ["DreamingConfig"]
