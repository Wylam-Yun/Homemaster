"""HomeMaster ↔ AgentScope adaptation boundary.

This package is the ONLY place in HomeMaster that may import ``agentscope``.
Everything outside must consume canonical HomeMaster contracts
(``homemaster.agent.messages``, ``homemaster.tools.contracts``).
"""
