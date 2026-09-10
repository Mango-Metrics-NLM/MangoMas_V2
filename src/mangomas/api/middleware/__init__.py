"""HTTP middleware facade (ADR-0019).

Permanent re-export of the backpressure, tenancy, and access-log classes
that used to live in ``api/middleware.py``. ``create_app`` install order
is unchanged. ``error_envelope`` and ``_BAGGAGE_KEY`` stay on this surface
so existing imports keep working.

Call sites that *build* 413/503 bodies go through the ``mangomas.api.errors``
module object, not the bound name here — see the seam commit that preceded
this split.
"""

from __future__ import annotations

from mangomas.api import errors as api_errors
from mangomas.api.middleware.access_log import _BAGGAGE_KEY as _BAGGAGE_KEY
from mangomas.api.middleware.access_log import AccessLogMiddleware as AccessLogMiddleware
from mangomas.api.middleware.backpressure import (
    ConcurrencyLimitMiddleware as ConcurrencyLimitMiddleware,
)
from mangomas.api.middleware.backpressure import MaxBodySizeMiddleware as MaxBodySizeMiddleware
from mangomas.api.middleware.tenancy import TenancyMiddleware as TenancyMiddleware

# Facade identity with the one construction site (tests/test_api_envelope.py).
error_envelope = api_errors.error_envelope

__all__ = [
    "AccessLogMiddleware",
    "ConcurrencyLimitMiddleware",
    "MaxBodySizeMiddleware",
    "TenancyMiddleware",
    "error_envelope",
]
