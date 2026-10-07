"""Async thin client for the HomeMaster server (REST + WebSocket).

The CLI no longer composes an application in-process for ``shell``/``run`` —
it talks to the long-lived ``homemaster serve`` runtime over the same REST+WS
protocol the browser console uses.

Ordering contract (server enforced, ``app.py`` returns 409 otherwise):
subscribe the session event stream *before* ``POST /messages``.  ``send()``
guarantees that ordering internally.

Reconnect semantics follow dsh ``connection.ts``: a generation counter is
bumped per connect attempt, drops retry with exponential backoff + jitter,
and after a successful reconnect the client refetches history, pending
approvals and pending questions, then enqueues one ``client.resync`` marker
event so consumers can repaint recovered state (old events are not replayed).
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import httpx

RESYNC_EVENT_TYPE = "client.resync"
STREAM_END = object()

_DEFAULT_BACKOFF_BASE_S = 0.5
_DEFAULT_BACKOFF_FACTOR = 2.0
_DEFAULT_BACKOFF_MAX_S = 10.0
_WS_OPEN_TIMEOUT_S = 10.0
_NOT_READY_RETRY_LIMIT = 8
_NOT_READY_RETRY_BASE_S = 0.15


class ServerError(Exception):
    """Base class for HomeMaster server communication failures."""


class ServerUnavailableError(ServerError):
    """The server cannot be reached at all (connect refused, DNS, timeout)."""


class ApiError(ServerError):
    """A typed error envelope returned by the server."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable

    def __repr__(self) -> str:
        return f"ApiError(status={self.status}, code={self.code!r})"


class SessionNotFoundError(ApiError):
    """The target session does not exist on the server."""


class SessionBusyError(ApiError):
    """The session already has an active run (HTTP 409 session_busy)."""


class EventStreamNotReadyError(ApiError):
    """The event stream was not subscribed before ``POST /messages``."""


class StreamClosedError(ServerError):
    """The server rejected the event subscription (unknown session)."""


