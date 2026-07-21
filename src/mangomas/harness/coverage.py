"""Coverage-floor single source of truth and ``Stop``-hook gate decision logic.

The historical ``Stop`` hook hardcoded ``--cov-fail-under=85`` while
``pyproject.toml``'s ``[tool.pytest.ini_options]`` declares ``95`` and
``ci.yml`` separately declares ``90`` — three numbers for one nominal gate
(ADR-0011). ``read_coverage_floor`` parses the real value out of
``pyproject.toml`` at runtime so ``scripts/harness_stop_gate.py`` never
hardcodes (and drifts from) it again. The rest of this module is the pure
decision logic for that hook: whether to skip a session already mid-block,
what pytest invocation to run, and what exit code a given mode resolves to.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from mangomas.errors import ConfigError

DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")
_COV_FAIL_UNDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"--cov-fail-under=(\d+)")

StopGateMode = Literal["advisory", "enforced"]

PYTEST_BASE_ARGS: Final[tuple[str, ...]] = (
    "-m",
    "pytest",
    "--cov=mangomas",
    "--cov-report=term-missing",
    "-q",
)


def read_coverage_floor(pyproject_path: Path | None = None) -> int:
    """Return the ``--cov-fail-under`` integer declared in ``pyproject.toml``.

    Parses ``[tool.pytest.ini_options].addopts`` via stdlib ``tomllib`` (no
    new dependency) so callers never re-hardcode a number that can drift from
    this single source of truth. Raises :class:`~mangomas.errors.ConfigError`
    for every failure mode (missing file, malformed TOML, missing key,
    missing token) rather than guessing a fallback — the caller decides
    whether a read failure should be fail-open or fail-closed.
    """
    path = pyproject_path or DEFAULT_PYPROJECT_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from exc

    try:
        addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    except KeyError as exc:
        raise ConfigError(f"{path} has no [tool.pytest.ini_options].addopts key") from exc

    match = _COV_FAIL_UNDER_PATTERN.search(addopts)
    if match is None:
        raise ConfigError(f"{path}'s addopts has no --cov-fail-under=N token")
    return int(match.group(1))


def build_pytest_args(floor: int) -> list[str]:
    """Return the pytest CLI args (excluding the interpreter) for *floor*."""
    return [*PYTEST_BASE_ARGS, f"--cov-fail-under={floor}"]


def should_skip_active_stop_hook(payload: Mapping[str, object]) -> bool:
    """Return ``True`` when *payload* marks this ``Stop`` hook as already active.

    Claude Code overrides a ``Stop`` hook after 8 consecutive blocks without
    progress; a hook must check ``stop_hook_active`` and exit ok early once
    it's ``true``, or it risks looping the whole session through re-attempts
    right up to the override instead of gracefully letting Claude stop.
    """
    return bool(payload.get("stop_hook_active", False))


def resolve_stop_gate_exit_code(mode: StopGateMode, pytest_returncode: int) -> int:
    """Return the hook's own exit code for *mode* given pytest's returncode.

    Claude Code's ``Stop`` hook protocol: exit ``0`` lets the turn stop, exit
    ``2`` blocks it and shows Claude the hook's stderr. ``advisory`` (today's
    default) always returns ``0`` — a coverage failure is visible in the
    transcript but never blocks the stop. ``enforced`` (opt-in) returns ``2``
    when *pytest_returncode* is nonzero.
    """
    if mode == "enforced" and pytest_returncode != 0:
        return 2
    return 0
