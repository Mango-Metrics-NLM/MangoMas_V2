"""Settings override example for the mango-composition-builder skill."""

from __future__ import annotations

from typing import Any

from mangomas.config import Settings


def apply_settings_override(
    base_settings: Settings,
    overrides: dict[str, Any],
) -> Settings:
    """Dynamically apply overrides to a base configuration.

    Return a validated Settings model for environment-specific runtime overrides.
    """
    def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
        result = base.copy()
        for k, v in over.items():
            if isinstance(v, dict) and k in result and isinstance(result[k], dict):
                result[k] = _deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    base_dict = base_settings.model_dump()
    merged_dict = _deep_merge(base_dict, overrides)
    return Settings.model_validate(merged_dict)
