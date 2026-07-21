"""Owned/borrowed resource lifecycle for one application run."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from homemaster.application.contracts import (
    ResourceBinding,
    ResourceLifetime,
    ResourceOwnership,
)

T = TypeVar("T")


class ResourceScopeError(RuntimeError):
    """Base class for resource initialization and cleanup failures."""


class ResourceCleanupError(ResourceScopeError):
    """All cleanup failures observed while best-effort closing a scope."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        if not errors:
            raise ValueError("ResourceCleanupError requires at least one error")
        self.errors = errors
        summary = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        super().__init__(f"resource cleanup failed ({len(errors)}): {summary}")


class ApplicationResourceManager:
    """Application-wide keyed leases for shared physical backends."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._users: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._registry_lock = asyncio.Lock()
        self._active_lease_count = 0
        self._waiting_count = 0

    @property
    def active_lease_count(self) -> int:
        return self._active_lease_count

    @property
    def waiting_count(self) -> int:
        return self._waiting_count

    @property
    def resource_count(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def acquire(self, resource_key: str, context: Any):
        key = (resource_key, _backend_resource_identity(context.backend, context.session_id))
        async with self._registry_lock:
            lock = self._locks.setdefault(key, asyncio.Lock())
            self._users[key] += 1
            self._waiting_count += 1
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            self._waiting_count -= 1
            self._active_lease_count += 1
            yield
        finally:
            if acquired:
                self._active_lease_count -= 1
                lock.release()
            else:
                self._waiting_count -= 1
            async with self._registry_lock:
                self._users[key] -= 1
                if self._users[key] == 0:
                    del self._users[key]
                    self._locks.pop(key, None)


@dataclass
class _CleanupEntry:
    binding: ResourceBinding
    closer: Callable[[Any], Any] | None
    closed: bool = False


class ResourceHandle(Generic[T]):
    """Typed view of a resource registered in a scope."""

    def __init__(self, entry: _CleanupEntry) -> None:
        self._entry = entry

    @property
    def resource(self) -> T:
        return self._entry.binding.resource

    @property
    def binding(self) -> ResourceBinding:
        return self._entry.binding


class RunResourceScope:
    """A LIFO cleanup stack with explicit ownership semantics.

    A successful acquire is registered before optional startup runs.  Thus a
    startup exception can never leak the just-created resource.  Borrowed
    resources remain visible through the scope but have no cleanup entry.
    """

    def __init__(self) -> None:
        self._entries: list[_CleanupEntry] = []
        self._by_name: dict[str, _CleanupEntry] = {}
        self._closed = False
        self._closing = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def bindings(self) -> tuple[ResourceBinding, ...]:
        return tuple(entry.binding for entry in self._entries)

    async def acquire(
        self,
        name: str,
        factory: Callable[[], T | Awaitable[T]],
        *,
        ownership: ResourceOwnership = ResourceOwnership.OWNED,
        lifetime: ResourceLifetime = ResourceLifetime.RUN,
        release: Callable[[T], Any] | None = None,
        start: Callable[[T], Any] | None = None,
    ) -> ResourceHandle[T]:
        self._ensure_open()
        if name in self._by_name:
            raise ValueError(f"resource already registered: {name}")
        if not callable(factory):
            raise TypeError("resource factory must be callable")
        if release is not None and not callable(release):
            raise TypeError("resource release must be callable")
        if start is not None and not callable(start):
            raise TypeError("resource start must be callable")
        try:
            resource = await _maybe_await(factory())
        except BaseException as exc:
            await self._rollback_preserving(exc)
            raise

        binding = ResourceBinding(
            name=name,
            resource=resource,
            ownership=ownership,
            lifetime=lifetime,
            release=release,
        )
        entry = _CleanupEntry(
            binding=binding,
            closer=None if ownership is ResourceOwnership.BORROWED else _resolve_closer(
                resource,
                release,
            ),
        )
        self._entries.append(entry)
        self._by_name[name] = entry
        try:
            if start is not None:
                await _maybe_await(start(resource))
        except BaseException as exc:
            await self._rollback_preserving(exc)
            raise
        return ResourceHandle(entry)

    def bind(
        self,
        binding: ResourceBinding,
    ) -> ResourceHandle[Any]:
        """Register an already-acquired resource, preserving the same rules."""

        self._ensure_open()
        if not isinstance(binding, ResourceBinding):
            raise TypeError("binding must be ResourceBinding")
        if binding.name in self._by_name:
            raise ValueError(f"resource already registered: {binding.name}")
        entry = _CleanupEntry(
            binding=binding,
            closer=(
                None
                if binding.ownership is ResourceOwnership.BORROWED
                else _resolve_closer(binding.resource, binding.release)
            ),
        )
        self._entries.append(entry)
        self._by_name[binding.name] = entry
        return ResourceHandle(entry)

    def get(self, name: str) -> ResourceHandle[Any] | None:
        entry = self._by_name.get(name)
        return ResourceHandle(entry) if entry is not None else None

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._closing:
            return
        self._closing = True
        errors: list[BaseException] = []
        for entry in reversed(self._entries):
            if entry.closed or entry.closer is None:
                entry.closed = True
                continue
            try:
                await _maybe_await(entry.closer(entry.binding.resource))
            except BaseException as exc:
                errors.append(exc)
            finally:
                entry.closed = True
        self._closed = True
        self._closing = False
        if errors:
            raise ResourceCleanupError(tuple(errors))

    async def __aenter__(self) -> RunResourceScope:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        del exc_type, traceback
        try:
            await self.aclose()
        except ResourceCleanupError as cleanup_error:
            if exc_value is None:
                raise
            _attach_cleanup_errors(exc_value, cleanup_error)
        return False

    async def _rollback_preserving(self, primary: BaseException) -> None:
        try:
            await self.aclose()
        except ResourceCleanupError as cleanup_error:
            _attach_cleanup_errors(primary, cleanup_error)

    def _ensure_open(self) -> None:
        if self._closed or self._closing:
            raise RuntimeError("resource scope is closed")


def _resolve_closer(
    resource: Any,
    release: Callable[[Any], Any] | None,
) -> Callable[[Any], Any] | None:
    if release is not None:
        return release
    aclose = getattr(resource, "aclose", None)
    if callable(aclose):
        return lambda value: value.aclose()
    close = getattr(resource, "close", None)
    if callable(close):
        return lambda value: value.close()
    return None


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _attach_cleanup_errors(primary: BaseException, cleanup: ResourceCleanupError) -> None:
    primary.add_note(str(cleanup))
    primary.cleanup_error = cleanup  # type: ignore[attr-defined]


def _backend_resource_identity(backend: object | None, session_id: str) -> str:
    actual = getattr(backend, "actual_backend", backend)
    identity = getattr(actual, "backend_id", None)
    if isinstance(identity, str) and identity.strip():
        return identity
    if actual is not None:
        return f"object:{id(actual)}"
    return f"session:{session_id}"


__all__ = [
    "ApplicationResourceManager",
    "ResourceCleanupError",
    "ResourceHandle",
    "ResourceScopeError",
    "RunResourceScope",
]
