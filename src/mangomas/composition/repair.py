"""Composition factory for the repair-loop oracle isolation seam (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
from typing import Final

from mangomas.config import RepairSettings
from mangomas.errors import ConfigError
from mangomas.harness.isolation import OracleIsolationBackend, isolation_registry

logger = logging.getLogger(__name__)

AUTO_ORACLE_BACKENDS: Final[tuple[str, ...]] = ("docker", "wsl")


def build_oracle_isolation(cfg: RepairSettings | None = None) -> OracleIsolationBackend:
    """Resolve and validate the configured :class:`OracleIsolationBackend`.

    When ``cfg.oracle_isolation == "none"`` (the default), returns the
    registered ``"none"`` backend. When ``cfg.oracle_isolation == "required"``,
    probes the requested backend (or ``docker`` then ``wsl`` when
    ``oracle_backend == "auto"``) via :meth:`OracleIsolationBackend.is_available`
    and fails closed with :class:`ConfigError` if no isolating backend is ready.
    """
    settings = RepairSettings() if cfg is None else cfg
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


__all__ = [
    "AUTO_ORACLE_BACKENDS",
    "build_oracle_isolation",
]
