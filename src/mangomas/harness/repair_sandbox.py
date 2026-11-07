"""Deterministic staging and promotion containment for automated LLM coding (Ring 2).

Implements the security invariants described by the ``mango-repair-loop`` skill
so a local LLM proposing edits can never:

1. Exfiltrate source code or prompts to a non-loopback endpoint
   (:func:`validate_loopback_llm_url`).
2. Write candidate drafts outside a dedicated staging directory, traverse ``..``,
   or follow a symlink/junction to overwrite files on the host PC
   (:func:`stage_candidate_file`).
3. Promote a candidate onto a protected core contract, a governance-surface
   file, ``.git/``, ``.claude/``, ``*.env*``, or any path outside the caller's
   explicit per-run allowlist (:func:`validate_promotion_target`).
4. Execute an unbounded or credential-leaking oracle subprocess, or promote a
   candidate when the oracle fails (:func:`run_oracle_and_promote`).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse

from mangomas.config.repair import DEFAULT_REPAIR_ORACLE_TIMEOUT_SECONDS
from mangomas.errors import ConfigError, ToolExecutionError
from mangomas.harness.governance import GOVERNANCE_SURFACE, PROTECTED_PATHS, normalize_path
from mangomas.harness.isolation.base import OracleIsolationBackend
from mangomas.harness.isolation.none import NoIsolationBackend

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

FORBIDDEN_PROMOTION_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".claude/",
    ".venv/",
    "venv/",
)

# Component form of the prefixes: a bare `.git` (a gitdir-pointer *file* in a
# worktree or submodule) and a nested `sub/.git/` are refused as well.
_FORBIDDEN_PROMOTION_DIR_NAMES: frozenset[str] = frozenset(
    prefix.rstrip("/").lower() for prefix in FORBIDDEN_PROMOTION_PREFIXES
)

# Write flags for promotion: never follow a final-component link where the OS
# supports it (POSIX `O_NOFOLLOW`); binary mode so the hashed bytes land as-is.
_PROMOTION_WRITE_FLAGS: int = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_TRUNC
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_BINARY", 0)
)
_PROMOTION_FILE_MODE: int = 0o644

FORBIDDEN_PROMOTION_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".mcp.json",
        "sitecustomize.py",
        "pyproject.toml",
    }
)

# DOS device names and Windows reserved filename patterns rejected anywhere in
# a staging or promotion path (Windows-specific host-protection).
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "com1",
        "com2",
        "com3",
        "com4",
        "com5",
        "com6",
        "com7",
        "com8",
        "com9",
        "lpt1",
        "lpt2",
        "lpt3",
        "lpt4",
        "lpt5",
        "lpt6",
        "lpt7",
        "lpt8",
        "lpt9",
    }
)

# Environment variable prefixes stripped before an oracle subprocess runs on
# untrusted LLM-generated code (case-insensitive on Windows).
_SCRUBBED_ENV_PREFIXES: tuple[str, ...] = (
    "RUN_",
    "GITHUB_",
    "GOOGLE_",
    "OPENAI_",
    "ANTHROPIC_",
    "LANGFUSE_",
    "AWS_",
    "AZURE_",
)

# Environment variable suffixes stripped unconditionally (case-insensitive).
_SCRUBBED_ENV_SUFFIXES: tuple[str, ...] = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_PRIVATE_KEY",
)

# Only these `MANGOMAS_` environment variables survive into an oracle subprocess.
_ORACLE_MANGOMAS_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "MANGOMAS_ENV",
        "MANGOMAS_LOG_LEVEL",
        "MANGOMAS_LOG__FORMAT",
        "MANGOMAS_TELEMETRY__EXPORTER",
        "MANGOMAS_TELEMETRY__METRICS_ENABLED",
        "MANGOMAS_DISABLE_RTK_HOOK",
        "RTK_TELEMETRY_DISABLED",
    }
)

_SAFE_ORACLE_ADDOPTS: str = "-p no:cacheprovider -o addopts="
# Aliases the settings default so `run_oracle_and_promote` and
# MANGOMAS_REPAIR__ORACLE_TIMEOUT_SECONDS cannot drift apart.
DEFAULT_ORACLE_TIMEOUT_SECONDS: float = DEFAULT_REPAIR_ORACLE_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PromotionOutcome:
    """Result of running the deterministic oracle and promoting a staged file."""

    promoted: bool
    returncode: int
    stdout: str
    stderr: str
    target_path: Path | None = None


def validate_loopback_llm_url(url: str) -> str:
    """Ensure *url* targets a loopback HTTP(S) endpoint, or raise ``ConfigError``."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ConfigError(
            "Repair-loop LLM URL could not be parsed",
            detail=str(exc),
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(
            "Repair-loop LLM URL must be an http(s) URL with an explicit host",
            detail=url,
        )
    if parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"Repair-loop LLM URL host {parsed.hostname!r} is not a loopback address "
            f"(allowed: {sorted(LOOPBACK_HOSTS)})",
            detail=url,
        )
    return url


