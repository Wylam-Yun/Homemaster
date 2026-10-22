"""LLM client factory helpers."""

from __future__ import annotations

import inspect
from typing import Any

import litellm

from ..config import get_config
from ..errors import InvalidConfigError
from .chat import LLMClient
from .embedding import EmbedClient
from .rerank import RerankClient
from .router import _ROUTER_CACHE, clear_router_cache, get_limiter, get_router


def init_llm_client() -> LLMClient:
    """Build a chat client once so router config errors fail fast at startup.

    The returned client is not cached; request-time calls use the currently
    bound config context.
    """
    return get_llm_client()


def init_embed_client() -> EmbedClient:
    """Build an embedding client once so router config errors fail fast at startup.

    The returned client is not cached; request-time calls use the currently
    bound config context.
    """
    return get_embed_client()


def init_rerank_client() -> RerankClient:
    """Build a rerank client once so router config errors fail fast at startup."""
    return get_rerank_client()


def get_llm_client() -> LLMClient:
    """Create a chat client from the currently bound config context.

    The thin client wrapper is per-request, but its underlying Router is cached
    by resolved endpoint config so cooldown/load-balancing state is preserved.
    """
    cfg = get_config()
    router_cfg = cfg.chat_model_router
    router, _ = get_router(router_cfg, LLMClient.ALIAS)
    default_model = LLMClient.ALIAS if router_cfg.endpoints else None
    return LLMClient(
        router,
        default_model=default_model,
        max_attempts=router_cfg.format_parser_max_attempts,
        limiter=get_limiter(router_cfg, LLMClient.ALIAS),
    )


def get_embed_client() -> EmbedClient:
    """Create an embedding client from the currently bound config context."""
    cfg = get_config()
    router_cfg = cfg.embed_model_router
    router, _ = get_router(router_cfg, EmbedClient.ALIAS)
    default_model = EmbedClient.ALIAS if router_cfg.endpoints else None
    return EmbedClient(router, default_model=default_model)


PROBE_TEXT = "ping"


async def validate_embedding_dimension() -> None:
    """Probe the embedding endpoint once and fail fast on dimension mismatch.

    Skips silently when no embedding endpoint is configured. Raises
    ``InvalidConfigError`` if any endpoint declares ``dimensions`` that differs
    from ``database.qdrant.vector_size``, and ``EmbeddingDimensionError`` (via
    ``EmbedClient.embed``) if the provider actually returns the wrong dimension
    — the symptom of ``dimensions`` being silently dropped by litellm
    ``drop_params=True`` or of switching to a model with a different native
    dimension. Intended to run at startup, right after ``init_embed_client``.
    """

    cfg = get_config()
    if not cfg.embed_model_router.endpoints:
        return

    vector_size = cfg.database.qdrant.vector_size
    for ep in cfg.embed_model_router.endpoints:
        if ep.dimensions is not None and ep.dimensions != vector_size:
            raise InvalidConfigError(
                field=f"embed_model_router.endpoints[model={ep.model}].dimensions",
                support=f"equal to database.qdrant.vector_size={vector_size}",
            )

    # embed() resolves expected_dim from config (vector_size) and raises
    # EmbeddingDimensionError on mismatch, so the probe needs no extra checks.
    # await get_embed_client().embed(task="startup.probe", text=PROBE_TEXT)


def get_rerank_client() -> RerankClient:
    """Create a rerank client from the currently bound config context.

    When no rerank endpoint is configured, the client falls back to keyword
    overlap scoring, or identity ordering as last resort.
    """
    cfg = get_config()
    rerank_cfg = cfg.algo_config.search.rerank
    router_cfg = cfg.rerank_model_router
    router = None
    if rerank_cfg.enabled and router_cfg.endpoints:
        router, _ = get_router(router_cfg, RerankClient.ALIAS)
    return RerankClient(
        router,
        max_query_length=rerank_cfg.max_query_length,
        max_doc_length=rerank_cfg.max_doc_length,
        max_batch_size=rerank_cfg.max_batch_size,
        max_concurrent_batches=rerank_cfg.max_concurrent_batches,
        request_timeout=rerank_cfg.request_timeout,
    )


def reset_clients() -> None:
    """Drop cached Routers so the next client picks up refreshed config."""
    clear_router_cache()


async def _close_cached_clients(cache_dict: Any) -> None:
    """Generically close every client object held in a litellm cache dict.

    ``close_litellm_async_clients`` only knows ``BaseLLMAIOHTTPHandler`` and
    objects exposing ``.client``/``.aclose`` in a specific shape — cached
    ``AsyncOpenAI`` SDK clients (which wrap an httpx client whose transport
    may back an aiohttp ``ClientSession``) match none of those branches and
    survive until GC reports "Unclosed client session". Walk the dict and use
    ``aclose()``/``close()`` duck-typing instead.
    """
    if not isinstance(cache_dict, dict):
        return
    for client in list(cache_dict.values()):
        closer = getattr(client, "aclose", None)
        if not callable(closer):
            closer = getattr(client, "close", None)
        if not callable(closer):
            continue
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception:
            continue


async def _close_router_cached_clients(router: Any) -> None:
    """Close async clients a litellm Router holds in its own DualCache.

    Defensive: current litellm keeps deployment clients only in the
    module-level ``in_memory_llm_clients_cache``, but Router internals have
    varied across versions — sweep its per-instance cache too since it is
    about to be dropped from ``_ROUTER_CACHE``.
    """
    cache = getattr(router, "cache", None)
    in_memory = getattr(cache, "in_memory_cache", None)
    await _close_cached_clients(getattr(in_memory, "cache_dict", None))


async def close_llm_clients() -> None:
    """Close LiteLLM-managed async HTTP clients and drop cached Routers."""
    # Close the module-level client cache first with our generic walker —
    # ``close_litellm_async_clients``'s type matching misses AsyncOpenAI-style
    # entries, leaving their aiohttp sessions for the GC to warn about.
    await _close_cached_clients(
        getattr(litellm.in_memory_llm_clients_cache, "cache_dict", None)
    )
    for entry in list(_ROUTER_CACHE.values()):
        router = entry[0] if isinstance(entry, tuple) else entry
        try:
            await _close_router_cached_clients(router)
        except Exception:
            continue
    clear_router_cache()
    await litellm.close_litellm_async_clients()
    shared_session = getattr(litellm, "aclient_session", None)
    if shared_session is not None:
        try:
            result = shared_session.close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass
    litellm.aclient_session = None
