from __future__ import annotations

import pytest
from case02_openenv.terminal.policy import CommandPolicy, CommandPolicyError


def test_only_exact_locked_grep_is_accepted() -> None:
    policy = CommandPolicy("tenanttenanttenant000198", "read")
    parsed = policy.parse(policy.exact_command)
    assert parsed.tokens == policy.expected_tokens
    assert policy.parse(policy.exact_command) == parsed


@pytest.mark.parametrize(
    "mutation",
    [
        " | cat",
        "; true",
        " > /tmp/out",
        " --include=*",
        "$(id)",
        (
            "grep -A 2 tenanttenanttenant000198:read "
            "/opt/app/service_layer/component/config/extension_item_mapping.json"
        ),
    ],
)
def test_shell_mutations_are_rejected(mutation: str) -> None:
    policy = CommandPolicy("tenanttenanttenant000198", "read")
    command = policy.exact_command + mutation if not mutation.startswith("grep") else mutation
    with pytest.raises(CommandPolicyError):
        policy.parse(command)
