"""Repair-loop oracle isolation settings (``MANGOMAS_REPAIR__*``, spec-0044 / ADR-0040)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


_BACKEND_TOKEN_FIELDS: tuple[str, ...] = (
    "oracle_image",
    "oracle_memory_limit",
    "wsl_distro",
    "wsl_python",
)
_UNSAFE_TOKEN_RE = re.compile(r"[\s\x00-\x1f\x7f]")


def validate_cli_token(value: str, *, field_name: str) -> str:
    """Reject empty strings, leading option flags, whitespace, or control characters."""
    if not value or value.startswith("-") or _UNSAFE_TOKEN_RE.search(value):
        raise ValueError(
            f"{field_name} must be non-empty and must not start with '-' or contain "
            f"whitespace or control characters; got {value!r}"
        )
    return value


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
    # allow_inf_nan=False: an infinite budget reaches subprocess.run unchecked
    # and either hangs the probe or raises an untranslated OverflowError.
    oracle_timeout_seconds: float = Field(
        default=DEFAULT_REPAIR_ORACLE_TIMEOUT_SECONDS,
        gt=0.0,
        allow_inf_nan=False,
    )
    oracle_memory_limit: str = Field(default=DEFAULT_REPAIR_ORACLE_MEMORY_LIMIT, min_length=1)
    oracle_pids_limit: int = Field(
        default=DEFAULT_REPAIR_ORACLE_PIDS_LIMIT,
        ge=MIN_REPAIR_ORACLE_PIDS_LIMIT,
    )
    oracle_probe_timeout_seconds: float = Field(
        default=DEFAULT_REPAIR_ORACLE_PROBE_TIMEOUT_SECONDS,
        gt=0.0,
        allow_inf_nan=False,
    )
    wsl_distro: str = Field(default=DEFAULT_REPAIR_WSL_DISTRO, min_length=1)
    wsl_python: str = Field(default=DEFAULT_REPAIR_WSL_PYTHON, min_length=1)

    @model_validator(mode="after")
    def _validate_backend_tokens(self) -> RepairSettings:
        """Reject leading flags, whitespace, or control chars passed to isolation CLIs."""
        for field_name in _BACKEND_TOKEN_FIELDS:
            validate_cli_token(getattr(self, field_name), field_name=f"repair.{field_name}")
        return self
