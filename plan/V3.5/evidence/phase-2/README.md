# V3.5 Phase 2 Evidence

Local canonical tool and HomeWorld black-box evidence, 2026-09-25.

```text
uv run pytest -q tests/homemaster/tools tests/homemaster/test_home_backend_blackbox.py tests/homemaster/v35/test_tool_interface_audit.py
PASS: canonical RegisteredTool async dispatch, typed receipts, and independent
HomeWorld state readback.

uv run ruff check src tests scripts
PASS
```

The old `ToolSpec`/`ToolResult` files are deleted. Historical tests that import
the removed ALFWorld permission package are migration leftovers and are tracked
as cleanup work rather than compatibility requirements.
