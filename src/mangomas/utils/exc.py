"""Bounded exception text for typed-error ``detail`` fields.

Every adapter boundary that wraps a third-party failure in a
:class:`~mangomas.errors.MangomasError` carries the original exception's type
and message in ``detail`` — but **bounded**, because that text is untrusted
(a remote stack trace, an upstream response body) and would otherwise blow out
a log line or an error envelope (spec 0014 / D2). This helper is the single
spelling of that rule; the former per-call-site copies could drift on the
bound or the format.
"""

from __future__ import annotations

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE

__all__ = ["truncated_exc_detail"]


def truncated_exc_detail(exc: BaseException, *, limit: int = DEFAULT_ERROR_DETAIL_TRUNCATE) -> str:
    """Return ``"<TypeName>: <message>"`` for ``exc``, cut to ``limit`` characters."""
    return f"{type(exc).__name__}: {exc}"[:limit]
