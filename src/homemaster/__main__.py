"""Allow ``python -m homemaster`` — forwards to the CLI entrypoint.

The thin client spawns the server with ``python -m homemaster serve`` (see
``cli/server_process.py``); this shim keeps that argv working.
"""

from __future__ import annotations

from homemaster.cli.app import app

if __name__ == "__main__":
    app()
