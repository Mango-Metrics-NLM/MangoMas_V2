"""LLM provider settings (`MANGOMAS_LLM__*`)."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field, model_validator

from mangomas.config._shared import DEFAULT_VERTEX_LOCATION

# ── Module-level defaults (single source of truth) ────────────────────────────

DEFAULT_LLM_PROVIDER: str = "lmstudio"


DEFAULT_LLM_BASE_URL: str = "http://localhost:1234/v1"


DEFAULT_LLM_MODEL: str = "local-model"


DEFAULT_LLM_API_KEY: str = "lm-studio"


DEFAULT_LLM_TIMEOUT_SECONDS: float = 60.0


DEFAULT_LLM_TEMPERATURE: float = 0.2


# ── Sub-settings models ────────────────────────────────────────────────────────


# Empty means unconstrained, so an environment that sets nothing behaves
# exactly as before. A non-empty list is an approved-model roster: every model
# this deployment may build, including the base ``model`` itself — exempting the
# default would make the allowlist a loophole rather than a control (ADR-0033).
DEFAULT_LLM_ALLOWED_MODELS: list[str] = []


# Providers the base client *and every named profile* may use (spec-0040 R4).
# Empty means unconstrained; ``["lmstudio"]`` is the local-only configuration.
# Enforced at ``Settings`` parse so a disallowed profile is never built and no
# request-time check exists to bypass. Scoped to the LLM seam on purpose — the
# name promises no control over secrets, embeddings or telemetry egress
# (ADR-0033).
DEFAULT_LLM_PROVIDER_ALLOWLIST: list[str] = []


#: ``AgentRequest.metadata`` / ``AgentResponse.metadata`` key naming the
#: selected provider profile (spec-0040 R2/R5). Defined here, in the lowest
#: layer, because three layers read it — ``agents/`` (the resolver),
#: ``composition/`` (the stamp) and ``workflow/`` (the fan-out join that must
#: carry a run-scoped selector across a synthesised response) — and none of
#: them may import the others for a string.
LLM_PROFILE_METADATA_KEY: Final[str] = "llm_profile"


class LLMSettings(BaseModel):
    """LLM endpoint configuration (LM Studio by default; OpenAI-compatible).

    Vertex-specific fields (``project_id``, ``location``, ``credentials_path``)
    are optional and only consulted when ``provider == "vertex"``. They default
    to ``None``/``DEFAULT_VERTEX_LOCATION`` so existing LM Studio deployments
    see no behaviour change.
    """

    provider: str = DEFAULT_LLM_PROVIDER
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    api_key: str = DEFAULT_LLM_API_KEY
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    temperature: float = DEFAULT_LLM_TEMPERATURE
    # Optional reference resolved via the SecretsProvider seam. When set,
    # the resolved value overrides ``api_key`` at orchestrator-build time.
    # For Vertex this resolved value is treated as a service-account JSON body.
    # See ``mangomas.secrets`` and ``composition._lmstudio_factory``.
    secret_ref: str | None = None
    # Vertex-specific fields (required only when provider="vertex").
    project_id: str | None = None
    location: str = DEFAULT_VERTEX_LOCATION
    credentials_path: str | None = None
    # Approved-model roster. Empty (the default) means unconstrained, so an
    # environment that sets nothing behaves exactly as before.
    allowed_models: list[str] = Field(default_factory=lambda: list(DEFAULT_LLM_ALLOWED_MODELS))
    # Approved-provider roster covering this base client and every
    # ``MANGOMAS_LLM_PROFILES__<NAME>__PROVIDER`` (spec-0040 R4). The profile
    # half is checked by the root ``Settings`` validator, which is the only
    # place that sees both.
    provider_allowlist: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LLM_PROVIDER_ALLOWLIST)
    )

    @model_validator(mode="after")
    def _validate_provider_allowlist(self) -> LLMSettings:
        """A non-empty provider allowlist must cover the base provider itself.

        Same rule as :meth:`_validate_allowed_models`: the one provider that is
        always built is the one an exemption would leave ungoverned.
        """
        if self.provider_allowlist and self.provider not in self.provider_allowlist:
            raise ValueError(
                f"llm.provider {self.provider!r} is not in llm.provider_allowlist "
                f"{self.provider_allowlist!r}; add it or leave the allowlist empty"
            )
        return self

    @model_validator(mode="after")
    def _validate_allowed_models(self) -> LLMSettings:
        """A non-empty allowlist must cover the deployment's own base model.

        Exempting the default would make the allowlist a loophole rather than a
        control: the one model guaranteed to be built is the one it would not
        govern. Failing at Settings parse means the misconfiguration surfaces
        at startup rather than at the first agent that happens to opt in.
        """
        if self.allowed_models and self.model not in self.allowed_models:
            raise ValueError(
                f"llm.model {self.model!r} is not in llm.allowed_models "
                f"{self.allowed_models!r}; add it or leave the allowlist empty"
            )
        return self


class LLMProfileSettings(BaseModel):
    """One named LLM provider profile (``MANGOMAS_LLM_PROFILES__<NAME>__*``; spec-0040).

    A profile is a second ``LLMClient`` built at startup in the composition
    root and selected per run through ``AgentRequest.metadata["llm_profile"]``.
    ``provider`` and ``model`` are required — a half-declared profile fails at
    ``Settings`` parse rather than silently inheriting the base provider.
    Every other field is ``None`` by default and **inherits the matching
    ``MANGOMAS_LLM__*`` value** when unset, with one exception: the credential
    fields (``api_key``, ``secret_ref``, ``credentials_path``) inherit only
    when the profile's ``provider`` equals the base provider. A cloud profile
    under a local base never receives the local bearer, and vice versa.

    ``allowed_models`` and ``provider_allowlist`` are never per-profile; the
    base ``LLMSettings`` rosters govern every profile (the root ``Settings``
    validator enforces both). Profile names are lowercased by pydantic-settings
    and matched exactly at request time.
    """

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    secret_ref: str | None = None
    project_id: str | None = None
    location: str | None = None
    credentials_path: str | None = None
    timeout_seconds: float | None = None
    temperature: float | None = None
