"""Every ``MANGOMAS_*`` name the settings tree declares — sibling of ``_makefile.py``.

Two contracts need the same walk: ``test_env_example_contract.py`` (every name
in ``.env.example`` and the instruction docs resolves to a field) and
``test_manifest_provider_contract.py`` (every env name a rendered Cloud Run
manifest sets resolves to a field). One copy keeps their notion of "declared"
identical, so a renamed field is reported by both.
"""

from __future__ import annotations

from typing import Final, get_args, get_origin

from pydantic import BaseModel

from mangomas.config import Settings

PREFIX: Final[str] = str(Settings.model_config.get("env_prefix", ""))
NESTED_DELIMITER: Final[str] = str(Settings.model_config.get("env_nested_delimiter", ""))


def dict_model_groups() -> dict[str, type[BaseModel]]:
    """Settings fields typed ``dict[str, <BaseModel>]``, keyed by upper-cased field name.

    ``agents`` (``dict[str, AgentSettings]``) and ``llm_profiles``
    (``dict[str, LLMProfileSettings]``) are keyed by a free-form slug, so a plain
    ``model_fields`` walk cannot resolve a ``MANGOMAS_<GROUP>__<SLUG>__<FIELD>``
    name. Deriving the set from the annotations (rather than naming ``AGENTS``
    by hand, as this module once did) means a new dict-valued group is covered
    by both contracts the moment it is declared.
    """
    groups: dict[str, type[BaseModel]] = {}
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is dict:
            key_type, value_type = get_args(annotation)
            if (
                key_type is str
                and isinstance(value_type, type)
                and issubclass(value_type, BaseModel)
            ):
                groups[name.upper()] = value_type
    return groups


def declared_names() -> set[str]:
    """Return every ``MANGOMAS_*`` name the settings tree actually declares.

    Names under a dict-valued group are returned in their
    ``MANGOMAS_<GROUP>__<FIELD>`` shape with the slug elided; :func:`normalize`
    collapses a real name to match.
    """
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for sub in annotation.model_fields:
                names.add(f"{PREFIX}{name.upper()}{NESTED_DELIMITER}{sub.upper()}")
        else:
            names.add(f"{PREFIX}{name.upper()}")
    for group, model in dict_model_groups().items():
        for sub in model.model_fields:
            names.add(f"{PREFIX}{group}{NESTED_DELIMITER}{sub.upper()}")
    return names


def normalize(name: str) -> str:
    """Collapse ``MANGOMAS_<GROUP>__<SLUG>__<FIELD>`` to ``MANGOMAS_<GROUP>__<FIELD>``.

    Only for dict-valued groups; every other name is returned unchanged.
    """
    parts = name.split(NESTED_DELIMITER)
    dict_prefixes = {f"{PREFIX}{group}" for group in dict_model_groups()}
    if len(parts) == 3 and parts[0] in dict_prefixes:
        return f"{parts[0]}{NESTED_DELIMITER}{parts[2]}"
    return name


def settings_groups() -> set[str]:
    """Every bare ``MANGOMAS_<GROUP>__`` prefix, including the dict-valued groups."""
    groups = {
        f"{PREFIX}{name.upper()}{NESTED_DELIMITER}"
        for name, field in Settings.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }
    for group in dict_model_groups():
        groups.add(f"{PREFIX}{group}{NESTED_DELIMITER}")
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
