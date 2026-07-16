"""Run the environment service with Uvicorn."""

from __future__ import annotations

import uvicorn

from case02_openenv.api import create_app
from case02_openenv.config import ServiceConfig


def main() -> None:
    config = ServiceConfig.from_env()
    uvicorn.run(create_app(config), host=config.bind_host, port=config.port)


if __name__ == "__main__":
    main()
