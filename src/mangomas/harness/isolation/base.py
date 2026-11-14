"""Protocol and shared helpers for repair-loop oracle isolation (spec-0044 / ADR-0040)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from mangomas.errors import ToolExecutionError

PYTHON_ARGV_TOKENS: Final[frozenset[str]] = frozenset({"{python}", "python"})

SANDBOX_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "PYTEST_ADDOPTS",
        "PYTHONIOENCODING",
        "PYTHONDONTWRITEBYTECODE",
        "MANGOMAS_ENV",
        "MANGOMAS_LOG_LEVEL",
        "MANGOMAS_LOG__FORMAT",
        "MANGOMAS_TELEMETRY__EXPORTER",
        "MANGOMAS_TELEMETRY__METRICS_ENABLED",
        "MANGOMAS_DISABLE_RTK_HOOK",
        "RTK_TELEMETRY_DISABLED",
    }
)

SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class IsolationResult:
    """Outcome of running the verification oracle inside an isolation backend."""

    returncode: int
    stdout: str
    stderr: str
    backend: str


@runtime_checkable
class OracleIsolationBackend(Protocol):
    """Protocol satisfied by every repair-loop oracle isolation backend."""

    name: str

    def is_available(self) -> bool:
        """Return ``True`` when the backend's runtime is reachable and ready."""
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> IsolationResult:
        """Execute *argv* with working directory *cwd*, scrubbed *env*, and *timeout_seconds*."""
        ...


def resolve_sandbox_argv(
    argv: Sequence[str],
    *,
    interpreter: str,
    backend_name: str,
    allow_host_interpreter: bool = False,
) -> list[str]:
    """Rewrite a leading ``{python}``/``python`` token and reject host paths in sandboxes."""
    if not argv:
        raise ToolExecutionError(
            f"Sandbox command argv for backend {backend_name!r} must not be empty",
            tool_name="repair_sandbox",
        )
    head = argv[0]
    if head in PYTHON_ARGV_TOKENS:
        return [interpreter, *argv[1:]]
    if not allow_host_interpreter and ("/" in head or "\\" in head or ":" in head):
        raise ToolExecutionError(
            f"Oracle command argv[0] {head!r} is a host-specific path and cannot run inside "
            f"the {backend_name!r} sandbox; use '{{python}}' or 'python'",
            tool_name="repair_sandbox",
        )
    return list(argv)


SANDBOX_IMPORT_SUBDIRS: Final[tuple[str, ...]] = ("src", "")


def sandbox_pythonpath(work_dir: str) -> str:
    """Return sandbox-local import roots: the staged ``src/`` tree first, then staging.

    The host ``PYTHONPATH`` is never forwarded (it names host paths), so an
    isolating backend sets this instead; otherwise a staged candidate could be
    shadowed by the copy installed in the sandbox image and promoted untested.
    """
    roots = (f"{work_dir}/{sub}".rstrip("/") for sub in SANDBOX_IMPORT_SUBDIRS)
    return ":".join(roots)


def select_sandbox_env(env: Mapping[str, str]) -> dict[str, str]:
    """Filter *env* down to the explicit sandbox allowlist and disable bytecode writes."""
    selected = {key: val for key, val in env.items() if key in SANDBOX_ENV_KEYS}
    selected["PYTHONDONTWRITEBYTECODE"] = "1"
    return selected
