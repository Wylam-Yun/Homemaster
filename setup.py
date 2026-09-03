"""Packaging hook for the generic HomeMaster wheel."""

from __future__ import annotations

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class _BuildPyWithoutBenchmark(_build_py):
    """Keep the generic wheel independent from the optional ALFWorld runtime."""

    def build_packages(self) -> None:
        super().build_packages()
        root = Path(self.build_lib)
        for path in root.rglob("*"):
            if path.is_file() and "alfworld" in path.as_posix().casefold():
                path.unlink()


setup(cmdclass={"build_py": _BuildPyWithoutBenchmark})
