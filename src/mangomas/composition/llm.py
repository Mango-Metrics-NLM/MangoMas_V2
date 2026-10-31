"""LLM provider factories.

Factories for instantiating LLM clients from configuration. Each factory
accepts LLMSettings and returns a fully-initialized LLMClient implementation.

Lazy imports (e.g., Vertex SDK) are deferred so optional extras stay optional.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from mangomas.adapters.llm import LMStudioClient
from mangomas.agents._prompt import (
    AGENT_LLM_OVERRIDES_EXTRAS_KEY,
    LLM_PROFILE_METADATA_KEY,
    LLM_PROFILES_EXTRAS_KEY,
    select_llm_profile,
)
from mangomas.composition.secrets import _resolve_llm_secrets
from mangomas.config import AgentSettings, LLMProfileSettings, LLMSettings
from mangomas.errors import ConfigError

if TYPE_CHECKING:
    from mangomas.adapters.llm.base import LLMClient
    from mangomas.core.agent import AgentContext, AgentRequest, AgentResponse
    from mangomas.core.loop import AcceptanceFn

logger = logging.getLogger(__name__)

# Fields a profile inherits from the base ``LLMSettings`` only when it names the
# *same* provider (spec-0039). A Vertex profile under an LM Studio base must
# not receive the local bearer as its service-account body, and an LM Studio
# profile under a Vertex base must not receive the SA JSON as its bearer.
_CREDENTIAL_FIELDS: Final[frozenset[str]] = frozenset({"api_key", "secret_ref", "credentials_path"})


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
        if base_llm_cfg.allowed_models and override_model not in base_llm_cfg.allowed_models:
            # Raised, not skipped: silently ignoring an unapproved override
            # would run the agent on the *base* model while its configuration
            # claimed otherwise — a worse outcome than refusing to start.
            raise ConfigError(
                f"agent {agent_name!r} requests model {override_model!r}, which is not in "
                f"MANGOMAS_LLM__ALLOWED_MODELS {base_llm_cfg.allowed_models!r}"
            )
        if override_model not in built_by_model:
            override_cfg = base_llm_cfg.model_copy(update={"model": override_model})
            built_by_model[override_model] = llm_registry.get(override_cfg.provider)(override_cfg)
            logger.debug(
                "Building agent-scoped LLM override client",
                extra={"agent": agent_name, "model": override_model},
            )
        overrides[agent_name] = built_by_model[override_model]
    return overrides


class LLMProfiles(NamedTuple):
    """What :func:`build_llm_profiles` returns: the clients and a secret-free summary."""

    clients: dict[str, LLMClient]
    info: dict[str, dict[str, str]]


def _merge_profile(name: str, profile: LLMProfileSettings, base: LLMSettings) -> LLMSettings:
    """Overlay *profile* on *base*, applying the credential-inheritance rule.

    Unset profile fields (``None``) inherit the base value; set fields win.
    Credentials inherit only for a same-provider profile. A profile that sets
    ``api_key`` without ``secret_ref`` also clears any inherited ``secret_ref``,
    otherwise secret resolution would overwrite the explicit key with the base
    secret. ``allowed_models`` / ``provider_allowlist`` are always the base's —
    the root ``Settings`` validator has already checked this profile against
    them, so nothing here re-decides policy.
    """
    update = profile.model_dump(exclude_none=True)
    if profile.provider != base.provider:
        for field in _CREDENTIAL_FIELDS - set(update):
            # A different provider never inherits the base credential; fall
            # back to the field's own declared default instead.
            update[field] = LLMSettings.model_fields[field].get_default()
    if profile.api_key is not None and profile.secret_ref is None:
        update["secret_ref"] = None
    merged = base.model_copy(update=update)
    logger.debug(
        "Merged LLM profile",
        extra={"profile": name, "provider": merged.provider, "model": merged.model},
    )
    return merged


def build_llm_profiles(
    profiles_cfg: dict[str, LLMProfileSettings],
    base_llm_cfg: LLMSettings,
    secrets_provider: str,
) -> LLMProfiles:
    """Build one ``LLMClient`` per named profile (spec-0039 R1).

    Each profile is merged onto the **raw** base settings (not the already
    secret-resolved client config, so a base secret is resolved once and a
    profile-level ``secret_ref`` resolves on its own), run through
    :func:`~mangomas.composition.secrets._resolve_llm_secrets`, and built by
    the provider factory registered in ``llm_registry`` — so a profile may name
    a *different* provider than the base, which is the whole point. The
    common case (no profile configured) returns two empty dicts: zero extra
    clients, zero extra connections. The ``info`` half carries only
    ``provider`` and ``model`` — never a key, a ref or a URL — because it is
    what ``GET /llm/profiles`` serialises (R6).
    """
    from mangomas.composition._registries import llm_registry  # noqa: PLC0415

    clients: dict[str, LLMClient] = {}
    info: dict[str, dict[str, str]] = {}
    for name, profile in profiles_cfg.items():
        cfg = _resolve_llm_secrets(_merge_profile(name, profile, base_llm_cfg), secrets_provider)
        clients[name] = llm_registry.get(cfg.provider)(cfg)
        info[name] = {"provider": cfg.provider, "model": cfg.model}
        logger.debug(
            "Building LLM profile client",
            extra={"profile": name, "provider": cfg.provider, "model": cfg.model},
        )
    return LLMProfiles(clients=clients, info=info)


class _LLMProfileDispatchMixin:
    """Validate the requested profile before dispatch; stamp it on the response.

    spec-0039 R2/R5 at the one site every non-streaming topology passes
    through: ``dispatch_pipeline`` and ``dispatch_fan_out`` both delegate to
    ``dispatch``. Placed **first** in the base list of both composition
    orchestrators, so an unknown profile raises :class:`ConfigError` before
    :class:`~mangomas.composition.recording._FailureRecordingMixin` and before
    ``Orchestrator.dispatch``'s metrics — no agent ran, so it is not a turn,
    the same position-based rule recording.py applies to ``max_steps < 1``.

    The stamp is a copy, not a mutation (``AgentResponse`` is mutable, but the
    orchestrator's own convention is to re-wrap). Because ``_pipeline_pass``
    seeds each next stage's request with ``response.metadata``, stamping here
    is also what carries a profile across every pipeline stage. Streams are
    not stamped — there is no response object — so the SSE ``metadata`` frame
    carries the name instead (``api/routes/agents.py``).
    """

    if TYPE_CHECKING:  # pragma: no cover
        context: AgentContext

    async def dispatch(
        self,
        agent_name: str,
        request: AgentRequest,
        *,
        acceptance_fn: AcceptanceFn | None = None,
        max_steps: int | None = None,
    ) -> AgentResponse:
        # Mirrors ``Orchestrator.dispatch``'s signature exactly, for the reason
        # recording.py gives: ``**kwargs`` would silently drop a keyword a
        # future dispatch grows.
        profile = select_llm_profile(self.context.extras, request.metadata)
        response: AgentResponse = await super().dispatch(  # type: ignore[misc]
            agent_name,
            request,
            acceptance_fn=acceptance_fn,
            max_steps=max_steps,
        )
        if profile is None:
            return response
        return response.model_copy(
            update={"metadata": {**response.metadata, LLM_PROFILE_METADATA_KEY: profile}}
        )


class _AgentLLMOverrideCloseMixin:
    """Extends ``Orchestrator._close_hooks`` to close per-agent override and profile clients.

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
            AGENT_LLM_OVERRIDES_EXTRAS_KEY, {}
        )
        for agent_name, client in overrides.items():
            if hasattr(client, "aclose"):
                hooks.append((f"LLM override ({agent_name})", client.aclose))
        # Named profile clients (spec-0039) close on the same path.
        profiles: dict[str, LLMClient] = self.context.extras.get(  # type: ignore[attr-defined]
            LLM_PROFILES_EXTRAS_KEY, {}
        )
        for profile_name, client in profiles.items():
            if hasattr(client, "aclose"):
                hooks.append((f"LLM profile ({profile_name})", client.aclose))
        return hooks


__all__ = [
    "LLMProfiles",
    "_AgentLLMOverrideCloseMixin",
    "_LLMProfileDispatchMixin",
    "_lmstudio_factory",
    "_vertex_factory",
    "build_agent_llm_overrides",
    "build_llm_profiles",
]
