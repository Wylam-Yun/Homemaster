# Vendored: AgentScope

- **Upstream**: https://github.com/agentscope-ai/agentscope
- **Version**: 2.0.10dev
- **Commit**: `a1f30d48b6de5bfb25a4bf2c5fbf1b6442f5106d` (2026-09-30)
- **Copied**: entire upstream `src/agentscope/` package (verbatim, including `py.typed`, `_models/*.yaml`, `Dockerfile*.template`, alembic assets)
- **License**: Apache-2.0 — see `LICENSE` in this directory
- **Why vendored**: 2.0.10dev is not published to PyPI; HomeMaster's agent-foundation migration (plan/V3.7) requires this exact dev-version API surface.
- **Discipline**: keep this tree verbatim. Local patches MUST be recorded in the patch list below; an upstream upgrade = replay the whole tree at a new commit + re-apply listed patches + run contract tests.

## Local patches

(none)
