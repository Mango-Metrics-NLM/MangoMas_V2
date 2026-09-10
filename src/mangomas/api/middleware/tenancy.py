"""Opt-in tenancy scoping (ADR-0017).

Installed only when tenancy is enabled. Reads + sanitises the header and sets
the ``tenant_id`` ContextVar so storage repositories scope their SQL to it;
resets it on the way out so the tenant never leaks across requests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from mangomas.tenancy import resolve_tenant, set_tenant
from mangomas.tenancy import tenant_id as _tenant_var


class TenancyMiddleware(BaseHTTPMiddleware):
    """Set the per-request tenant from the configured header (ADR-0017)."""

    def __init__(self, app: ASGIApp, *, header: str, default: str) -> None:
        super().__init__(app)
        self._header = header
        self._default = default

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        tenant = resolve_tenant(request.headers.get(self._header), self._default)
        # set_tenant is the canonical setter; it returns the ContextVar Token so
        # the finally block can restore the pre-request value exactly.
        token = set_tenant(tenant)
        try:
            return await call_next(request)
        finally:
            _tenant_var.reset(token)
