"""Claude Code harness settings (`MANGOMAS_HARNESS__*`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DEFAULT_HARNESS_ENABLED: bool = False


DEFAULT_HARNESS_METRICS_NAMESPACE: str = "mangomas.harness"


DEFAULT_HARNESS_HOOK_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING"] = "INFO"


# "inherit" reuses the global application exporter (no behaviour change).
DEFAULT_HARNESS_METRICS_EXPORTER: Literal["inherit", "console", "gcp"] = "inherit"


# "off" reproduces today's exact behavior (no ConfigChange hook existed before
# ADR-0021). "audit" logs governed-source (project_settings/local_settings)
# changes without blocking; "block" blocks them. See
# mangomas.harness.config_audit.evaluate_config_change.
DEFAULT_HARNESS_CONFIG_AUDIT_MODE: Literal["off", "audit", "block"] = "off"


class HarnessSettings(BaseModel):
    """Claude Code harness telemetry and hook configuration.

    All fields default to safe no-op values so existing callers behave
    identically when this group is absent from the environment.
    """

    enabled: bool = DEFAULT_HARNESS_ENABLED
    metrics_namespace: str = DEFAULT_HARNESS_METRICS_NAMESPACE
    hook_log_level: Literal["DEBUG", "INFO", "WARNING"] = DEFAULT_HARNESS_HOOK_LOG_LEVEL
    # Route harness.agent_invoke spans to a dedicated exporter, or "inherit" the
    # global application exporter (default → no behaviour change).
    metrics_exporter: Literal["inherit", "console", "gcp"] = DEFAULT_HARNESS_METRICS_EXPORTER
    # scripts/harness_config_audit.py: mangomas.harness.config_audit.ConfigChangeMode.
    config_audit_mode: Literal["off", "audit", "block"] = DEFAULT_HARNESS_CONFIG_AUDIT_MODE
