from typing import Any, Dict
from mangomas.config import Settings

def apply_settings_override(base_settings: Settings, overrides: Dict[str, Any]) -> Settings:
    "\""
    Dynamically applies setting overrides to a base configuration.
    Useful for environment-specific or user-specific runtime overrides.
    Returns a validated Settings model.
    "\""
    # Dump base settings to a dict, merge overrides, and re-validate
    base_dict = base_settings.model_dump()
    
    # Simple top-level merge for demonstration
    merged_dict = {**base_dict, **overrides}
    
    return Settings.model_validate(merged_dict)
