"""Property tests for ``mangomas.adapters.storage._url.path_from_sqlite_url``.

The function is the one place every SQLite consumer (the turn store, the eval
``sqlite_results`` sink) turns a ``MANGOMAS_DB__URL``-shaped value into a
filesystem path. Its unit tests pin the documented examples; these state what
must hold for *every* input: a bare path passes through untouched, the three
URL forms round-trip, ``:memory:`` is preserved, and the only exception the
function ever raises is the documented ``ValueError``.
"""

from __future__ import annotations

import string

from hypothesis import assume, given
from hypothesis import strategies as st

from mangomas.adapters.storage._url import path_from_sqlite_url

_PATH_CHARS = string.ascii_letters + string.digits + "/._-"
_bare_paths = st.text(alphabet=_PATH_CHARS, min_size=1, max_size=64).filter(
    lambda p: not p.startswith(("sqlite:", "file:")) and p != ":memory:"
)
_relative_paths = st.text(alphabet=_PATH_CHARS, min_size=1, max_size=64).filter(
    lambda p: p[0] in string.ascii_letters + string.digits
)


@given(path=_bare_paths)
def test_a_bare_path_passes_through_untouched(path: str) -> None:
    assert path_from_sqlite_url(path) == path


@given(path=_relative_paths)
def test_three_slash_relative_url_round_trips(path: str) -> None:
    assert path_from_sqlite_url(f"sqlite:///{path}") == path


@given(path=_relative_paths)
def test_four_slash_absolute_url_keeps_one_leading_slash(path: str) -> None:
    assert path_from_sqlite_url(f"sqlite:////{path}") == f"/{path}"


@given(path=_relative_paths, query=st.text(alphabet=string.ascii_letters + "=&", max_size=16))
def test_query_parameters_are_stripped(path: str, query: str) -> None:
    assume("?" not in path)
    assert path_from_sqlite_url(f"sqlite:///{path}?{query}") == path


@given(suffix=st.text(max_size=32))
def test_memory_urls_are_preserved(suffix: str) -> None:
    assert path_from_sqlite_url(":memory:") == ":memory:"
    assert path_from_sqlite_url(f"file::memory:{suffix}") == ":memory:"


@given(url=st.text(max_size=128))
def test_the_only_exception_is_the_documented_value_error(url: str) -> None:
    try:
        result = path_from_sqlite_url(url)
    except ValueError:
        # Documented for an empty URL path; the type is the whole contract.
        return
    assert isinstance(result, str)


@given(path=_bare_paths)
def test_normalisation_is_idempotent(path: str) -> None:
    once = path_from_sqlite_url(path)
    assert path_from_sqlite_url(once) == once
