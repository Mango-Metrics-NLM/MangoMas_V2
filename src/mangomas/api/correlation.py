"""Backwards-compatible re-export shim for the correlation module.

The canonical home for correlation primitives is :mod:`mangomas.correlation`
(top-level). This shim exists so existing imports of
``from mangomas.api.correlation import ...`` continue to work without
disruption.

New code should import directly from :mod:`mangomas.correlation`.
"""

from __future__ import annotations

from mangomas.correlation import (
    MAX_CORRELATION_ID_LENGTH,
    CorrelationFilter,
    correlation_id,
    generate_correlation_id,
    get_correlation_id,
    resolve_correlation_id,
    sanitize_inbound_correlation_id,
    set_correlation_id,
)

__all__ = [
    "MAX_CORRELATION_ID_LENGTH",
    "CorrelationFilter",
    "correlation_id",
    "generate_correlation_id",
    "get_correlation_id",
    "resolve_correlation_id",
    "sanitize_inbound_correlation_id",
    "set_correlation_id",
]
