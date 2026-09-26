"""Taskset lifecycle helpers shared by continuous benchmark runs."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from homemaster.alfworld.benchmark.episode import BenchmarkApplicationLifecycle


class TasksetLifecycle(AbstractContextManager[BenchmarkApplicationLifecycle]):
    """Close taskset sessions and application resources as one unit."""

    def __init__(self, owner: BenchmarkApplicationLifecycle, session_id: str) -> None:
        self.owner = owner
        self.session_id = session_id

    def __enter__(self) -> BenchmarkApplicationLifecycle:
        self.open()
        return self.owner

    def open(self) -> BenchmarkApplicationLifecycle:
        self.owner.begin_session(self.session_id, exit_reason="alfworld_taskset_end")
        return self.owner

    def close(self) -> None:
        try:
            self.owner.end_session(self.session_id)
        except (AttributeError, KeyError):
            # Test seams may expose only the lifecycle methods they exercise.
            pass
        self.owner.close()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        del exc_type, exc_value, traceback
        self.close()
        return False


__all__ = ["TasksetLifecycle"]
