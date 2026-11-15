"""PostgreSQL DSN parsing and fail-closed TCP reachability probe (stdlib-only).

Extracted from ``scripts/verify_gcp_prerequisites.py`` (spec-0049) so RFC 3986
authority ambiguity checks, IPv6/port validation, credential redaction, and
TCP connect probing live in a cohesive, independently testable module.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import parse_qsl

MIN_TCP_PORT: Final[int] = 1
MAX_TCP_PORT: Final[int] = 65535
LIBPQ_DEFAULT_PORT: Final[int] = 5432

# The schemes the runtime accepts: asyncpg takes postgresql:// and postgres://
# (adapters/storage/postgres.py::_normalise_dsn rewrites the latter).
POSTGRES_SCHEMES: Final[frozenset[str]] = frozenset({"postgres", "postgresql"})
# RFC 3986: the authority ends at the first of these delimiters.
URL_AUTHORITY_DELIMITERS: Final[str] = "/?#"
# asyncpg honours these query parameters over the authority; the probe does not.
POSTGRES_QUERY_ENDPOINT_OVERRIDES: Final[frozenset[str]] = frozenset({"host", "port", "service"})
# Authority characters this probe cannot interpret like asyncpg does:
# ',' (multi-host lists) and '%' (percent-encoded hosts, e.g. socket paths).
POSTGRES_UNSUPPORTED_HOST_CHARS: Final[str] = ",%"
REDACTED_PORT_PLACEHOLDER: Final[str] = "<PORT>"
PERCENT_ENCODING_HINT: Final[str] = (
    "Percent-encode reserved characters in the user/password "
    "('@' -> %40, '/' -> %2F, '?' -> %3F, '#' -> %23, ':' -> %3A) and any '@' in the "
    "database path or query, as libpq/asyncpg require."
)
UNSUPPORTED_ENDPOINT_HINT: Final[str] = (
    "Probe one TCP endpoint explicitly: --db-url postgresql://<user>@<HOST>:<PORT>/<db>"
)
REDACTED_ENDPOINT_LABEL: Final[str] = "the MANGOMAS_DB__URL endpoint"
REDACTED_ENDPOINT_NOTE: Final[str] = (
    "host/port withheld: the URL has no user@ part, so a 'user:password' typed "
    "without '@host' would read as host:port"
)


@dataclass(frozen=True)
class PostgresParseFailure:
    """A sanitized, static report row for one class of URL parse failure."""

    needle: str
    message: str
    details: dict[str, Any] = field(default_factory=lambda: {"host": None, "port": None})
    remediation: str | None = None


@dataclass(frozen=True)
class PostgresProbeOutcome:
    """Sanitized outcome produced by :func:`probe_postgres_endpoint`."""

    status: Literal["PASS", "FAIL", "SKIP"]
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    remediation: str | None = None


# First match wins: more specific needles come before their prefixes.
POSTGRES_PARSE_FAILURES: Final[tuple[PostgresParseFailure, ...]] = (
    PostgresParseFailure(
        "ambiguous",
        "MANGOMAS_DB__URL is ambiguous: its user/password and host cannot be told apart "
        "the way asyncpg reads them, so an unencoded reserved character is likely in the "
        "password, path or query.",
        remediation=PERCENT_ENCODING_HINT,
    ),
    PostgresParseFailure(
        "not probed",
        "MANGOMAS_DB__URL is not probed: it uses a multi-host list, a percent-encoded "
        "host, or a ?host=/?port=/?service= override, which asyncpg resolves differently from "
        "a single TCP probe.",
        remediation=UNSUPPORTED_ENDPOINT_HINT,
    ),
    PostgresParseFailure("names no host", "MANGOMAS_DB__URL names no host."),
    PostgresParseFailure(
        "invalid port 0", "Invalid port 0 (1-65535).", details={"host": None, "port": 0}
    ),
    PostgresParseFailure(
        "unterminated ipv6", "MANGOMAS_DB__URL does not parse: unterminated IPv6 literal."
    ),
    PostgresParseFailure(
        "invalid host format", "MANGOMAS_DB__URL does not parse: invalid host format."
    ),
    PostgresParseFailure(
        "port out of range",
        "MANGOMAS_DB__URL does not parse: port out of range (1-65535).",
        remediation=PERCENT_ENCODING_HINT,
    ),
    PostgresParseFailure(
        "invalid port",
        "MANGOMAS_DB__URL does not parse: invalid port.",
        remediation=PERCENT_ENCODING_HINT,
    ),
    PostgresParseFailure("missing scheme", "MANGOMAS_DB__URL does not parse: missing scheme."),
    PostgresParseFailure(
        "unsupported scheme", "MANGOMAS_DB__URL does not parse: unsupported scheme."
    ),
)


def parse_bracketed_ipv6(host_port: str) -> tuple[str, str | None]:
    """Extract ``(ipv6_host, raw_port)`` from a bracketed authority ``[addr][:port]``."""
    closing = host_port.find("]")
    if closing == -1:
        raise ValueError("MANGOMAS_DB__URL does not parse: unterminated IPv6 literal.")
    host = host_port[1:closing]
    rest = host_port[closing + 1 :]
    try:
        ipaddress.IPv6Address(host)
    except ipaddress.AddressValueError as exc:
        raise ValueError("MANGOMAS_DB__URL does not parse: invalid host format.") from exc
    if rest.startswith(":"):
        return host, rest[1:]
    if not rest:
        return host, None
    raise ValueError("MANGOMAS_DB__URL does not parse: invalid host format.")


def parse_host_and_port(host_port: str, *, has_userinfo: bool = True) -> tuple[str, str | None]:
    """Isolate host and raw port string from authority ``host_port``."""
    if host_port.startswith("["):
        return parse_bracketed_ipv6(host_port)
    if "[" in host_port or "]" in host_port:
        raise ValueError("MANGOMAS_DB__URL does not parse: invalid host format.")
    if ":" not in host_port:
        return host_port, None
    host, _, raw_port = host_port.partition(":")
    if ":" in raw_port:
        if not has_userinfo:
            raise ValueError(
                "MANGOMAS_DB__URL is ambiguous: multiple colons without user@ separator."
            )
        raise ValueError("MANGOMAS_DB__URL does not parse: invalid host format.")
    return host, raw_port


def postgres_authority(db_url: str) -> tuple[str, bool]:
    """Return ``(host[:port] text, authority carried userinfo)`` per RFC 3986."""
    if not db_url or not db_url.strip():
        raise ValueError("MANGOMAS_DB__URL names no host.")
    url = db_url.strip()
    if "://" not in url:
        raise ValueError("MANGOMAS_DB__URL does not parse: missing scheme.")
    scheme, _, after_scheme = url.partition("://")
    if scheme.lower() not in POSTGRES_SCHEMES:
        raise ValueError("MANGOMAS_DB__URL does not parse: unsupported scheme.")
    end = next(
        (i for i, char in enumerate(after_scheme) if char in URL_AUTHORITY_DELIMITERS),
        len(after_scheme),
    )
    authority, remainder = after_scheme[:end], after_scheme[end:]
    at_signs = authority.count("@")
    if at_signs > 1 or (at_signs == 0 and "@" in remainder):
        raise ValueError("MANGOMAS_DB__URL is ambiguous: '@' outside a single user@ separator.")
    host_port = authority.partition("@")[2] if at_signs else authority
    query = remainder.partition("?")[2].partition("#")[0]
    overridden = {key.lower() for key, _ in parse_qsl(query, keep_blank_values=True)}
    if overridden & POSTGRES_QUERY_ENDPOINT_OVERRIDES or any(
        char in host_port for char in POSTGRES_UNSUPPORTED_HOST_CHARS
    ):
        raise ValueError("MANGOMAS_DB__URL is not probed: unsupported endpoint form.")
    return host_port, bool(at_signs)


def parse_postgres_endpoint(db_url: str) -> tuple[str, int]:
    """Safely extract ``(host, port)`` from a database URL without leaking credentials."""
    host_port, has_userinfo = postgres_authority(db_url)
    if not host_port:
        raise ValueError("MANGOMAS_DB__URL names no host.")

    host, raw_port = parse_host_and_port(host_port, has_userinfo=has_userinfo)
    if not host:
        raise ValueError("MANGOMAS_DB__URL names no host.")
    if host.isascii() and host.isdigit():
        raise ValueError("MANGOMAS_DB__URL is ambiguous: all-digit host.")

    if raw_port is None:
        port = LIBPQ_DEFAULT_PORT
    else:
        if not (raw_port.isascii() and raw_port.isdigit()):
            raise ValueError("MANGOMAS_DB__URL does not parse: invalid port.")
        port = int(raw_port)

    if not (MIN_TCP_PORT <= port <= MAX_TCP_PORT):
        if port == 0:
            raise ValueError("Invalid port 0 (1-65535).")
        raise ValueError("MANGOMAS_DB__URL does not parse: port out of range (1-65535).")
    return host, port


def postgres_parse_failure_outcome(exc: ValueError) -> PostgresProbeOutcome:
    """Map a parser error to its static row; the URL never reaches the report."""
    msg_lower = str(exc).lower()
    failure = next(
        (f for f in POSTGRES_PARSE_FAILURES if f.needle in msg_lower),
        PostgresParseFailure("", "MANGOMAS_DB__URL does not parse."),
    )
    return PostgresProbeOutcome(
        "FAIL",
        failure.message,
        details=dict(failure.details),
        remediation=failure.remediation,
    )


def probe_postgres_endpoint(
    db_url: str | None,
    timeout: float,
    *,
    connect_fn: Callable[..., Any] = socket.create_connection,
    log: logging.Logger | None = None,
) -> PostgresProbeOutcome:
    """TCP-probe the host/port of a ``postgres*://`` URL without leaking credentials."""
    if db_url is None:
        return PostgresProbeOutcome("SKIP", "Skipped: storage provider is not postgres.")

    try:
        _, has_userinfo = postgres_authority(db_url)
        host, port = parse_postgres_endpoint(db_url)
    except ValueError as exc:
        return postgres_parse_failure_outcome(exc)

    if has_userinfo:
        label = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        details: dict[str, Any] = {"host": host, "port": port}
        port_hint = str(port)
        suffix = ""
    else:
        label = REDACTED_ENDPOINT_LABEL
        details = {"host": None, "port": None, "redacted": True}
        port_hint = REDACTED_PORT_PLACEHOLDER
        suffix = f"; {REDACTED_ENDPOINT_NOTE}"

    active_log = log or logging.getLogger(__name__)
    active_log.debug("probe=postgres_tcp redacted=%s", not has_userinfo)
    try:
        with connect_fn((host, port), timeout=timeout):
            pass
    except (OSError, ValueError, OverflowError) as exc:
        error = type(exc).__name__
        active_log.debug("probe=postgres_tcp error=%s redacted=%s", error, not has_userinfo)
        return PostgresProbeOutcome(
            "FAIL",
            f"Nothing accepts TCP on {label} ({error}{suffix}).",
            details={**details, "error": error},
            remediation=(
                "Start the proxy: cloud-sql-proxy <PROJECT>:<REGION>:<INSTANCE> "
                f"--port {port_hint}\n"
                "Or use SQLite locally: MANGOMAS_DB__PROVIDER=sqlite"
            ),
        )
    return PostgresProbeOutcome(
        "PASS",
        f"TCP reachable at {label} (proxy or local Postgres{suffix}).",
        details=details,
    )
