"""Script entry point for the isolated worker interpreter."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    namespace = runpy.run_path(str(Path(__file__).with_name("__main__.py")))
    raise SystemExit(namespace["main"]())