def _is_link(path: Path) -> bool:
    """True for a symlink, or a Windows junction where ``Path.is_junction`` exists (3.12+)."""
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (is_junction is not None and bool(is_junction()))


def _reject_symlink_chain(path: Path, stop_at: Path) -> None:
    """Raise ``ToolExecutionError`` if *path* or any parent up to *stop_at* is a link."""
    current = path
    stop_resolved = stop_at.resolve()
    while True:
        if _is_link(current):
            raise ToolExecutionError(
                f"Sandbox refused symlink or junction in path: {current}",
                tool_name="repair_sandbox",
            )
        if current == stop_at or current.resolve() == stop_resolved or current.parent == current:
            break
        current = current.parent


def _resolve_contained_path(root: Path, relative_path: str | Path, *, label: str) -> Path:
    """Resolve *relative_path* inside *root*, rejecting traversal, UNC, and symlinks."""
    raw = str(relative_path).strip()
    if not raw:
        raise ToolExecutionError(
            f"Sandbox {label} path must not be empty",
            tool_name="repair_sandbox",
        )
    if "\0" in raw:
        raise ToolExecutionError(
            f"Sandbox {label} path contains a null byte",
            tool_name="repair_sandbox",
        )
    # PureWindowsPath, not Path: the host interpreter parses with POSIX
    # semantics on Linux CI, where a Windows drive/UNC prefix must still refuse.
    if raw.startswith(("/", "\\")) or ":" in PureWindowsPath(raw).drive:
        raise ToolExecutionError(
            f"Sandbox {label} path must be relative (refusing absolute/drive/UNC path {raw!r})",
            tool_name="repair_sandbox",
        )
    candidate_rel = Path(raw)
    if ":" in raw or ".." in candidate_rel.parts:
        raise ToolExecutionError(
            f"Sandbox {label} path must be a relative path without '..' traversal: {raw!r}",
            tool_name="repair_sandbox",
        )
    # Device names are reserved in every directory, with any extension and
    # trailing spaces; PureWindowsPath splits on both separators on any host.
    stems = (part.split(".", 1)[0].rstrip(" ").lower() for part in PureWindowsPath(raw).parts)
    if any(stem in _WINDOWS_RESERVED_NAMES for stem in stems):
        raise ToolExecutionError(
            f"Sandbox {label} path uses a reserved Windows device name: {raw!r}",
            tool_name="repair_sandbox",
        )
    root_resolved = root.resolve()
    if root.is_symlink():
        raise ToolExecutionError(
            f"Sandbox {label} root must not be a symlink: {root}",
            tool_name="repair_sandbox",
        )
    joined = root / candidate_rel
    _reject_symlink_chain(joined, root)
    resolved = joined.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ToolExecutionError(
            f"Sandbox {label} path {raw!r} escapes root {root_resolved}",
            tool_name="repair_sandbox",
        )
    return resolved


def stage_candidate_file(
    staging_root: Path,
    relative_path: str | Path,
    content: str,
) -> Path:
    """Write *content* to *relative_path* inside *staging_root* after containment checks."""
    target = _resolve_contained_path(staging_root, relative_path, label="staging")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def validate_promotion_target(
    repo_root: Path,
    relative_path: str | Path,
    *,
    allowed_paths: Collection[str],
    protected_paths: Collection[str] = PROTECTED_PATHS,
    governance_surface: Collection[str] = GOVERNANCE_SURFACE,
) -> Path:
    """Validate that *relative_path* is an allowlisted, non-protected file inside *repo_root*."""
    resolved = _resolve_contained_path(repo_root, relative_path, label="promotion")
    norm = normalize_path(resolved.relative_to(repo_root.resolve()).as_posix())
    normalized_allowlist = {normalize_path(p) for p in allowed_paths}
    if norm not in normalized_allowlist:
        raise ToolExecutionError(
            f"Promotion target {norm!r} is not in the repair allowlist "
            f"{sorted(normalized_allowlist)}",
            tool_name="repair_sandbox",
        )
    protected_norm = {normalize_path(p) for p in protected_paths} | {
        normalize_path(p) for p in governance_surface
    }
    if norm in protected_norm:
        raise ToolExecutionError(
            f"Promotion target {norm!r} is a protected or governance-surface path",
            tool_name="repair_sandbox",
        )
    if any(part.lower() in _FORBIDDEN_PROMOTION_DIR_NAMES for part in Path(norm).parts):
        raise ToolExecutionError(
            f"Promotion target {norm!r} is (or is inside) a forbidden directory",
            tool_name="repair_sandbox",
        )
    filename_lower = Path(norm).name.lower()
    forbidden_lower = {f.lower() for f in FORBIDDEN_PROMOTION_FILENAMES}
    if filename_lower in forbidden_lower or filename_lower.startswith(".env"):
        raise ToolExecutionError(
            f"Promotion target {norm!r} names a forbidden configuration/secret file",
            tool_name="repair_sandbox",
        )
    return resolved


