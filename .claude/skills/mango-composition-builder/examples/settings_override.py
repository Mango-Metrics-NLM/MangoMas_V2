from typing import Any, Dict

def apply_settings_override(base_settings: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically applies setting overrides to a base configuration.
    Useful for environment-specific or user-specific runtime overrides.
    """
    merged_settings = base_settings.copy()
    
    for key, value in overrides.items():
        if isinstance(value, dict) and key in merged_settings and isinstance(merged_settings[key], dict):
            # Recursively apply overrides for nested dictionaries
            merged_settings[key] = apply_settings_override(merged_settings[key], value)
        else:
            merged_settings[key] = value
            
    return merged_settings
