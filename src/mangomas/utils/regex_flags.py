"""Regex flag names shared by the eval scorer and workflow predicates.

``workflow`` and ``eval`` are independent packages (import-linter), so neither
may import the other. Both may import ``utils``.
"""

from __future__ import annotations

import re

from mangomas.errors import ConfigError

REGEX_FLAG_BY_NAME: dict[str, re.RegexFlag] = {
    "ignorecase": re.IGNORECASE,
    "multiline": re.MULTILINE,
    "dotall": re.DOTALL,
}


def resolve_regex_flags(names: list[str]) -> int:
    """OR-combine named flags. An unknown name is a config error, not a no-op."""
    flags = 0
    for name in names:
        flag = REGEX_FLAG_BY_NAME.get(name.lower())
        if flag is None:
            known = sorted(REGEX_FLAG_BY_NAME)
            raise ConfigError(f"unknown regex flag {name!r}; valid: {known}")
        flags |= flag
    return flags
