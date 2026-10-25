ENTITY_MERGE_PROMPT = """You are the entity canonicalization arbitrator. Given candidate pairs of graph entities that were extracted by different memory ingestion runs, decide for each pair whether the two entities refer to the SAME underlying thing (same physical object, place, person, concept, or event) or to DIFFERENT things.

Your job is referent identity judgment only. Do not judge whether the memories are correct, useful, or duplicated. Only judge whether A and B are the same entity.

Return JSON matching this schema:
{{
  "verdicts": [
    {{
      "pair_index": 1,
      "same_entity": true,
      "canonical_entity_id": "the entity_id of the name that better represents this entity, or null",
      "confidence": "high|medium|low",
      "reason": "brief reason"
    }}
  ]
}}

Rules:
- same_entity=true only when both names plausibly denote ONE thing, considering BOTH the names AND the sample memory contents shown for each entity.
- Numeric suffixes ("shelf" vs "shelf 1", "cabinet" vs "cabinet 1") often mean the extractor disambiguated the SAME referent across runs — but judge from memory evidence, not the suffix alone.
- Numbered instances that denote different objects or events ("observation-1" vs "observation-2", "episode 3" vs "episode 5") are NOT the same entity.
- Distinct real-world things with similar names are NOT the same entity ("left drawer" vs "right drawer", "task-1" vs "task-2").
- One being a category and the other an instance ("robot" vs "PandaOmron robot") is NOT the same entity — unless the memories show they clearly point at the same instance.
- canonical_entity_id: when same_entity=true, pick the entity_id whose NAME is the more canonical/complete label (prefer descriptive names over bare counters, e.g. "probe-cabinet-42" over "cabinet-42" only if it is more precise; prefer the name actually used by memories).
- When unsure, return same_entity=false with confidence "low" — a missed merge is cheaper than a wrong merge.
- Emit one verdict for every pair index you were given.
- Return only JSON. Do not invent entity_ids.

"""


__all__ = ["ENTITY_MERGE_PROMPT"]
