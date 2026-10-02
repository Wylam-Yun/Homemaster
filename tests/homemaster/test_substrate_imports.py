"""Import-boundary regression: core-only environment must not pull extras.

Only subprocesses give a clean ``sys.modules`` view, so each case spawns
one interpreter. The boundary package ``homemaster.substrate`` must not
import ``agentscope.app``, ``.tui``, ``.rag``, or other extras-gated
subpackages as a side effect.
"""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN = (
    "agentscope.app",
    "agentscope.tui",
    "agentscope.rag",
    "agentscope.sop",
    "agentscope.workspace",
    "agentscope.realtime",
    "agentscope.classifier",
    "agentscope.a2a",
    "agentscope.tts",
)


def _loaded_modules_after(import_target: str) -> list[str]:
    code = (
        "import sys, importlib\n"
        f"importlib.import_module({import_target!r})\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('agentscope'))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


def test_substrate_import_pulls_no_extras_subpackages() -> None:
    loaded = _loaded_modules_after("homemaster.substrate")
    pulled = [
        m
        for m in loaded
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert not pulled, f"extras-gated subpackages imported: {pulled}"


def test_plain_agentscope_import_pulls_no_extras_subpackages() -> None:
    loaded = _loaded_modules_after("agentscope")
    pulled = [
        m
        for m in loaded
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert not pulled, f"extras-gated subpackages imported: {pulled}"
