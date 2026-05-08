"""Recovery configuration — max_attempts from homemaster.json.

The ``recovery`` section is optional.  When absent the default of 3 is used.

Example homemaster.json::

    {
      "recovery": {
        "max_attempts": 5
      }
    }
"""

from __future__ import annotations

from homemaster.runtime import RuntimeConfigError, get_config_section, load_homemaster_config


def _load_recovery_config() -> int:
    cfg = get_config_section(load_homemaster_config(), "recovery")
    if cfg is None:
        return 3
    ma = cfg.get("max_attempts", 3)
    if not isinstance(ma, int) or ma < 1:
        raise RuntimeConfigError(
            f"recovery.max_attempts must be a positive int, got {ma!r}"
        )
    return ma


MAX_RECOVERY_ATTEMPTS: int = _load_recovery_config()
