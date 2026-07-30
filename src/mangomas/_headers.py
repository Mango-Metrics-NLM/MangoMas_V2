"""Shared sanitiser for identity tokens derived from inbound HTTP headers.

SECURITY INVARIANT — shared allowed-charset
-------------------------------------------
The allowed character set ``[A-Za-z0-9_\\-./:]`` is the single log-injection /
SQL-parameter defence for every identity token read from an inbound HTTP
header. Two consumers share it:

* :func:`mangomas.correlation.sanitize_inbound_correlation_id` — the
  ``X-Request-ID`` value injected into log records and the OTel baggage.
* :func:`mangomas.tenancy.sanitize_tenant` — the ``X-Tenant-ID`` value that
  scopes storage reads/writes (it reaches SQL as a bound parameter).

Widening the set widens **both** attack surfaces at once: CR/LF (log
line-splitting), NUL/control characters, quotes, and shell/HTML
metacharacters must never survive sanitisation. The set covers the canonical
UUID/hex/url-safe forms without enabling free-form text. Each consumer keeps
its own named max-length constant and public wrapper; only the
strip → charset-filter → truncate mechanics live here.
"""

from __future__ import annotations

import re

# Anything OUTSIDE this set is stripped from inbound values. Read the module
# docstring before changing it — the charset is a shared security invariant.
_ALLOWED_CHAR_PATTERN: re.Pattern[str] = re.compile(r"[^A-Za-z0-9_\-./:]")


def sanitize_header_token(raw: str, *, max_length: int) -> str:
    """Return *raw* stripped, filtered to the allowed charset, and truncated.

    The transformation is, in order:

    1. Strip surrounding whitespace.
    2. Remove characters outside the allowed set (alphanumerics + ``_-./:``)
       — the log-injection / SQL-parameter defence: CR/LF, tabs, and control
       characters all get dropped.
    3. Truncate to *max_length* characters.

    Returns the empty string when nothing survives (whitespace-only input, or
    input composed entirely of disallowed characters) — callers translate that
    to their own "absent" signal (usually ``None``).
    """
    stripped = raw.strip()
    if not stripped:
        return ""
    return _ALLOWED_CHAR_PATTERN.sub("", stripped)[:max_length]
