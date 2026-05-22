"""Runtime failure rule loader — evaluates declarative rules from failures.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FailureRuleProvider:
    """Evaluates declarative failure rules from failures.json at runtime."""

    rules: list[dict[str, Any]]
    scenario: str

    @classmethod
    def from_scenario(cls, scenario: str, scenario_root: Path) -> FailureRuleProvider:
        """Load failures.json from scenario_root; missing file yields empty rules."""
        failures_path = scenario_root / "failures.json"
        if not failures_path.is_file():
            return cls(rules=[], scenario=scenario)
        try:
            data = json.loads(failures_path.read_text(encoding="utf-8"))
            rules = data.get("failure_rules", [])
            if not isinstance(rules, list):
                return cls(rules=[], scenario=scenario)
            return cls(rules=rules, scenario=scenario)
        except (json.JSONDecodeError, OSError):
            return cls(rules=[], scenario=scenario)

    def should_force_no_object(self, *, target_category: str | None = None) -> bool:
        """Return True if any enabled force_no_object rule matches.

        Matching logic:
        - Skip rules without ``rule_type`` key (legacy attempt-based rules).
        - Skip rules where ``enabled`` is not True.
        - For ``force_no_object`` rules: match when ``target_category`` is
          absent/None in the rule, or equals the provided ``target_category``.
        """
        for rule in self.rules:
            if "rule_type" not in rule:
                continue  # legacy format
            if rule.get("enabled") is not True:
                continue
            if rule["rule_type"] != "force_no_object":
                continue
            rule_cat = rule.get("target_category")
            if rule_cat is not None and target_category is not None and rule_cat == target_category:
                return True
        return False
