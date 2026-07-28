# OpenHarness upstream test ownership mapping

Upstream source: `HKUDS/OpenHarness@9b2efd795c6aa09f88b0c257d269a9e518da6ae7`.

| Deleted upstream test | HomeMaster-owned regression |
| --- | --- |
| `tests/test_skills/test_loader.py` | `tests/homemaster/skills/test_v20_openharness_compat.py`, `tests/homemaster/skills/test_skill_security.py` |
| `tests/test_tools/test_bash_tool.py` | `tests/homemaster/tools/test_v20_openharness_bash_tool.py` |
| `tests/test_tools/test_core_tools.py` | `tests/homemaster/tools/test_v20_openharness_core_tools.py`, `tests/homemaster/tools/test_v20_openharness_file_tools.py` |
| `tests/test_tools/test_grep_tool.py` | `tests/homemaster/tools/test_v20_openharness_core_tools.py` |
| `tests/test_tools/test_image_generation_tool.py` | `tests/homemaster/tools/test_v20_openharness_service_tools.py` |
| `tests/test_tools/test_image_to_text_tool.py` | `tests/homemaster/tools/test_v20_openharness_service_tools.py` |
| `tests/test_tools/test_integration_flows.py` | `tests/homemaster/tools/test_v20_openharness_default_tools.py`, `tests/homemaster/tools/test_v20_openharness_service_tools.py` |
| `tests/test_tools/test_mcp_auth_tool.py` | `tests/homemaster/tools/test_v20_openharness_service_tools.py`, `tests/homemaster/mcp/test_management_tools.py` |
| `tests/test_tools/test_mcp_tool.py` | `tests/homemaster/mcp/test_adapter.py`, `tests/homemaster/mcp/test_management_tools.py` |
| `tests/test_tools/test_task_tools.py` | `tests/homemaster/tools/test_v20_openharness_service_tools.py` |
| `tests/test_tools/test_web_fetch_tool.py` | `tests/homemaster/tools/test_v20_openharness_web_tools.py` |
| `tests/test_mcp/test_stdio_flow.py` | `tests/homemaster/mcp/test_client.py`, `tests/homemaster/mcp/test_stdio_integration.py` |
| `tests/test_mcp/test_http_flow.py` | `tests/homemaster/mcp/test_client.py` |
| `tests/test_mcp/test_integration.py` | `tests/homemaster/mcp/test_adapter.py`, `tests/homemaster/mcp/test_client.py` |
| `tests/test_swarm/test_imports.py` | `tests/homemaster/tools/test_v20_openharness_service_tools.py`, `tests/homemaster/test_child_worker.py` |

The mapping is a provenance index, not a claim of byte-for-byte test equivalence. Current behavior is governed by
HomeMaster's public contracts and terminal-state tests.
