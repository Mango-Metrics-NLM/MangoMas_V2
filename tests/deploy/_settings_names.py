"""Every ``MANGOMAS_*`` name the settings tree declares — sibling of ``_makefile.py``.

Two contracts need the same walk: ``test_env_example_contract.py`` (every name
in ``.env.example`` and the instruction docs resolves to a field) and
``test_manifest_provider_contract.py`` (every env name a rendered Cloud Run
manifest sets resolves to a field). One copy keeps their notion of "declared"
identical, so a renamed field is reported by both.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from mangomas.config import AgentSettings, Settings

PREFIX: Final[str] = str(Settings.model_config.get("env_prefix", ""))
NESTED_DELIMITER: Final[str] = str(Settings.model_config.get("env_nested_delimiter", ""))
# `agents` is a dict keyed by agent name, so a plain `model_fields` walk cannot
# resolve a per-agent name; it is handled in its slug-elided shape instead.
AGENTS_FIELD: Final[str] = "AGENTS"


def declared_names() -> set[str]:
    """Return every ``MANGOMAS_*`` name the settings tree actually declares.

    Per-agent names are returned in their ``MANGOMAS_AGENTS__<FIELD>`` shape
    with the slug elided; :func:`normalize` collapses a real name to match.
    """
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for sub in annotation.model_fields:
                names.add(f"{PREFIX}{name.upper()}{NESTED_DELIMITER}{sub.upper()}")
        else:
            names.add(f"{PREFIX}{name.upper()}")
    for sub in AgentSettings.model_fields:
        names.add(f"{PREFIX}{AGENTS_FIELD}{NESTED_DELIMITER}{sub.upper()}")
    return names


def normalize(name: str) -> str:
    """Collapse ``MANGOMAS_AGENTS__<SLUG>__<FIELD>`` to ``MANGOMAS_AGENTS__<FIELD>``."""
    parts = name.split(NESTED_DELIMITER)
    if len(parts) == 3 and parts[0] == f"{PREFIX}{AGENTS_FIELD}":
        return f"{parts[0]}{NESTED_DELIMITER}{parts[2]}"
    return name


def settings_groups() -> set[str]:
    """Every bare ``MANGOMAS_<GROUP>__`` prefix, including the dict-valued agents group."""
    groups = {
        f"{PREFIX}{name.upper()}{NESTED_DELIMITER}"
        for name, field in Settings.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }
    groups.add(f"{PREFIX}{AGENTS_FIELD}{NESTED_DELIMITER}")
    return groups


def unresolved(names: set[str]) -> list[str]:
    """Return the names in *names* that resolve to neither a field nor a group prefix."""
    declared = declared_names()
    groups = settings_groups()
    bad: list[str] = []
    for name in names:
        normalized = normalize(name)
        if normalized.endswith(NESTED_DELIMITER):
            if normalized not in groups:
                bad.append(name)
        elif normalized not in declared:
            bad.append(name)
    return sorted(bad)
