"""Repair-loop oracle isolation settings (``MANGOMAS_REPAIR__*``, spec-0044 / ADR-0040)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_REPAIR_ORACLE_ISOLATION: Literal["none", "required"] = "none"
DEFAULT_REPAIR_ORACLE_BACKEND: Literal["auto", "docker", "wsl"] = "auto"
DEFAULT_REPAIR_ORACLE_IMAGE: str = "mangomas:oracle"
DEFAULT_REPAIR_ORACLE_TIMEOUT_SECONDS: float = 60.0
DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT: str = "512m"
DEFAULT_REPAIR_ORACLE_PIDS_LIMIT: int = 128
MIN_REPAIR_ORACLE_PIDS_LIMIT: int = 16
DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS: float = 5.0
DEFAULT_REPAIR_WSL_DISTRO: str = "Ubuntu"
DEFAULT_REPAIR_WSL_PYTHON: str = "/usr/bin/python3"


class RepairSettings(BaseModel):
    """Configuration for the governed repair-loop oracle isolation seam.

    Defaults to ``oracle_isolation="none"`` so existing callers run unchanged
    when this group is absent from the environment. Setting
    ``MANGOMAS_REPAIR__ORACLE_ISOLATION=required`` makes
    ``composition.repair.build_oracle_isolation`` fail closed when the selected
    isolating backend is unavailable.
    """

    oracle_isolation: Literal["none", "required"] = DEFAULT_REPAIR_ORACLE_ISOLATION
    oracle_backend: Literal["auto", "docker", "wsl"] = DEFAULT_REPAIR_ORACLE_BACKEND
    oracle_image: str = Field(default=DEFAULT_REPAIR_ORACLE_IMAGE, min_length=1)
    oracle_timeout_seconds: float = Field(default=DEFAULT_REPAIR_ORACLE_TIMEOUT_SECONDS, gt=0.0)
    oracle_memory_limit: str = Field(default=DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT, min_length=1)
    oracle_pids_limit: int = Field(
        default=DEFAULT_REPAIR_ORACLE_PIDS_LIMIT,
        ge=MIN_REPAIR_ORACLE_PIDS_LIMIT,
    )
    oracle_probe_timeout_seconds: float = Field(
        default=DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
        gt=0.0,
    )
    wsl_distro: str = Field(default=DEFAULT_REPAIR_WSL_DISTRO, min_length=1)
    wsl_python: str = Field(default=DEFAULT_REPAIR_WSL_PYTHON, min_length=1)
