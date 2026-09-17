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
    base_dict = base_settings.model_dump()
    merged_dict = {**base_dict, **overrides}
    return Settings.model_validate(merged_dict)
