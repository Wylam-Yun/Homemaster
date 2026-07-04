"""HomeMaster CLI sub-package."""

import sys
from types import ModuleType
from typing import Any

__all__ = ["app"]


class _CliPackage(ModuleType):
    def __getattribute__(self, name: str) -> Any:
        value = super().__getattribute__(name)
        if name == "app" and isinstance(value, ModuleType):
            typer_app = getattr(value, "app", None)
            if typer_app is not None:
                return typer_app
        return value


class _LazyTyperApp:
    def _load(self) -> Any:
        from homemaster.cli.app import app

        return app

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._load()(*args, **kwargs)


app = _LazyTyperApp()
sys.modules[__name__].__class__ = _CliPackage
