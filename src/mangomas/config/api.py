"""HTTP surface settings: the app itself, auth, and tenancy.

`MANGOMAS_API__*`, `MANGOMAS_AUTH__*`, `MANGOMAS_TENANCY__*`. Grouped because
all three are opt-in hardening of the same surface (ADR-0014 / ADR-0015 /
ADR-0017) and are configured together."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from mangomas.tenancy import DEFAULT_TENANT

DEFAULT_API_HOST: str = "0.0.0.0"  # noqa: S104


DEFAULT_API_PORT: int = 8000


DEFAULT_API_READY_TIMEOUT: float = 2.0


# Opt-in CORS. Empty origins (default) → CORSMiddleware is not installed, so
# behaviour is byte-identical unless MANGOMAS_API__CORS_ALLOW_ORIGINS is set.
# ``credentials`` defaults False: reflecting credentials with a wildcard origin
# is a browser-security footgun, so operators must opt in explicitly.
DEFAULT_API_CORS_ALLOW_ORIGINS: tuple[str, ...] = ()


DEFAULT_API_CORS_ALLOW_METHODS: tuple[str, ...] = ("*",)


DEFAULT_API_CORS_ALLOW_HEADERS: tuple[str, ...] = ("*",)


DEFAULT_API_CORS_ALLOW_CREDENTIALS: bool = False


# Request backpressure (ADR-0015). 0 = off → the middleware is not installed.
DEFAULT_API_MAX_BODY_BYTES: int = 0


DEFAULT_API_MAX_CONCURRENT_REQUESTS: int = 0


# /history read bounds (a user-controlled ``limit`` query param).
DEFAULT_API_HISTORY_DEFAULT_LIMIT: int = 10


DEFAULT_API_HISTORY_MAX_LIMIT: int = 1000


class APISettings(BaseModel):
    """HTTP server configuration."""

    host: str = DEFAULT_API_HOST
    port: int = DEFAULT_API_PORT
    ready_timeout_seconds: float = DEFAULT_API_READY_TIMEOUT
    # Opt-in CORS. Empty origins (default) → CORSMiddleware not installed, so an
    # environment without MANGOMAS_API__CORS_ALLOW_ORIGINS sees no change. The
    # methods/headers/credentials knobs are env-driven too (no hard-coded policy).
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: list(DEFAULT_API_CORS_ALLOW_ORIGINS)
    )
    cors_allow_methods: list[str] = Field(
        default_factory=lambda: list(DEFAULT_API_CORS_ALLOW_METHODS)
    )
    cors_allow_headers: list[str] = Field(
        default_factory=lambda: list(DEFAULT_API_CORS_ALLOW_HEADERS)
    )
    cors_allow_credentials: bool = DEFAULT_API_CORS_ALLOW_CREDENTIALS
    # Request backpressure (0 = off). Bytes cap → 413; in-flight cap → 503.
    max_body_bytes: int = DEFAULT_API_MAX_BODY_BYTES
    max_concurrent_requests: int = DEFAULT_API_MAX_CONCURRENT_REQUESTS
    # /history read bounds (user-controlled ``limit`` query param).
    history_default_limit: int = DEFAULT_API_HISTORY_DEFAULT_LIMIT
    history_max_limit: int = DEFAULT_API_HISTORY_MAX_LIMIT


# Opt-in API authentication (ADR-0014). Default OFF → the auth dependency is a
# no-op pass-through, so the HTTP surface is byte-identical.
DEFAULT_AUTH_ENABLED: bool = False


DEFAULT_AUTH_SECRET_REF: str | None = None


class AuthSettings(BaseModel):
    """Opt-in API authentication (ADR-0014).

    Gated by ``enabled`` (default ``False``): when off, the FastAPI auth
    dependency is a no-op. When on, the expected token is resolved from
    ``secret_ref`` via the configured ``SecretsProvider`` and compared
    (constant-time) against ``Authorization: Bearer`` / ``X-API-Key``.
    """

    enabled: bool = DEFAULT_AUTH_ENABLED
    secret_ref: str | None = DEFAULT_AUTH_SECRET_REF

    @model_validator(mode="after")
    def _validate_auth(self) -> AuthSettings:
        """Require a secret reference when enabled so auth is never keyless."""
        if self.enabled and not self.secret_ref:
            raise ValueError(
                "auth.enabled requires auth.secret_ref "
                "(set MANGOMAS_AUTH__SECRET_REF to a SecretsProvider reference)"
            )
        return self


# Multi-tenancy (ADR-0017). Default OFF → a single implicit DEFAULT_TENANT, so
# storage behaviour is byte-identical. DEFAULT_TENANT is owned by tenancy.py.
DEFAULT_TENANCY_ENABLED: bool = False


DEFAULT_TENANCY_HEADER: str = "X-Tenant-ID"


class TenancySettings(BaseModel):
    """Tenant scoping for conversation storage (ADR-0017).

    Gated by ``enabled`` (default ``False``): when off, no middleware is installed
    and the storage repositories use the implicit ``default`` tenant, so behaviour
    is byte-identical. When on, the ``header`` value is sanitised into a
    ``ContextVar`` per request; ``default`` is used when the header is absent.
    """

    enabled: bool = DEFAULT_TENANCY_ENABLED
    header: str = DEFAULT_TENANCY_HEADER
    default: str = DEFAULT_TENANT
