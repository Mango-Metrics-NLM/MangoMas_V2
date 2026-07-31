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
    """
    if url == ":memory:" or url.startswith("file::memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        parsed = urlparse(url)
        # netloc carries the path when only two slashes are present.
        path = parsed.path.lstrip("/")
        combined = f"{parsed.netloc}/{path}" if path else parsed.netloc
        if not combined:
            raise ValueError(f"Empty sqlite URL path: {url!r}")
        return combined
    return url
