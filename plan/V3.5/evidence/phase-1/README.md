# V3.5 Phase 1 Evidence

Local composition/import gate recorded 2026-09-25.

Commands and results:

```text
uv run python scripts/verify_v35_architecture.py --json
PASS: composition entry, CLI composition removal, runtime domain boundary,
worker import isolation, old package removal, required artifacts, lock/schema checks.

uv run pytest -q tests/homemaster/application tests/homemaster/integration/test_entry_parity.py
PASS (local run; external provider/live gates excluded).
```

The benchmark event-loop owner now builds an `ApplicationCompositionRequest`; no
provider connection or worker is started by composition itself.