def build_scrubbed_oracle_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment dict with live-gate flags and credentials stripped.

    All ``MANGOMAS_`` keys are dropped except an explicit allowlist, any key
    ending in a credential suffix is dropped, and comparisons are
    case-insensitive so ``openai_api_key`` is stripped on Windows the same as
    ``OPENAI_API_KEY``.
    """
    source = os.environ if base_env is None else base_env
    scrubbed: dict[str, str] = {}
    stripped_keys: list[str] = []
    for key, value in source.items():
        key_upper = key.upper()
        if (
            key_upper.startswith(_SCRUBBED_ENV_PREFIXES)
            or key_upper.endswith(_SCRUBBED_ENV_SUFFIXES)
            or (key_upper.startswith("MANGOMAS_") and key not in _ORACLE_MANGOMAS_ENV_ALLOWLIST)
        ):
            stripped_keys.append(key)
            continue
        scrubbed[key] = value
    if stripped_keys:
        logger.debug(
            "Repair-sandbox oracle env scrubbed credential/gate keys",
            extra={"event": "repair_sandbox_env_scrubbed", "stripped_keys": sorted(stripped_keys)},
        )
    scrubbed["PYTEST_ADDOPTS"] = _SAFE_ORACLE_ADDOPTS
    scrubbed["PYTHONIOENCODING"] = "utf-8"
    return scrubbed


def _write_promotion_target(
    repo_root: Path,
    relative_path: str | Path,
    allowed_paths: Collection[str],
    payload: bytes,
) -> Path:
    """Re-validate the target after the oracle ran, then write without following links.

    The oracle may have replaced the target (or an ancestor) with a link to a
    file outside the repo; validation runs again after ``mkdir`` so a link
    created in between is refused, and ``O_NOFOLLOW`` closes the final hop
    on POSIX.
    """
    target = validate_promotion_target(repo_root, relative_path, allowed_paths=allowed_paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = validate_promotion_target(repo_root, relative_path, allowed_paths=allowed_paths)
    try:
        fd = os.open(target, _PROMOTION_WRITE_FLAGS, _PROMOTION_FILE_MODE)
    except OSError as exc:
        raise ToolExecutionError(
            f"Promotion target could not be opened without following links: {exc}",
            tool_name="repair_sandbox",
        ) from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
    return target


def run_oracle_and_promote(
    *,
    staging_root: Path,
    repo_root: Path,
    relative_path: str | Path,
    allowed_paths: Collection[str],
    oracle_argv: Sequence[str],
    timeout_seconds: float = DEFAULT_ORACLE_TIMEOUT_SECONDS,
    base_env: Mapping[str, str] | None = None,
    isolation: OracleIsolationBackend | None = None,
) -> PromotionOutcome:
    """Run *oracle_argv* in *isolation* (or host subprocess) and promote iff it exits 0."""
    if not oracle_argv or any(not isinstance(arg, str) or not arg for arg in oracle_argv):
        raise ToolExecutionError(
            "Oracle command must be a non-empty sequence of non-empty argument strings",
            tool_name="repair_sandbox",
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ToolExecutionError(
            "Oracle timeout_seconds must be positive and finite",
            tool_name="repair_sandbox",
        )
    staged_file = _resolve_contained_path(staging_root, relative_path, label="staging")
    if not staged_file.is_file():
        raise ToolExecutionError(
            f"Staged candidate file does not exist: {staged_file}",
            tool_name="repair_sandbox",
        )
    target_file = validate_promotion_target(
        repo_root,
        relative_path,
        allowed_paths=allowed_paths,
    )
    env = build_scrubbed_oracle_env(base_env)
    staged_hash = hashlib.sha256(staged_file.read_bytes()).hexdigest()
    backend: OracleIsolationBackend = NoIsolationBackend() if isolation is None else isolation
    completed = backend.run(
        oracle_argv,
        cwd=staging_root.resolve(),
        env=env,
        timeout_seconds=timeout_seconds,
    )

    if completed.returncode != 0:
        return PromotionOutcome(
            promoted=False,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            target_path=None,
        )

    # The oracle ran untrusted code: re-check the staged path for a swapped-in
    # link, then hash and promote the *same* bytes (no read-after-check gap).
    staged_file = _resolve_contained_path(staging_root, relative_path, label="staging")
    staged_bytes = staged_file.read_bytes()
    after_hash = hashlib.sha256(staged_bytes).hexdigest()
    if after_hash != staged_hash:
        raise ToolExecutionError(
            "Oracle subprocess modified the staged candidate file before promotion",
            tool_name="repair_sandbox",
        )

    target_file = _write_promotion_target(repo_root, relative_path, allowed_paths, staged_bytes)
    logger.info(
        "Repair-sandbox candidate promoted",
        extra={
            "event": "repair_sandbox_promotion",
            "relative_path": str(relative_path),
            "target_path": str(target_file),
            "returncode": completed.returncode,
        },
    )
    return PromotionOutcome(
        promoted=True,
        returncode=0,
        stdout=completed.stdout,
        stderr=completed.stderr,
        target_path=target_file,
    )
