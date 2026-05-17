"""HomeMaster V1.2 LLM-first task brain package.

Package layout:
  agent/      Active AgentRuntime implementation (tool loop, state, decisions)
  tools/      ToolSpec / ToolRegistry / Dispatcher / simulated executors / get_skill
  skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md packages
  memory/     RAG / profile / fact memory / MEMORY.md and USER.md snapshots
  events/     RuntimeEvent schema, sinks, sanitizer
  config/     Run-scoped RuntimeSettings and path/config helpers
  providers/  LLM/embedding/Mimo decision provider clients
  pipeline/   Compatibility layer only (legacy stage loop)
  stages/     Transitional Stage02-06 handlers only
  cli/        CLI entry points (run, doctor, interactive shell)

Root-level .py files are either public facade (contracts, runtime, trace) or
backward-compatibility shims. See docs/shim_lifecycle.md for the full inventory.
"""

__all__ = [
    "__version__",
    "cli",
    "contracts",
    "logger",
    "pipeline",
    "runtime",
    "stages",
    "trace",
]

__version__ = "0.1.0"
