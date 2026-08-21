"""Constants shared by more than one settings group.

A constant lands here only when a second group genuinely needs it — keeping
it in its home module and importing across would make the domain modules
depend on each other for no benefit."""

from __future__ import annotations

# Vertex AI provider defaults. ``project_id``/``credentials_path`` have no
# safe defaults — they must be supplied explicitly via env vars when using
# the ``vertex`` provider.
DEFAULT_VERTEX_LOCATION: str = "us-central1"


# Maximum length of the ``detail`` field on structured error envelopes /
# log records. Bounds untrusted exception text so that adapter exception
# bodies (which can include URLs, payload fragments, or remote stack
# traces) can never blow out a log line or an HTTP response body.
DEFAULT_ERROR_DETAIL_TRUNCATE: int = 200