def _coalesce_body(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


class HomeServerClient:
    """One session-scoped REST+WS client bound to a HomeMaster server."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        ws_connect: Callable[..., Any] | None = None,
        backoff_base_s: float = _DEFAULT_BACKOFF_BASE_S,
        backoff_factor: float = _DEFAULT_BACKOFF_FACTOR,
        backoff_max_s: float = _DEFAULT_BACKOFF_MAX_S,
        jitter: Callable[[], float] = random.random,
        request_timeout_s: float = 30.0,
        max_reconnect_attempts: int | None = 8,
    ) -> None:
        base_url = str(base_url).strip().rstrip("/")
        if not base_url:
            raise ValueError("base_url must be a non-empty server URL")
        self.base_url = base_url
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(request_timeout_s),
        )
        self._owns_http = http_client is None
        self._ws_connect = ws_connect
        self._backoff_base_s = backoff_base_s
        self._backoff_factor = backoff_factor
        self._backoff_max_s = backoff_max_s
        self._jitter = jitter
        # Reconnects are bounded: after ``max_reconnect_attempts`` consecutive
        # failures the pump ends so blocked consumers (a shell mid-turn) are
        # released instead of spinning forever on a dead server.
        self._max_reconnect_attempts = max_reconnect_attempts
        # Connection bookkeeping. ``generation`` counts connect attempts so a
        # stale socket can never publish into a newer subscription.
        self.generation = 0
        self.state = "offline"  # offline|connecting|connected|reconnecting
        self._session_id: str | None = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_ready = asyncio.Event()
        self._stream_failed = asyncio.Event()
        self._stream_error: ServerError | None = None
        self._stop_requested = False

    # ------------------------------------------------------------------
    # HTTP surface
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method,
                path,
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ServerUnavailableError(
                f"cannot reach HomeMaster server at {self.base_url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            payload: dict[str, Any] = {}
            try:
                parsed = response.json() if response.content else {}
                if isinstance(parsed, dict):
                    payload = parsed
            except ValueError:
                payload = {}
            code = str(payload.get("code") or "http_error")
            message = str(payload.get("message") or f"HTTP {response.status_code}")
            retryable = payload.get("retryable") is True
            if code == "session_not_found" or (
                response.status_code == 404 and "/sessions/" in path
            ):
                raise SessionNotFoundError(response.status_code, code, message, retryable)
            if code == "session_busy":
                raise SessionBusyError(response.status_code, code, message, retryable)
            if code == "event_stream_not_ready":
                raise EventStreamNotReadyError(response.status_code, code, message, retryable)
            raise ApiError(response.status_code, code, message, retryable)
        if not response.content:
            return {}
        return response.json()

    async def meta(self) -> dict[str, Any]:
        """GET /api/meta — server version, memory mode, environment."""

        return _coalesce_body(await self._request("GET", "/api/meta"))

    async def providers(self) -> list[dict[str, Any]]:
        """GET /api/providers — configured provider list (never echoes keys)."""

        payload = await self._request("GET", "/api/providers")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in payload.get("providers", []) if isinstance(item, dict)]

    async def create_session(self, session_id: str | None = None) -> str:
        body = {"session_id": session_id} if session_id else {}
        payload = _coalesce_body(await self._request("POST", "/api/sessions", json_body=body))
        session = str(payload.get("session_id") or "")
        if not session:
            raise ApiError(500, "invalid_response", "server returned no session_id")
        return session

    async def list_sessions(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/api/sessions")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return [item for item in payload.get("sessions", []) if isinstance(item, dict)]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return _coalesce_body(await self._request("GET", f"/api/sessions/{session_id}"))

    async def history(self, session_id: str | None = None) -> list[dict[str, Any]]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("history() requires a session_id")
        payload = _coalesce_body(
            await self._request("GET", f"/api/sessions/{sid}/history")
        )
        messages = payload.get("messages", [])
        return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []

    async def send(
        self,
        text: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        provider_name: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """POST one message, guaranteeing the WS subscription came first."""

        sid = session_id or self._session_id
        if not sid:
            raise ValueError("send() requires a session_id")
        await self.subscribe(sid)
        body: dict[str, Any] = {
            "request_id": request_id or f"cli-{uuid.uuid4().hex}",
            "text": text,
        }
        if provider_name:
            body["provider_name"] = provider_name
        if model:
            body["model"] = model
        last_error: ApiError | None = None
        for attempt in range(_NOT_READY_RETRY_LIMIT):
            try:
                return _coalesce_body(
                    await self._request(
                        "POST", f"/api/sessions/{sid}/messages", json_body=body
                    )
                )
            except EventStreamNotReadyError as exc:
                # Server-side race: the WS handshake completed but the hub has
                # not registered the subscriber yet.  Short retry; after the
                # limit the typed error propagates.
                last_error = exc
                await asyncio.sleep(_NOT_READY_RETRY_BASE_S * (attempt + 1))
        assert last_error is not None
        raise last_error

    async def cancel(self, session_id: str | None = None) -> dict[str, Any]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("cancel() requires a session_id")
        return _coalesce_body(
            await self._request("POST", f"/api/sessions/{sid}/cancel", json_body={})
        )

    async def status(self, session_id: str | None = None) -> dict[str, Any]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("status() requires a session_id")
        return _coalesce_body(await self._request("GET", f"/api/sessions/{sid}/status"))

    async def compact(self, session_id: str | None = None) -> dict[str, Any]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("compact() requires a session_id")
        return _coalesce_body(
            await self._request("POST", f"/api/sessions/{sid}/compact", json_body={})
        )

    async def set_mode(self, ui_mode: str, session_id: str | None = None) -> dict[str, Any]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("set_mode() requires a session_id")
        return _coalesce_body(
            await self._request(
                "POST", f"/api/sessions/{sid}/mode", json_body={"ui_mode": ui_mode}
            )
        )

    async def list_approvals(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """GET /{id}/approvals — pending approvals for reconnect recovery."""

        sid = session_id or self._session_id
        if not sid:
            raise ValueError("list_approvals() requires a session_id")
        try:
            payload = await self._request("GET", f"/api/sessions/{sid}/approvals")
        except ApiError as exc:
            if exc.status == 404:
                return []
            raise
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        approvals = payload.get("approvals", [])
        return [a for a in approvals if isinstance(a, dict)] if isinstance(approvals, list) else []

    async def get_approval(self, approval_id: str) -> dict[str, Any]:
        return _coalesce_body(await self._request("GET", f"/api/approvals/{approval_id}"))

    async def submit_approval(
        self,
        approval_id: str,
        *,
        request_revision: int,
        decisions: list[tuple[str, str] | dict[str, str]],
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/approvals/{id} with one v2 per-item submission."""

        items = [
            {"item_id": item[0] if isinstance(item, tuple) else item["item_id"],
             "choice": item[1] if isinstance(item, tuple) else item["choice"]}
            for item in decisions
        ]
        body = {
            "protocol_version": 2,
            "submission_id": submission_id or f"sub-cli-{uuid.uuid4().hex[:16]}",
            "request_revision": request_revision,
            "decisions": items,
        }
        return _coalesce_body(
            await self._request("POST", f"/api/approvals/{approval_id}", json_body=body)
        )

    async def cancel_approval(
        self,
        approval_id: str,
        *,
        request_revision: int,
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "submission_id": submission_id or f"cancel-cli-{uuid.uuid4().hex[:16]}",
            "request_revision": request_revision,
        }
        return _coalesce_body(
            await self._request(
                "POST", f"/api/approvals/{approval_id}/cancel", json_body=body
            )
        )

    async def list_questions(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """GET /{id}/questions — pending ask_user questions for reconnect."""

        sid = session_id or self._session_id
        if not sid:
            raise ValueError("list_questions() requires a session_id")
        try:
            payload = await self._request("GET", f"/api/sessions/{sid}/questions")
        except ApiError as exc:
            if exc.status == 404:
                return []
            raise
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        questions = payload.get("questions", [])
        return [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []

    async def answer_question(
        self,
        question_id: str,
        text: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        sid = session_id or self._session_id
        if not sid:
            raise ValueError("answer_question() requires a session_id")
        return _coalesce_body(
            await self._request(
                "POST",
                f"/api/sessions/{sid}/questions/{question_id}/answer",
                json_body={"text": text},
            )
        )

    async def resolve_skill(self, text: str) -> dict[str, Any]:
        """POST /api/skills/resolve — the thin client has no local registry."""

        try:
            return _coalesce_body(
                await self._request("POST", "/api/skills/resolve", json_body={"text": text})
            )
        except ApiError as exc:
            if exc.status == 404:
                # Older server without the resolve endpoint: treat as plain so
                # the caller can fall back to the unknown-command hint.
                return {"kind": "plain"}
            raise

    # ------------------------------------------------------------------
    # WebSocket event stream
    # ------------------------------------------------------------------
    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def subscribed(self) -> bool:
        return (
            self._stream_ready.is_set()
            and self._stream_task is not None
            and not self._stream_task.done()
        )

    async def subscribe(self, session_id: str) -> None:
        """Start (or restart) the WS pump for ``session_id``; wait for open."""

        if (
            self._stream_task is not None
            and not self._stream_task.done()
            and self._session_id == session_id
            and self._stream_ready.is_set()
        ):
            return
        await self._stop_stream()
        self._session_id = session_id
        self._stream_ready.clear()
        self._stream_failed.clear()
        self._stream_error = None
        self._stop_requested = False
        self.state = "connecting"
        self._stream_task = asyncio.create_task(
            self._stream_loop(session_id), name=f"homemaster-ws-{session_id}"
        )
        ready_task = asyncio.create_task(self._stream_ready.wait())
        failed_task = asyncio.create_task(self._stream_failed.wait())
        try:
            await asyncio.wait(
                (ready_task, failed_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in (ready_task, failed_task):
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(ready_task, failed_task, return_exceptions=True)
        if self._stream_failed.is_set():
            assert self._stream_error is not None
            raise self._stream_error

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield WebEvent dicts (plus ``client.resync`` markers) until closed."""

        while True:
            item = await self._queue.get()
            if item is STREAM_END:
                return
            yield item

    async def _stream_loop(self, session_id: str) -> None:
        first_connect = True
        attempt = 0
        try:
            while not self._stop_requested:
                self.generation += 1
                generation = self.generation
                try:
                    async with self._open_socket(session_id) as socket:
                        self.state = "connected"
                        attempt = 0
                        if first_connect:
                            first_connect = False
                            self._stream_ready.set()
                        else:
                            await self._emit_resync(session_id, generation)
                        async for raw in socket:
                            event = _parse_event(raw)
                            if event is not None:
                                self._queue.put_nowait(event)
                except Exception as exc:  # noqa: BLE001 — classified below
                    if self._stop_requested:
                        break
                    if first_connect:
                        self._stream_error = _classify_connect_error(exc, self.base_url)
                        self._stream_failed.set()
                        return
                    self.state = "reconnecting"
                if self._stop_requested:
                    break
                attempt += 1
                if (
                    self._max_reconnect_attempts is not None
                    and attempt > self._max_reconnect_attempts
                ):
                    break
                await asyncio.sleep(self._backoff_delay(attempt))
        except asyncio.CancelledError:
            raise
        finally:
            self.state = "offline"
            self._stream_ready.clear()
            self._queue.put_nowait(STREAM_END)

    def _open_socket(self, session_id: str) -> Any:
        url = _ws_url(self.base_url, session_id)
        factory = self._ws_connect or _default_ws_connect
        return factory(url)

    def _backoff_delay(self, attempt: int) -> float:
        cap = min(
            self._backoff_max_s,
            self._backoff_base_s * self._backoff_factor ** max(0, attempt - 1),
        )
        return cap / 2 + self._jitter() * cap / 2

    async def _emit_resync(self, session_id: str, generation: int) -> None:
        """Refetch durable state after a reconnect and emit one marker event."""

        payload: dict[str, Any] = {"generation": generation, "resync": True}
        try:
            payload["history"] = await self.history(session_id)
        except ServerError as exc:
            payload["history_error"] = str(exc)
            payload["history"] = []
        try:
            payload["approvals"] = await self.list_approvals(session_id)
        except ServerError:
            payload["approvals"] = []
        try:
            payload["questions"] = await self.list_questions(session_id)
        except ServerError:
            payload["questions"] = []
        self._queue.put_nowait(
            {
                "type": RESYNC_EVENT_TYPE,
                "session_id": session_id,
                "run_id": "",
                "request_id": "",
                "payload": payload,
            }
        )

    async def _stop_stream(self) -> None:
        task = self._stream_task
        self._stream_task = None
        self._stop_requested = True
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._stream_ready.clear()

    async def aclose(self) -> None:
        self._stop_requested = True
        await self._stop_stream()
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> HomeServerClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def _default_ws_connect(url: str) -> Any:
    from websockets.asyncio.client import connect

    return connect(
        url,
        open_timeout=_WS_OPEN_TIMEOUT_S,
        close_timeout=5,
    )


def _ws_url(base_url: str, session_id: str) -> str:
    if base_url.startswith("https://"):
        scheme = "wss://"
        rest = base_url[len("https://") :]
    elif base_url.startswith("http://"):
        scheme = "ws://"
        rest = base_url[len("http://") :]
    elif base_url.startswith(("ws://", "wss://")):
        scheme, _, rest = base_url.partition("://")
        return f"{scheme}://{rest}/api/events?session_id={session_id}"
    else:
        raise ValueError(f"unsupported base_url scheme: {base_url!r}")
    return f"{scheme}{rest}/api/events?session_id={session_id}"


def _parse_event(raw: Any) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
        return None
    return parsed


def _classify_connect_error(exc: BaseException, base_url: str) -> ServerError:
    code = _ws_close_code(exc)
    if code == 4404:
        return StreamClosedError("the server rejected the session event subscription")
    status = _ws_reject_status(exc)
    if status in (403, 404):
        return StreamClosedError(
            f"the server rejected the session event subscription (HTTP {status})"
        )
    return ServerUnavailableError(
        f"cannot reach HomeMaster event stream at {base_url}: {exc}"
    )


def _ws_close_code(exc: BaseException) -> int | None:
    return getattr(exc, "code", None) or getattr(getattr(exc, "rcvd", None), "code", None)


def _ws_reject_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


__all__ = [
    "ApiError",
    "EventStreamNotReadyError",
    "HomeServerClient",
    "RESYNC_EVENT_TYPE",
    "ServerError",
    "ServerUnavailableError",
    "SessionBusyError",
    "SessionNotFoundError",
    "StreamClosedError",
]
