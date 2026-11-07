"""Composition factory for the repair-loop oracle isolation seam (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Final

from mangomas.config import RepairSettings, get_settings
from mangomas.errors import ConfigError
from mangomas.harness.isolation import OracleIsolationBackend, isolation_registry
from mangomas.harness.repair_sandbox import PromotionOutcome, run_oracle_and_promote

logger = logging.getLogger(__name__)

AUTO_ORACLE_BACKENDS: Final[tuple[str, ...]] = ("docker", "wsl")


def _resolve_repair_settings(cfg: RepairSettings | None) -> RepairSettings:
    """Explicit settings win; otherwise read the environment-backed singleton.

    ``RepairSettings()`` is a plain ``BaseModel`` and never reads the
    environment, so defaulting to it would silently ignore
    ``MANGOMAS_REPAIR__ORACLE_ISOLATION=required``.
    """
    return get_settings().repair if cfg is None else cfg


def build_oracle_isolation(cfg: RepairSettings | None = None) -> OracleIsolationBackend:
    """Resolve and validate the configured :class:`OracleIsolationBackend`.

    When ``cfg`` is omitted the environment-backed ``get_settings().repair`` is
    used, the same default ``build_orchestrator`` applies. When
    ``oracle_isolation == "none"`` (the default), returns the registered
    ``"none"`` backend. When ``oracle_isolation == "required"``, probes the
    requested backend (or ``docker`` then ``wsl`` when ``oracle_backend ==
    "auto"``) via :meth:`OracleIsolationBackend.is_available` and fails closed
    with :class:`ConfigError` if no isolating backend is ready.
    """
    settings = _resolve_repair_settings(cfg)
    if settings.oracle_isolation == "none":
        backend = isolation_registry.get("none")(settings)
        logger.info(
            "Selected repair-oracle isolation backend %r (policy=%r)",
            backend.name,
            settings.oracle_isolation,
            extra={
                "event": "repair_oracle_isolation_selected",
                "backend": backend.name,
                "policy": settings.oracle_isolation,
            },
        )
        return backend

    candidates = (
        AUTO_ORACLE_BACKENDS if settings.oracle_backend == "auto" else (settings.oracle_backend,)
    )
    probed: list[str] = []
    available_names = set(isolation_registry.available())
    for name in candidates:
        if name not in available_names:
            probed.append(f"{name}=unregistered")
            continue
        candidate = isolation_registry.get(name)(settings)
        if candidate.is_available():
            logger.info(
                "Selected repair-oracle isolation backend %r (policy=%r)",
                candidate.name,
                settings.oracle_isolation,
                extra={
                    "event": "repair_oracle_isolation_selected",
                    "backend": candidate.name,
                    "policy": settings.oracle_isolation,
                },
            )
            return candidate
        probed.append(f"{name}=unavailable")

    raise ConfigError(
        "Repair-loop oracle isolation is 'required' but no isolating backend is available",
        detail=", ".join(probed),
    )


def run_repair_oracle(
    cfg: RepairSettings | None = None,
    *,
    staging_root: Path,
    repo_root: Path,
    relative_path: str | Path,
    allowed_paths: Collection[str],
    oracle_argv: Sequence[str],
    base_env: Mapping[str, str] | None = None,
) -> PromotionOutcome:
    """Run the oracle with the configured backend *and* timeout, then promote on exit 0.

    The one documented repair-loop entry point: it reads both the isolation
    policy and ``oracle_timeout_seconds`` from the same settings, so neither
    can be honoured while the other is silently left at a module default.
    """
    settings = _resolve_repair_settings(cfg)
    isolation = build_oracle_isolation(settings)
    logger.debug(
        "Running repair oracle (backend=%r, timeout=%ss)",
        isolation.name,
        settings.oracle_timeout_seconds,
        extra={
            "event": "repair_oracle_run",
            "backend": isolation.name,
            "timeout_seconds": settings.oracle_timeout_seconds,
        },
    )
    return run_oracle_and_promote(
        staging_root=staging_root,
        repo_root=repo_root,
        relative_path=relative_path,
        allowed_paths=allowed_paths,
        oracle_argv=oracle_argv,
        timeout_seconds=settings.oracle_timeout_seconds,
        base_env=base_env,
        isolation=isolation,
    )


__all__ = [
    "AUTO_ORACLE_BACKENDS",
    "build_oracle_isolation",
    "run_repair_oracle",
]
