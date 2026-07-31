"""Shared Langfuse SDK bootstrap for the optional sink + dataset source.

``eval/sinks/langfuse.py`` and ``eval/sources/langfuse.py`` both need to (a)
lazily import the optional ``langfuse`` SDK, raising a clear
:class:`~mangomas.errors.ConfigError` when the ``langfuse`` extra is not
installed, and (b) construct a ``langfuse.Langfuse`` client from a
caller-supplied options dict, forwarding every option the SDK constructor
doesn't already have a dedicated keyword for. This module holds both
mechanisms so the two call sites cannot drift apart.

The two callers keep their own option names/behaviour for anything that
differs: the sink's ``per_row`` flag and the source's ``dataset`` name are
both *dropped* from the kwargs forwarded to ``langfuse.Langfuse(...)`` via
the same ``drop_keys`` filter, but reading ``per_row``'s value (the sink
needs it; the source has nothing analogous) stays caller-side.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mangomas.errors import ConfigError


def import_langfuse_module(*, owner: str) -> Any:
    """Import the optional ``langfuse`` SDK or raise a clear ``ConfigError``.

    ``owner`` names the caller (e.g. ``"langfuse sink"``) so the message
    matches what that caller wants surfaced.
    """
    try:
        import langfuse  # noqa: PLC0415
    except ImportError as exc:
        raise ConfigError(
            f"{owner} requires the 'langfuse' extra: pip install 'mangomas[langfuse]'"
        ) from exc
    return langfuse


def build_langfuse_client(
    options: dict[str, Any] | None,
    *,
    owner: str,
    drop_keys: Iterable[str] = (),
) -> Any:
    """Import the SDK and construct a ``langfuse.Langfuse`` client.

    ``drop_keys`` lists option keys the *caller* owns (e.g. ``per_row`` for
    the sink, ``dataset`` for the dataset source) so they are excluded from
    the kwargs forwarded to the SDK constructor rather than rejected as
    unknown kwargs. Any construction failure (bad credentials, unexpected
    kwarg, ...) is re-raised as :class:`ConfigError`.
    """
    langfuse = import_langfuse_module(owner=owner)
    drop = set(drop_keys)
    client_options = {k: v for k, v in (options or {}).items() if k not in drop}
    try:
        return langfuse.Langfuse(**client_options)
    except Exception as exc:
        raise ConfigError(f"invalid Langfuse configuration: {exc}") from exc
