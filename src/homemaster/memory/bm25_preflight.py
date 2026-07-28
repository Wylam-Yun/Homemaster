"""Offline integrity preflight for the locked Qdrant BM25 artifact."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from homemaster.config import REPO_ROOT

FASTEMBED_CACHE_DIR = REPO_ROOT / ".cache" / "homemaster" / "fastembed"
BM25_COMMIT = "e499a1f8d6bec960aab5533a0941bf914e70faf9"
_BM25_MODEL_DIRECTORY = "models--Qdrant--bm25"
_BM25_ARTIFACT_DIRECTORY = "bm25_artifact"
BM25_HASHES = {
    "arabic.txt": "0df01c0a184d8c15077c6f0ee70e25e0c0308d827b92b14a19f5f819a0c465d0",
    "config.json": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    "danish.txt": "1bedc9cf5a8830dacf8c4ee0d8b301f0801861756ad0d504431d01047f961b0c",
    "dutch.txt": "e5a2a7c390fe3ad0c0a132586ed11492b635d66a1f426cd89677f80b07bc76a6",
    "english.txt": "019f104ba2ed07436d05f9cdd3383034ad66014edc27fc651f837e1a038b6451",
    "finnish.txt": "952af766edc9b8e7ddc877fc464cbd94b91754b5621fdfdd7020568fd4813fcd",
    "french.txt": "58ac7f7c074b70dc0fc86f6fcf40b2c227e9f95627e90e57971d5cce1e30e2e9",
    "german.txt": "f0c25acd7ec02a31f6679d9664ec5222ca846fdbc58db96160f4e7c0ddb0f7ea",
    "greek.txt": "7a97c6dc81144f7e8fcfe976025954ca3731f78a75d1fd86aa87f261fd195dae",
    "hungarian.txt": "64a68b5ebaa616b25bd976ac915c326f2ea8cce8b59a0ac8c5728b980ae4fb0c",
    "italian.txt": "293d7841f198e4012f49e8e5653c3bfd073a58cea1259fa2c0fcec894167b628",
    "norwegian.txt": "f7e5b42208ccf1b1f282f9e0f8570e464272762bda5718b6b26750f510a688dc",
    "portuguese.txt": "6e7b98378c6b728266a83ebf035d927abf16b9e0d815d6204d81d90d896bcc9b",
    "romanian.txt": "0ab53d21cbde00c6bf367706a0243c646140ba564cce7e83ac33d80f8923e957",
    "russian.txt": "1743191192b4a4f77fcc216499455dc00c1b8626fdd407076a8deefff80e3d59",
    "spanish.txt": "6125eadf28ba664a60bf4296147bcbd40b80be93670056fdb229960ac15e2310",
    "swedish.txt": "2a9d9d756bc4257d49329994f805c712cd5ab6746162e2082752238766c1c0c8",
    "turkish.txt": "f2c7f0c2bd3dba42da700776266831853a3b5f1207fada5c15207334109abb57",
}


def configure_bm25_offline_cache(cache_dir: Path = FASTEMBED_CACHE_DIR) -> Path:
    """Materialize the packaged BM25 artifact and make FastEmbed use it offline."""

    resolved_cache_dir = cache_dir.expanduser().absolute()
    _materialize_bm25_artifact(resolved_cache_dir)
    os.environ["FASTEMBED_CACHE_PATH"] = str(resolved_cache_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return resolved_cache_dir


def verify_bm25_offline(cache_dir: Path = FASTEMBED_CACHE_DIR) -> Path:
    """Verify the packaged artifact through FastEmbed's real offline load path."""

    from fastembed import SparseTextEmbedding

    resolved_cache_dir = configure_bm25_offline_cache(cache_dir)
    encoder = SparseTextEmbedding(
        model_name="Qdrant/bm25",
        cache_dir=str(resolved_cache_dir),
        local_files_only=True,
    )
    model_dir = Path(encoder.model._model_dir)
    if model_dir.name != BM25_COMMIT:
        raise RuntimeError(f"unexpected BM25 artifact revision: {model_dir.name}")
    observed = {path.name for path in model_dir.iterdir() if path.is_file()}
    if observed != set(BM25_HASHES):
        raise RuntimeError("BM25 artifact file set differs from the lock")
    for name, expected in BM25_HASHES.items():
        digest = hashlib.sha256((model_dir / name).read_bytes()).hexdigest()
        if digest != expected:
            raise RuntimeError(f"BM25 artifact checksum mismatch: {name}")
    encoded = list(encoder.embed(["中文记忆关键词校验"]))
    if len(encoded) != 1 or not encoded[0].values.size:
        raise RuntimeError("BM25 Chinese preflight returned no sparse values")
    if not all(math.isfinite(float(value)) for value in encoded[0].values):
        raise RuntimeError("BM25 Chinese preflight returned non-finite values")
    return model_dir


def _materialize_bm25_artifact(cache_dir: Path) -> Path:
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cache_dir, 0o700)
    model_dir = cache_dir / _BM25_MODEL_DIRECTORY
    snapshot_dir = model_dir / "snapshots" / BM25_COMMIT

    with _exclusive_cache_lock(cache_dir):
        if not _matches_lock(snapshot_dir):
            snapshot_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=f".{BM25_COMMIT}.", dir=snapshot_dir.parent))
            try:
                for name, expected in BM25_HASHES.items():
                    _atomic_write(staging_dir / name, _packaged_artifact_bytes(name, expected))
                if snapshot_dir.exists():
                    shutil.rmtree(snapshot_dir)
                staging_dir.replace(snapshot_dir)
            finally:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir)
        refs_dir = model_dir / "refs"
        refs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_write(refs_dir / "main", BM25_COMMIT.encode("ascii"))

    return snapshot_dir


def _packaged_artifact_bytes(name: str, expected: str) -> bytes:
    source = resources.files(__package__).joinpath(_BM25_ARTIFACT_DIRECTORY, name)
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() == expected:
        return content
    # apply_patch represents every text resource with a final newline. The upstream
    # artifact has three files without one, so restore only that proven byte change.
    if content.endswith(b"\n") and hashlib.sha256(content[:-1]).hexdigest() == expected:
        return content[:-1]
    raise RuntimeError(f"packaged BM25 artifact checksum mismatch: {name}")


def _matches_lock(model_dir: Path) -> bool:
    if not model_dir.is_dir():
        return False
    observed = {path.name for path in model_dir.iterdir() if path.is_file()}
    if observed != set(BM25_HASHES):
        return False
    return all(
        hashlib.sha256((model_dir / name).read_bytes()).hexdigest() == expected
        for name, expected in BM25_HASHES.items()
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _exclusive_cache_lock(cache_dir: Path) -> Iterator[None]:
    lock_path = cache_dir / ".homemaster-bm25.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows does not provide flock
            yield
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "BM25_COMMIT",
    "BM25_HASHES",
    "FASTEMBED_CACHE_DIR",
    "configure_bm25_offline_cache",
    "verify_bm25_offline",
]
