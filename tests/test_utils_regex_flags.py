"""Branch coverage for the shared regex-flag helper (utils floor is 100%)."""

from __future__ import annotations

import re

import pytest

from mangomas.errors import ConfigError
from mangomas.utils.regex_flags import REGEX_FLAG_BY_NAME, resolve_regex_flags


def test_no_names_is_a_zero_mask() -> None:
    assert resolve_regex_flags([]) == 0


def test_names_are_or_combined_and_case_insensitive() -> None:
    combined = resolve_regex_flags(["IGNORECASE", "multiline"])
    assert combined == re.IGNORECASE | re.MULTILINE
    assert combined & REGEX_FLAG_BY_NAME["dotall"] == 0


def test_an_unknown_name_names_itself_and_the_valid_set() -> None:
    with pytest.raises(ConfigError, match="nope") as caught:
        resolve_regex_flags(["nope"])
    message = str(caught.value)
    for name in sorted(REGEX_FLAG_BY_NAME):
        assert name in message
