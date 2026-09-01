"""LLM provider factories.

Factories for instantiating LLM clients from configuration. Each factory
accepts LLMSettings and returns a fully-initialized LLMClient implementation.

Lazy imports (e.g., Vertex SDK) are deferred so optional extras stay optional.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mangomas.adapters.llm import LMStudioClient
from mangomas.config import AgentSettings, LLMSettings

if TYPE_CHECKING:
    from mangomas.adapters.llm.base import LLMClient

logger = logging.getLogger(__name__)


def _lmstudio_factory(cfg: LLMSettings) -> LMStudioClient:
    """Build an LMStudioClient from LLMSettings.

    Returns an HTTP-based LLM client configured to connect to a local LM Studio server.
    """
    logger.debug(
        "Building LMStudioClient",
        extra={"base_url": cfg.base_url, "model": cfg.model},
    )
    return LMStudioClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


def _vertex_factory(cfg: LLMSettings) -> Any:
    """Build a VertexClient from LLMSettings.

    Returns a Google Cloud Vertex AI client. When ``secret_ref`` is set,
    ``api_key`` is treated as a service-account JSON body and forwarded as
    ``credentials_json``. Otherwise falls back to ``credentials_path`` (or
    Application Default Credentials when both are absent).

    VertexClient is imported from the composition module (rather than directly
    from adapters.llm) to allow tests to monkeypatch it. The SDK is lazily
    loaded inside VertexClient's constructor, keeping the optional ``vertex``
    extra genuinely optional.
    """
    import mangomas.composition as composition_module  # noqa: PLC0415

    logger.debug(
        "Building VertexClient",
        extra={"project_id": cfg.project_id, "location": cfg.location, "model": cfg.model},
    )
    credentials_json = cfg.api_key if cfg.secret_ref else None
    return composition_module.VertexClient(
        project_id=cfg.project_id,
        location=cfg.location,
        model=cfg.model,
        credentials_path=cfg.credentials_path,
        credentials_json=credentials_json,
        timeout_seconds=cfg.timeout_seconds,
        default_temperature=cfg.temperature,
    )


def build_agent_llm_overrides(
    agents_cfg: dict[str, AgentSettings], base_llm_cfg: LLMSettings
) -> dict[str, LLMClient]:
    """Build one ``LLMClient`` per agent whose ``model_override`` differs from the shared default.

    Reuses the same provider factory registered in ``llm_registry`` that
    builds the shared client, swapping only ``model`` — secrets, timeouts,
    and ``base_url`` are inherited from *base_llm_cfg* (already
    secret-resolved by the caller, see ``composition/builder.py``). Agents
    that set the same override model share one client instance rather than
    opening duplicate connections. An unset, blank/whitespace, or
    default-equal ``model_override`` is skipped, so the common case (no
    agent opts in) returns ``{}`` — zero extra clients, zero extra
    connections. See spec-0028 / ADR-0028.
    """
    from mangomas.composition._registries import llm_registry  # noqa: PLC0415

    overrides: dict[str, LLMClient] = {}
    built_by_model: dict[str, LLMClient] = {}
    for agent_name, agent_settings in agents_cfg.items():
        override_model = agent_settings.model_override
        if not override_model or not override_model.strip():
            continue
        if override_model == base_llm_cfg.model:
            continue
        if override_model not in built_by_model:
            override_cfg = base_llm_cfg.model_copy(update={"model": override_model})
            built_by_model[override_model] = llm_registry.get(override_cfg.provider)(override_cfg)
            logger.debug(
                "Building agent-scoped LLM override client",
                extra={"agent": agent_name, "model": override_model},
            )
        overrides[agent_name] = built_by_model[override_model]
    return overrides


class _AgentLLMOverrideCloseMixin:
    """Extends ``Orchestrator._close_hooks`` to close per-agent override clients.

    ``Orchestrator.aclose()`` dispatches to ``self._close_hooks()`` via
    ordinary Python method resolution, so overriding just this method from
    the composition layer — without editing the protected
    ``core/orchestrator.py`` — is enough for override clients built by
    :func:`build_agent_llm_overrides` to be closed on the same
    fault-isolated, idempotent path as ``ctx.llm``. Mirrors
    ``composition/harness.py``'s existing ``_HarnessOrchestrator``
    subclass-to-extend pattern. See ADR-0028 for why this was chosen over
    editing ``core/orchestrator.py`` directly.
    """

    def _close_hooks(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = super()._close_hooks()  # type: ignore[misc]
        overrides: dict[str, LLMClient] = self.context.extras.get(  # type: ignore[attr-defined]
            "agent_llm_overrides", {}
        )
        for agent_name, client in overrides.items():
            if hasattr(client, "aclose"):
                hooks.append((f"LLM override ({agent_name})", client.aclose))
        return hooks


__all__ = [
    "_AgentLLMOverrideCloseMixin",
    "_lmstudio_factory",
    "_vertex_factory",
    "build_agent_llm_overrides",
]
