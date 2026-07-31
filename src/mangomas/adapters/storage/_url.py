"""Shared SQLite URL/path normalisation.

Promoted out of :mod:`mangomas.adapters.storage.sqlite` so every consumer that
accepts a SQLite connection target — the ``SQLiteRepository`` turn store and
the eval ``sqlite_results`` sink — resolves ``sqlite:///...`` URLs the same
way. Before this existed, the sink used its ``db_path`` option verbatim: a
value copied from ``MANGOMAS_DB__URL`` (e.g. ``sqlite:///./data/mangomas.db``)
would create a literal directory named ``sqlite:`` next to a file named
``/data/mangomas.db`` instead of resolving to the intended path.
"""

from __future__ import annotations

from urllib.parse import urlparse


def path_from_sqlite_url(url: str) -> str:
    """Parse ``sqlite:///path`` URLs; fall back to bare paths or ``:memory:``.

    ``sqlite:///abs/path.db`` -> ``abs/path.db`` (absolute-style, three slashes).
    ``sqlite://relative.db``  -> ``relative.db`` (netloc form, two slashes).
    ``:memory:`` / ``file::memory:...`` -> ``:memory:``.
    Anything else is treated as a bare filesystem path.
    Query parameters (e.g. ?check_same_thread=false) are stripped.
    """
    if url == ":memory:" or url.startswith("file::memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        # Extract path component, stripping query params but preserving path structure
        parsed = urlparse(url)
        path = parsed.path
        if not path:
            raise ValueError(f"Empty sqlite URL path: {url!r}")
        # For relative paths (sqlite:///rel.db -> path="/rel.db"),
        # strip leading /; for absolute paths (sqlite:////abs/db -> path="//abs/db"),
        # preserve one leading / (the second one in the original URL).
        if path.startswith("//"):
            # Absolute path: //abs/db -> /abs/db
            return path[1:]
        else:
            # Relative path: /rel/db -> rel/db
            return path.lstrip("/")
    if url.startswith("sqlite://"):
        parsed = urlparse(url)
        # netloc carries the path when only two slashes are present.
        path = parsed.path.lstrip("/")
        combined = f"{parsed.netloc}/{path}" if path else parsed.netloc
        if not combined:
            raise ValueError(f"Empty sqlite URL path: {url!r}")
        return combined
    return url
