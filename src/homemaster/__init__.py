"""HomeMaster — generic agent runtime with home-robot domain tools.

Package layout:
  agent/      Generic runtime (messages, sessions, transport, tool loop)
  tools/      ToolSpec / ToolRegistry / Dispatcher
  domain/     Domain tool packages for robot capabilities
  memory/     RAG retrieval, indexing, tokenization, runtime memory store
  skills/     SkillSpec / SkillLoader / SkillRegistry / builtin SKILL.md
  events/     RuntimeEvent schema, sinks, and bounded projections
  config/     Unified YAML configuration
  providers/  LLM/embedding transport adapters
  cli/        CLI entry points (run, doctor, interactive shell)
"""

__all__ = [
    "__version__",
    "cli",
]

__version__ = "0.1.0"
