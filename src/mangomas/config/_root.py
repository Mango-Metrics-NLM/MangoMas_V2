"""The top-level `Settings` aggregate and its cached accessor.

Imports every group module, so it is the one place that knows the full shape.
Group modules never import this, which keeps the dependency direction acyclic."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mangomas.config.agents import AgentSettings, LoopSettings
from mangomas.config.api import APISettings, AuthSettings, TenancySettings
from mangomas.config.evaluation import EvalSettings
from mangomas.config.harness import HarnessSettings
from mangomas.config.llm import LLMProfileSettings, LLMSettings
from mangomas.config.observability import LogSettings, TelemetrySettings
from mangomas.config.rag import EmbeddingSettings, RagSettings, VectorSettings
from mangomas.config.secrets import SecretsSettings
from mangomas.config.signal import SignalSettings
from mangomas.config.storage import DBSettings, MemorySettings
from mangomas.config.workflow import WorkflowSettings


class Settings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_prefix="MANGOMAS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "dev", "qa", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    db: DBSettings = Field(default_factory=DBSettings)
    api: APISettings = Field(default_factory=APISettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    tenancy: TenancySettings = Field(default_factory=TenancySettings)
    log: LogSettings = Field(default_factory=LogSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)

    # Per-agent overrides keyed by agent name.
    agents: dict[str, AgentSettings] = Field(default_factory=dict)

    # Named LLM provider profiles keyed by profile name (spec-0040). Empty by
    # default: no extra client is built and dispatch is byte-identical.
    llm_profiles: dict[str, LLMProfileSettings] = Field(default_factory=dict)

    loop: LoopSettings = Field(default_factory=LoopSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    signal: SignalSettings = Field(default_factory=SignalSettings)

    # Set to True (MANGOMAS_DISCOVERY_ENABLED=true) to enable entry-point-based
    # plugin discovery for eval components (mangomas.eval.*) and agents
    # (mangomas.agents). Default False keeps only built-in providers registered.
    discovery_enabled: bool = False

    # Set to True (MANGOMAS_DISCOVERY_ALLOW_BUILTIN_OVERRIDE=true) to let a
    # discovered eval plugin replace a built-in scorer/sink/target/source of
    # the same name. Default False refuses the collision (ADR-0030): those
    # registries feed the CI quality gate and the regression baseline, so
    # last-call-wins let an installed package decide whether the gate passes.
    # Agent discovery has always refused built-in collisions; this closes the
    # asymmetry without removing the capability.
    discovery_allow_builtin_override: bool = False

    @model_validator(mode="after")
    def _validate_llm_profiles(self) -> Settings:
        """Every profile must satisfy the base LLM rosters (spec-0040 R3/R4).

        The only root-level validator: the provider allowlist and the model
        roster live on ``llm`` while the profiles live beside it, so no group
        can check this alone. Failing here means a disallowed profile is never
        built, which is what makes ``PROVIDER_ALLOWLIST`` a startup control
        rather than a request-time check something could bypass.
        """
        for name, profile in self.llm_profiles.items():
            if self.llm.provider_allowlist and profile.provider not in self.llm.provider_allowlist:
                raise ValueError(
                    f"llm_profiles[{name!r}].provider {profile.provider!r} is not in "
                    f"llm.provider_allowlist {self.llm.provider_allowlist!r}"
                )
            if self.llm.allowed_models and profile.model not in self.llm.allowed_models:
                raise ValueError(
                    f"llm_profiles[{name!r}].model {profile.model!r} is not in "
                    f"llm.allowed_models {self.llm.allowed_models!r}"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
