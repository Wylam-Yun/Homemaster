"""Real installed-mem0 and embedded-Qdrant store tests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from homemaster.config import HomeMasterConfig
from homemaster.memory.mem0_store import Mem0MemoryStore, Mem0StoreError
from homemaster.memory.models import FactRecord, ProcedureRecord


class _EmbeddingHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append({"path": self.path, "body": body})
        inputs = body.get("input")
        values = inputs if isinstance(inputs, list) else [inputs]
        data = [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(index + 1)] * 8,
            }
            for index, _value in enumerate(values)
        ]
        output = json.dumps(
            {
                "object": "list",
                "data": data,
                "model": "test-embedding",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def log_message(self, _format: str, *args: object) -> None:
        del args


@contextmanager
def _embedding_server() -> Iterator[str]:
    _EmbeddingHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(tmp_path: Path, base_url: str) -> HomeMasterConfig:
    return HomeMasterConfig(
        providers={
            "items": [
                {
                    "name": "MemoryEmbedding",
                    "kind": "embedding",
                    "api_format": "openai",
                    "base_url": base_url,
                    "embedding_url": f"{base_url}/embeddings",
                    "model": "test-embedding",
                    "api_keys": ["test-key"],
                }
            ]
        },
        memory={
            "root": tmp_path / "files",
            "mem0": {
                "qdrant_path": tmp_path / "qdrant",
                "collection_name": "memory_test_8_v1",
                "history_db_path": tmp_path / "history.sqlite3",
                "embedding_dimensions": 8,
                "search_threshold": 0.0,
            },
        },
    )


def _fact(location: str, *, name: str = "苹果") -> FactRecord:
    return FactRecord(
        subject={"type": "object", "name": name},
        predicate="location",
        value={"container": location},
        source="environment_observation",
    )


def _procedure() -> ProcedureRecord:
    return ProcedureRecord(
        name="查询当前告警",
        entry_url="https://monitor.example.com/alarms/current",
        steps=(
            {
                "order": 1,
                "action": "open",
                "target": {"url": "https://monitor.example.com/alarms/current"},
                "expect": {"visible_text": "当前告警"},
            },
            {
                "order": 2,
                "action": "extract",
                "target": {"role": "table", "name": "告警列表"},
                "output": "alarms",
            },
        ),
        success={"output_exists": "alarms"},
    )


def _procedure_with_private_input() -> ProcedureRecord:
    return ProcedureRecord(
        name="查询带筛选条件的告警",
        entry_url="https://monitor.example.com/alarms?view=current&region=cn",
        inputs=({"name": "query", "description": "运行时筛选内容"},),
        steps=(
            {
                "order": 1,
                "action": "open",
                "target": {"url": "https://monitor.example.com/alarms?view=current&region=cn"},
                "expect": {"visible_text": "当前告警"},
            },
            {
                "order": 2,
                "action": "fill",
                "target": {"role": "textbox", "name": "筛选", "value": "真实私密输入"},
                "expect": {"visible_text": "筛选完成"},
            },
        ),
        success={"visible_text": "筛选完成"},
    )


@pytest.mark.asyncio
async def test_real_qdrant_crud_raw_terminal_state_and_restart(tmp_path: Path) -> None:
    with _embedding_server() as base_url:
        config = _config(tmp_path, base_url)
        store = Mem0MemoryStore(config)
        store.start()
        assert store.available
        assert type(store._memory.vector_store).__name__ == "Qdrant"

        added = await store.add(_fact("冰箱第二层"), provenance_seq=1)
        assert added.record.value == {"container": "冰箱第二层"}
        raw = store._raw_point_sync(added.memory_id)
        assert raw is not None
        assert raw.payload["memory_type"] == "fact"
        assert raw.payload["record_json"]
        assert raw.payload["provenance_seq"] == 1
        assert raw.payload["user_id"] == "homemaster"

        same = await store.add(_fact("冰箱第二层"), provenance_seq=2)
        assert same.memory_id == added.memory_id
        assert (await store.get(added.memory_id)).memory_id == added.memory_id

        updated = await store.update(added.memory_id, _fact("餐桌"), provenance_seq=3)
        assert updated.memory_id == added.memory_id
        assert updated.record.value == {"container": "餐桌"}
        with pytest.raises(Mem0StoreError) as stale:
            await store.update(added.memory_id, _fact("厨房"), provenance_seq=2)
        assert stale.value.code == "memory_stale_observation"

        await store.close()
        reopened = Mem0MemoryStore(config)
        reopened.start()
        persisted = await reopened.get(added.memory_id)
        assert persisted.record.value == {"container": "餐桌"}
        await reopened.delete(added.memory_id)
        with pytest.raises(Mem0StoreError) as missing:
            await reopened.get(added.memory_id)
        assert missing.value.code == "memory_not_found"
        assert reopened._raw_point_sync(added.memory_id) is None
        await reopened.close()

        final = Mem0MemoryStore(config)
        final.start()
        with pytest.raises(Mem0StoreError):
            await final.get(added.memory_id)
        await final.close()


@pytest.mark.asyncio
async def test_real_exact_semantic_and_bm25_search_branches(tmp_path: Path) -> None:
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        apple = await store.add(_fact("冰箱"), provenance_seq=1)
        procedure = await store.add(_procedure(), provenance_seq=2)
        raw_with_vectors = store._memory.vector_store.client.retrieve(
            collection_name=store.config.memory.mem0.collection_name,
            ids=[apple.memory_id],
            with_payload=True,
            with_vectors=True,
        )[0]
        assert isinstance(raw_with_vectors.vector, dict)
        assert "bm25" in raw_with_vectors.vector

        with pytest.raises(Mem0StoreError) as conflict:
            await store.add(_fact("餐桌"), provenance_seq=3)
        assert conflict.value.code == "memory_conflict"
        assert conflict.value.details["memory_id"] == apple.memory_id

        facts = await store.search("苹果在哪里", memory_type="fact", limit=5)
        keyword_facts = await store.search("苹果", memory_type="fact", limit=5)
        exact_facts = await store.search(
            "不相关查询",
            memory_type="fact",
            subject={"type": "object", "name": "苹果"},
            predicate="location",
            limit=5,
        )
        procedures = await store.search("怎么看现在的告警", memory_type="procedure", limit=5)
        exact_procedures = await store.search(
            "不相关查询",
            memory_type="procedure",
            entry_url="https://monitor.example.com/alarms/current",
            name="查询当前告警",
            limit=5,
        )
        assert [item.memory_id for item in facts] == [apple.memory_id]
        assert "semantic" in facts[0].match_sources
        assert [item.memory_id for item in keyword_facts] == [apple.memory_id]
        assert "bm25" in keyword_facts[0].match_sources
        assert [item.memory_id for item in exact_facts] == [apple.memory_id]
        assert "exact" in exact_facts[0].match_sources
        assert [item.memory_id for item in procedures] == [procedure.memory_id]
        assert [item.memory_id for item in exact_procedures] == [procedure.memory_id]
        assert "exact" in exact_procedures[0].match_sources
        assert all(request["path"] == "/v1/embeddings" for request in _EmbeddingHandler.requests)
        assert not any("messages" in request["body"] for request in _EmbeddingHandler.requests)
        await store.close()


@pytest.mark.asyncio
async def test_search_quarantines_corrupt_raw_record_with_safe_diagnostic(tmp_path: Path) -> None:
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        valid = await store.add(_fact("冰箱", name="有效苹果"), provenance_seq=1)
        corrupt = await store.add(_fact("餐桌", name="损坏苹果"), provenance_seq=2)
        store._memory.vector_store.client.set_payload(
            collection_name=store.config.memory.mem0.collection_name,
            payload={"record_json": '{"schema_version":999}'},
            points=[corrupt.memory_id],
            wait=True,
        )

        result = await store.search_with_diagnostics("苹果", memory_type="fact", limit=5)

        assert [item.memory_id for item in result.records] == [valid.memory_id]
        assert len(result.diagnostics) == 1
        diagnostic = result.diagnostics[0]
        assert diagnostic.code == "memory_record_corrupt"
        assert len(diagnostic.memory_id_hash) == 16
        assert corrupt.memory_id not in repr(diagnostic)
        assert diagnostic.match_sources
        with pytest.raises(Mem0StoreError) as direct_get:
            await store.get(corrupt.memory_id)
        assert direct_get.value.code == "memory_record_corrupt"
        await store.close()


@pytest.mark.asyncio
async def test_timed_out_mutation_keeps_lock_until_thread_finishes_and_close_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        original = store._add_sync
        worker_started = threading.Event()
        release_worker = threading.Event()

        def delayed_add(serialized):
            worker_started.set()
            assert release_worker.wait(timeout=5)
            return original(serialized)

        monkeypatch.setattr(store, "_add_sync", delayed_add)
        mutation = asyncio.create_task(store.add(_fact("超时终态架"), provenance_seq=1))
        assert await asyncio.to_thread(worker_started.wait, 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(mutation), timeout=0.01)
        assert store._inflight

        close_started = time.monotonic()
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0.02)
        assert not closing.done()
        release_worker.set()
        added = await mutation
        await closing
        assert time.monotonic() >= close_started

        reopened = Mem0MemoryStore(_config(tmp_path, base_url))
        reopened.start()
        terminal = await reopened.get(added.memory_id)
        assert terminal.record.value == {"container": "超时终态架"}
        await reopened.close()


@pytest.mark.asyncio
async def test_missing_embedding_provider_is_explicitly_unavailable(tmp_path: Path) -> None:
    config = HomeMasterConfig(
        memory={
            "root": tmp_path / "files",
            "mem0": {
                "qdrant_path": tmp_path / "qdrant",
                "history_db_path": tmp_path / "history.sqlite3",
            },
        }
    )
    store = Mem0MemoryStore(config)
    store.start()
    assert not store.available
    assert "MemoryEmbedding" in (store.unavailable_cause or "")
    with pytest.raises(Mem0StoreError) as unavailable:
        await store.search("anything")
    assert unavailable.value.code == "memory_backend_unavailable"
    await store.close()


@pytest.mark.asyncio
async def test_outbound_policy_blocks_before_embedding_request(tmp_path: Path) -> None:
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        before = len(_EmbeddingHandler.requests)
        with pytest.raises(Mem0StoreError) as secret:
            await store.add(_fact("api_key=should-not-leave", name="敏感记录"), provenance_seq=1)
        assert secret.value.code == "memory_outbound_blocked"
        with pytest.raises(Mem0StoreError) as evidence:
            await store.search("memory-evidence-0123456789abcdef0123456789abcdef")
        assert evidence.value.code == "memory_outbound_blocked"
        assert len(_EmbeddingHandler.requests) == before
        await store.close()


@pytest.mark.asyncio
async def test_outbound_capture_contains_only_embedding_safe_fields(tmp_path: Path) -> None:
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        await store.add(_procedure_with_private_input(), provenance_seq=1)
        await store.search("怎么筛选当前告警", memory_type="procedure")

        assert _EmbeddingHandler.requests
        for request in _EmbeddingHandler.requests:
            assert request["path"] == "/v1/embeddings"
            body = request["body"]
            assert body["model"] == "test-embedding"
            assert "messages" not in body
            encoded = json.dumps(body, ensure_ascii=False)
            assert "test-key" not in encoded
            assert "memory-evidence-" not in encoded
            assert "view=current" not in encoded
            assert "region=cn" not in encoded
            assert "真实私密输入" not in encoded
        await store.close()


@pytest.mark.asyncio
async def test_mem0_telemetry_does_not_resolve_or_connect_to_external_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved_hosts: list[str] = []
    original_getaddrinfo = socket.getaddrinfo

    def record_getaddrinfo(host, *args, **kwargs):
        resolved_hosts.append(str(host))
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", record_getaddrinfo)
    with _embedding_server() as base_url:
        store = Mem0MemoryStore(_config(tmp_path, base_url))
        store.start()
        await store.add(_fact("无遥测架"), provenance_seq=1)
        await store.search("无遥测架")
        await store.close()

    assert resolved_hosts
    assert all(host in {"127.0.0.1", "localhost"} for host in resolved_hosts)
    assert not any("posthog" in host.casefold() for host in resolved_hosts)


def test_process_close_is_clean_and_same_qdrant_path_reopens(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import os
        from pathlib import Path

        from homemaster.config import HomeMasterConfig
        from homemaster.memory.mem0_store import Mem0MemoryStore
        from homemaster.memory.models import FactRecord

        root = Path(os.environ["MEMORY_TEST_ROOT"])
        base_url = os.environ["MEMORY_TEST_EMBEDDING_URL"]
        config = HomeMasterConfig(
            providers={
                "items": [{
                    "name": "MemoryEmbedding",
                    "kind": "embedding",
                    "api_format": "openai",
                    "base_url": base_url,
                    "embedding_url": f"{base_url}/embeddings",
                    "model": "test-embedding",
                    "api_keys": ["test-key"],
                }]
            },
            memory={
                "root": root / "files",
                "mem0": {
                    "qdrant_path": root / "qdrant",
                    "collection_name": "memory_process_8_v1",
                    "history_db_path": root / "history.sqlite3",
                    "embedding_dimensions": 8,
                    "search_threshold": 0.0,
                },
            },
        )

        async def main():
            store = Mem0MemoryStore(config)
            store.start()
            assert store.available, store.unavailable_cause
            memory_id = os.environ.get("MEMORY_TEST_ID")
            if memory_id:
                item = await store.get(memory_id)
                assert item.record.value == {"container": "进程终态架"}
                print(json.dumps({"status": "reopened", "id": item.memory_id}))
            else:
                item = await store.add(
                    FactRecord(
                        subject={"type": "object", "name": "进程测试物品"},
                        predicate="location",
                        value={"container": "进程终态架"},
                        source="environment_observation",
                    ),
                    provenance_seq=1,
                )
                print(json.dumps({"status": "created", "id": item.memory_id}))
            await store.close()

        asyncio.run(main())
        """
    )
    with _embedding_server() as base_url:
        environment = {
            **os.environ,
            "MEM0_TELEMETRY": "False",
            "HF_HUB_OFFLINE": "1",
            "MEMORY_TEST_ROOT": str(tmp_path),
            "MEMORY_TEST_EMBEDDING_URL": base_url,
        }
        created = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert created.returncode == 0, created.stderr
        created_receipt = json.loads(created.stdout.strip().splitlines()[-1])
        assert created_receipt["status"] == "created"
        assert "Exception ignored" not in created.stderr
        assert "QdrantClient.__del__" not in created.stderr

        reopened = subprocess.run(
            [sys.executable, "-c", script],
            env={**environment, "MEMORY_TEST_ID": created_receipt["id"]},
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert reopened.returncode == 0, reopened.stderr
        reopened_receipt = json.loads(reopened.stdout.strip().splitlines()[-1])
        assert reopened_receipt == {
            "status": "reopened",
            "id": created_receipt["id"],
        }
        assert "Exception ignored" not in reopened.stderr
        assert "QdrantClient.__del__" not in reopened.stderr
