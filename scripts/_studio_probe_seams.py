"""Process seams the Studio consumer probe installs for one run (spec-0041).

Each is a context manager that restores what it changed: the CORS env var and
the settings cache, the ``NO_PROXY`` env vars, httpx's real transports, and the
``lmstudio`` registry entry.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

import httpx
from _openai_compat_stub import build_stub_app
from _studio_probe_model import MODE_MOCK, PROVIDER, ProbeConfig

from mangomas.adapters.llm.lmstudio import LMStudioClient
from mangomas.composition import llm_registry
from mangomas.config import LLMSettings, get_settings

logger = logging.getLogger("studio_consumer_probe")

CORS_ORIGINS_ENV = "MANGOMAS_API__CORS_ALLOW_ORIGINS"
NO_PROXY_ENVS: tuple[str, ...] = ("NO_PROXY", "no_proxy")
BYPASS_ALL_PROXIES = "*"


@contextmanager
def _scoped_env(values: dict[str, str]) -> Iterator[None]:
    """Set *values* for the block, then restore each name exactly, including absence."""
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, old in previous.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


@contextmanager
def cors_allow_origin(origin: str) -> Iterator[None]:
    """Expose *origin* to ``create_app``, which reads CORS from ``get_settings``."""
    with _scoped_env({CORS_ORIGINS_ENV: json.dumps([origin])}):
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()


@contextmanager
def bypass_env_proxies() -> Iterator[list[httpx.AsyncClient]]:
    """Make live clients connect directly, so the host audit sees the real peer.

    The audit records ``request.url.host``, which is the destination even when
    httpx (``trust_env=True``) tunnels through ``HTTP(S)_PROXY`` / ``ALL_PROXY``
    or, on Windows, the registry proxy. ``NO_PROXY=*`` disables every source.
    """
    logger.debug("environment proxies bypassed for the live run")
    with _scoped_env(dict.fromkeys(NO_PROXY_ENVS, BYPASS_ALL_PROXIES)):
        yield []


@contextmanager
def record_network_hosts() -> Iterator[set[str]]:
    """Record the host of every request that reaches a real httpx transport.

    ``ASGITransport`` traffic never reaches these classes, so in mock mode the
    set stays empty unless some component opened a socket on its own.
    """
    hosts: set[str] = set()
    original_async = httpx.AsyncHTTPTransport.handle_async_request
    original_sync = httpx.HTTPTransport.handle_request

    async def _async(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        hosts.add(request.url.host)
        logger.debug("real transport request", extra={"host": request.url.host})
        return await original_async(self, request)

    def _sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        hosts.add(request.url.host)
        logger.debug("real transport request", extra={"host": request.url.host})
        return original_sync(self, request)

    httpx.AsyncHTTPTransport.handle_async_request = _async  # type: ignore[method-assign]
    httpx.HTTPTransport.handle_request = _sync  # type: ignore[method-assign]
    try:
        yield hosts
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original_async  # type: ignore[method-assign]
        httpx.HTTPTransport.handle_request = original_sync  # type: ignore[method-assign]


@contextmanager
def stub_llm_provider(models: list[str]) -> Iterator[list[httpx.AsyncClient]]:
    """Route every ``lmstudio`` client the composition root builds to the stub.

    An injected httpx client is not owned by the adapter, so the yielded list
    lets the caller close each one after the orchestrator is closed.
    """
    stub = build_stub_app(models)
    clients: list[httpx.AsyncClient] = []

    def _factory(cfg: LLMSettings) -> LMStudioClient:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stub), timeout=cfg.timeout_seconds
        )
        clients.append(http)
        logger.debug("stub client built", extra={"model": cfg.model})
        return LMStudioClient(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=cfg.api_key,
            timeout_seconds=cfg.timeout_seconds,
            default_temperature=cfg.temperature,
            client=http,
        )

    with llm_registry.scoped(PROVIDER, _factory):
        yield clients


def provider_seam(cfg: ProbeConfig) -> AbstractContextManager[list[httpx.AsyncClient]]:
    """The stub in mock mode; the real ``lmstudio`` factory, proxy-free, in live mode."""
    if cfg.mode == MODE_MOCK:
        return stub_llm_provider([cfg.draft_model, cfg.review_model])
    return bypass_env_proxies()
