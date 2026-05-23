"""GCP Secret Manager :class:`SecretsProvider` implementation.

Satisfies the sync :class:`SecretsProvider` protocol
(:mod:`mangomas.secrets.provider`). Credentials are sourced from
Application Default Credentials / Workload Identity Federation per
ADR-001 — service-account JSON keys are NEVER accepted by this module.

Error semantics
---------------
All failure modes — NotFound, PermissionDenied, Unauthenticated,
DeadlineExceeded, missing ADC, network errors — collapse to ``None`` so
that ``mangomas.composition._resolve_llm_secrets`` falls back to the
inline ``api_key`` (preserving local-dev ergonomics). Auth failures and
unexpected errors emit ERROR-level structured logs; missing secrets emit
a debug log only. **The secret value, the full resource path (with
version), and credential payloads are never logged.** See ADR-002.

The Google SDK is imported lazily inside method bodies so this module
remains importable even when the optional ``gcp`` extra is absent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from google.cloud import secretmanager

logger = logging.getLogger(__name__)


_PROJECTS_PREFIX = "projects/"


def _build_resource_path(name: str, *, project_id: str, default_version: str) -> str:
    """Return a fully-qualified secret version path.

    If *name* already starts with ``"projects/"`` it is returned unchanged
    (caller supplied a full path). Otherwise it is treated as a short id
    and combined with the configured project and default version.
    """
    if name.startswith(_PROJECTS_PREFIX):
        return name
    return f"projects/{project_id}/secrets/{name}/versions/{default_version}"


def _short_name(name: str) -> str:
    """Return only the short secret id from *name* for safe logging.

    Strips the project prefix and version suffix when present so the log
    field never carries the version (which we treat as sensitive context).
    """
    if not name.startswith(_PROJECTS_PREFIX):
        return name
    parts = name.split("/")
    # projects/<p>/secrets/<short>[/versions/<v>]
    try:
        idx = parts.index("secrets")
        return parts[idx + 1]
    except (ValueError, IndexError):  # pragma: no cover  -- malformed
        return name


class GCPSecretManagerProvider:
    """Resolve secrets from Google Secret Manager via Workload Identity.

    Parameters
    ----------
    project_id:
        GCP project that owns the secrets. Used to build the resource
        path when callers pass a short id.
    timeout_seconds:
        Per-call deadline forwarded to ``access_secret_version``.
    default_version:
        Version suffix appended to short ids (typically ``"latest"``).
    client:
        Optional pre-built :class:`SecretManagerServiceClient` — the
        injection seam for tests. When ``None`` the SDK is imported and
        a client is constructed on first ``get()`` call.
    """

    def __init__(
        self,
        *,
        project_id: str,
        timeout_seconds: float,
        default_version: str,
        client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._timeout_seconds = timeout_seconds
        self._default_version = default_version
        self._client = client

    def _ensure_client(self) -> secretmanager.SecretManagerServiceClient:
        # The SDK-construction branch is exercised only when the optional
        # ``gcp`` extra is installed; unit tests always inject ``client``
        # via the constructor seam, so the import + instantiation paths
        # are excluded from coverage.
        if self._client is None:  # pragma: no cover
            from google.cloud import secretmanager  # noqa: PLC0415

            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def get(self, name: str) -> str | None:
        """Return the plaintext secret, or ``None`` on any failure.

        See module docstring + ADR-002 for the rationale behind collapsing
        all failure modes into ``None``.
        """
        from google.api_core import exceptions as gax  # noqa: PLC0415
        from google.auth import exceptions as gauth_exc  # noqa: PLC0415

        resource_path = _build_resource_path(
            name,
            project_id=self._project_id,
            default_version=self._default_version,
        )
        short = _short_name(name)
        try:
            client = self._ensure_client()
            response = client.access_secret_version(
                request={"name": resource_path},
                timeout=self._timeout_seconds,
            )
        except gax.NotFound:
            logger.debug(
                "GCP secret not found",
                extra={"project_id": self._project_id, "secret_name": short},
            )
            return None
        except gauth_exc.DefaultCredentialsError as exc:
            logger.error(
                "GCP Secret Manager credentials missing (ADC not configured)",
                extra={
                    "error": type(exc).__name__,
                    "project_id": self._project_id,
                    "secret_name": short,
                },
            )
            return None
        except (gax.PermissionDenied, gax.Unauthenticated) as exc:
            logger.error(
                "GCP Secret Manager auth failure",
                extra={
                    "error": type(exc).__name__,
                    "project_id": self._project_id,
                    "secret_name": short,
                },
            )
            return None
        except gax.DeadlineExceeded as exc:
            logger.error(
                "GCP Secret Manager request timed out",
                extra={
                    "error": type(exc).__name__,
                    "project_id": self._project_id,
                    "secret_name": short,
                    "timeout_seconds": self._timeout_seconds,
                },
            )
            return None
        except gax.GoogleAPIError as exc:
            logger.error(
                "GCP Secret Manager request failed",
                extra={
                    "error": type(exc).__name__,
                    "project_id": self._project_id,
                    "secret_name": short,
                },
            )
            return None

        payload: bytes = response.payload.data
        return payload.decode("utf-8")
