"""Frontmatter linter for Claude Code agents and skills — and its hook modes.

Validates every ``.github/agents/**/*.agent.md`` and
``.github/skills/**/SKILL.md`` against the project's Pydantic v2 frontmatter
schemas (the default, no-argument mode). Two independent hook modes live here
too, both stdlib-only so neither depends on ``pydantic``/``pyyaml`` being
installed — a ``PreToolUse``/``PostToolUse`` hook must work in an interpreter
where the dev extras have not been installed, and must never crash a session
over its own plumbing (see ADR-0021 / spec-0017):

``--hook pre-tool-use``
    Reads the tool-call JSON Claude Code delivers on stdin, and — for an edit
    to a protected core contract — emits an **advisory**
    ``permissionDecision: "ask"`` response (never ``"deny"``; the
    authoritative enforcement point is the CI job run by
    ``scripts/check_protected_paths.py``, which reads committed history an
    in-session agent cannot rewrite). Always exits ``EXIT_OK``.
``--hook post-tool-use --emit-path``
    Reads the same stdin JSON and prints the edited file's path (or nothing),
    for piping into another tool, e.g.
    ``... --emit-path | xargs -r python -m ruff check --fix``. Always exits
    ``EXIT_OK``.

The legacy ``--check-protected-paths <path>`` flag (staged-diff based, used by
pre-commit where a staged diff genuinely exists) is unchanged.

Exit codes
----------
``EXIT_OK = 0``
    All files passed schema validation, or a hook mode ran to completion (hook
    modes never fail the calling process — see module docstring above).
``EXIT_SCHEMA = 1``
    At least one file failed schema validation, sub_agents resolution, or
    file-discovery.
``EXIT_PROTECTED = 2``
    The legacy ``--check-protected-paths`` flag is set and the staged diff of
    the requested path lacks the breaking-change marker.

Run::

    python scripts/lint_agent_frontmatter.py
    python scripts/lint_agent_frontmatter.py --check-protected-paths src/mangomas/core/agent.py
    python scripts/lint_agent_frontmatter.py --hook pre-tool-use < payload.json
    python scripts/lint_agent_frontmatter.py --hook post-tool-use --emit-path < payload.json
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import IO, Final, Literal

import _governance
import _stdin_json

# ``pydantic``/``pyyaml`` are deferred into the schema-lint code path (the
# module-level ``if`` below), not imported unconditionally at the top of the
# file — a hook mode must not require them. ``main()``'s default (no-flag)
# mode always needs them, exactly as before; that requirement is unchanged.
try:
    import yaml
    from pydantic import BaseModel, ConfigDict, Field, ValidationError

    _SCHEMA_DEPS_AVAILABLE = True
except ImportError:
    _SCHEMA_DEPS_AVAILABLE = False

# ── Module-level constants (single source of truth, no magic literals) ────────

AGENTS_GLOB: Final[str] = ".github/agents/**/*.agent.md"
SKILLS_GLOB: Final[str] = ".github/skills/**/SKILL.md"

_DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")

# Defined before _load_governance() (called below, at import time) needs it
# for its fallback-path warning.
logger = logging.getLogger(__name__)

# Fallback governance values, used only if pyproject.toml's
# [tool.mangomas.governance] table (the single source of truth — see
# ADR-0021 / spec-0017, shared with scripts/check_protected_paths.py) can't
# be read. Never fall back to an *empty* protected-path set — that would
# silently disable protection instead of degrading safely.
_FALLBACK_PROTECTED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "src/mangomas/core/agent.py",
        "src/mangomas/core/orchestrator.py",
        "src/mangomas/core/tools.py",
        "src/mangomas/errors.py",
        "src/mangomas/registry.py",
    }
)
_FALLBACK_BREAKING_CHANGE_MARKER_ALIASES: Final[frozenset[str]] = frozenset(
    {"BREAKING-CHANGE", "# approved-breaking-change"}
)


def _load_governance(
    pyproject_path: Path = _DEFAULT_PYPROJECT_PATH,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(protected_paths, marker_aliases)`` from *pyproject_path*.

    Thin wrapper around the shared ``scripts/_governance.py`` loader.
    Falls back to the documented defaults (with a logged warning) on any
    read/parse/shape failure rather than raising — this feeds both the
    schema-lint module constants (import time) and the hook modes (must
    never crash a session).
    """
    try:
        return _governance.load_governance(pyproject_path)
    except _governance.GovernanceLoadError as exc:
        logger.warning(
            "Could not read [tool.mangomas.governance] from %s; using fallback defaults (%s)",
            pyproject_path,
            exc,
        )
        return _FALLBACK_PROTECTED_PATHS, _FALLBACK_BREAKING_CHANGE_MARKER_ALIASES


# Stable core contracts gated by the protected-path hook. Kept in lock-step with
# the "File Ownership" / protected-paths documentation in CLAUDE.md, sourced
# from pyproject.toml so this file and check_protected_paths.py can't drift.
PROTECTED_PATHS, BREAKING_CHANGE_MARKER_ALIASES = _load_governance()
# Marker a committer adds to a commit message (CI gate) or staged diff (legacy
# pre-commit path) to approve a breaking change to a protected path.
BREAKING_CHANGE_MARKER: Final[str] = "BREAKING-CHANGE"

FRONTMATTER_DELIMITER: Final[str] = "---"
FRONTMATTER_SPLIT_PARTS: Final[int] = 3  # [pre, frontmatter, body]
DESCRIPTION_MIN_LENGTH: Final[int] = 30
# Allowed tool tokens are enforced by the ``Literal`` annotation on
# ``AgentFrontmatter.tools`` — the Pydantic model is the live source of truth,
# no parallel constant required.

EXIT_OK: Final[int] = 0
EXIT_SCHEMA: Final[int] = 1
EXIT_PROTECTED: Final[int] = 2

# PreToolUse advisory-decision constants (ADR-0021).
_HOOK_EVENT_NAME: Final[str] = "PreToolUse"
_HOOK_PERMISSION_ASK: Final[str] = "ask"


# ── Pydantic v2 schemas (schema-lint mode only — see module docstring) ────────

if _SCHEMA_DEPS_AVAILABLE:

    class SkillFrontmatter(BaseModel):
        """Frontmatter schema for ``.github/skills/<name>/SKILL.md``."""

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        name: str = Field(min_length=1)
        description: str = Field(min_length=DESCRIPTION_MIN_LENGTH)
        argument_hint: str = Field(alias="argument-hint", min_length=1)

    class AgentFrontmatter(BaseModel):
        """Frontmatter schema for ``.github/agents/**/*.agent.md``."""

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        name: str = Field(min_length=1)
        description: str = Field(min_length=DESCRIPTION_MIN_LENGTH)
        tools: list[Literal["read", "edit", "search", "execute"]] = Field(min_length=1)
        model: str = Field(min_length=1)
        argument_hint: str = Field(alias="argument-hint", min_length=1)
        sub_agents: list[str] | None = None


# ── Parsing helpers ───────────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> dict[str, object]:
    """Return the parsed YAML frontmatter of *text*.

    Raises ``ValueError`` when the text does not start with the standard
    triple-dash frontmatter delimiter.
    """
    parts = text.split(FRONTMATTER_DELIMITER, 2)
    if len(parts) < FRONTMATTER_SPLIT_PARTS:
        raise ValueError("missing frontmatter (expected leading ---)")
    raw = parts[1]
    loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return loaded


def _normalize_path(p: str) -> str:
    """Normalize path separators and strip leading './' without destroying '.github'."""
    normalized = p.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


_MIN_SUBAGENT_PATH_DEPTH = 3  # .github/agents/<parent>/<child>.agent.md


def _parent_slug_of(agent_path: str) -> str | None:
    """Return the parent slug for *agent_path* or ``None`` if it is a parent file."""
    normalized = _normalize_path(agent_path)
    parts = normalized.split("/")
    if len(parts) <= _MIN_SUBAGENT_PATH_DEPTH:
        return None
    return parts[-2]


# ── Validators ────────────────────────────────────────────────────────────────


def _validate_skill(path: str) -> list[str]:
    """Return a list of error messages (empty when valid)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        fm = _split_frontmatter(text)
        SkillFrontmatter.model_validate(fm)
    except ValidationError as exc:
        return [f"{path}: schema violation: {exc}"]
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]
    return []


def _validate_agent(path: str, all_agent_paths: list[str]) -> list[str]:
    """Return a list of error messages (empty when valid)."""
    errors: list[str] = []
    normalized_path = _normalize_path(path)
    normalized_all = [_normalize_path(p) for p in all_agent_paths]
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        fm = _split_frontmatter(text)
        agent = AgentFrontmatter.model_validate(fm)
    except ValidationError as exc:
        return [f"{path}: schema violation: {exc}"]
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]

    if agent.sub_agents is None:
        return []

    parent_slug = _parent_slug_of(normalized_path)
    if parent_slug is not None:
        # Sub-agents declaring further sub-agents is intentionally disallowed
        # to keep the hierarchy two-deep and predictable.
        errors.append(f"{path}: sub_agents is only valid on parent agent files")
        return errors

    parent_filename = os.path.basename(normalized_path)
    parent_name_slug = parent_filename.removesuffix(".agent.md")
    for child_slug in agent.sub_agents:
        expected = f".github/agents/{parent_name_slug}/{child_slug}.agent.md"
        if expected not in normalized_all:
            errors.append(f"{path}: sub_agents references missing file {expected!r}")
    return errors


# ── Protected-path enforcement (legacy: staged-diff based, pre-commit only) ───
#
# Retained unchanged for pre-commit, where a staged diff genuinely exists (the
# ``--hook pre-tool-use`` mode below does not use this — see its docstring for
# why a staged-diff check cannot work at PreToolUse time).


def _staged_diff(path: str) -> str:
    """Return ``git diff --staged -- <path>`` output (empty on git failure)."""
    result = subprocess.run(
        ["git", "diff", "--staged", "--", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "git diff failed for protected-path check",
            extra={"path": path, "stderr": result.stderr.strip()},
        )
        return ""
    return result.stdout


def _check_protected_path(path: str) -> int:
    """Return ``EXIT_PROTECTED`` if *path* is protected and unmarked, else ``EXIT_OK``.

    Uses :func:`_normalize_path` (not ``str.lstrip``) so Windows backslash paths
    and a leading ``./`` are matched against the forward-slash ``PROTECTED_PATHS``
    set — otherwise a ``src\\mangomas\\core\\agent.py`` edit would silently bypass
    the hook. ``lstrip`` is also avoided because it strips individual leading
    characters rather than a fixed prefix.
    """
    normalised = _normalize_path(path)
    if normalised not in PROTECTED_PATHS:
        return EXIT_OK
    diff = _staged_diff(normalised)
    matched_marker = _governance.find_breaking_change_marker(diff, BREAKING_CHANGE_MARKER_ALIASES)
    if matched_marker is not None:
        logger.info(
            "Protected path has approved breaking-change marker",
            extra={"path": normalised, "marker": matched_marker},
        )
        return EXIT_OK
    logger.error(
        "Protected path edit blocked",
        extra={"path": normalised, "required_marker": BREAKING_CHANGE_MARKER},
    )
    return EXIT_PROTECTED


# ── Hook modes (stdin JSON, stdlib-only — ADR-0021 / spec-0017) ───────────────
#
# Claude Code delivers hook input as JSON on stdin, not as per-field
# environment variables — the ``$CLAUDE_TOOL_INPUT_path`` interpolation
# previously used in ``.claude/settings.json`` was never a real substitution,
# so it always expanded empty. Both functions below read the real payload.
#
# Neither function blocks: a ``PreToolUse`` hook cannot be a complete gate
# regardless of its internal correctness (an agent with ``Write`` can create
# any file it likes, including a proposed approval marker, and ``Bash``/MCP
# filesystem tool calls bypass the ``Edit|Write`` matcher entirely) — so the
# authoritative enforcement point is the CI job
# (``scripts/check_protected_paths.py``), which reads committed history an
# in-session agent cannot rewrite. These hooks exist to inform, not to block.


def _read_hook_payload(stream: IO[str]) -> dict[str, object]:
    """Return the hook's stdin JSON, or ``{}`` on any parse failure.

    Thin wrapper around the shared ``scripts/_stdin_json.py`` reader — a
    hook must never crash a session over its own plumbing, so malformed or
    empty stdin degrades to "nothing to report" rather than raising.
    """
    return _stdin_json.read_json_payload(stream, logger=logger)


def _extract_tool_path(payload: dict[str, object]) -> str | None:
    """Return the path a ``PreToolUse``/``PostToolUse`` payload names, if any.

    Tries ``tool_input.file_path`` first (``Edit``/``Write``), then
    ``tool_input.notebook_path`` (``NotebookEdit``) — matching the matcher
    widened to ``Edit|Write|NotebookEdit`` in ``.claude/settings.json``.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _pre_tool_use_hook(stream: IO[str]) -> int:
    """Advisory protected-path check. Always returns ``EXIT_OK``.

    For an edit to a protected core contract, prints a
    ``permissionDecision: "ask"`` JSON response (Claude Code processes hook
    JSON only on exit 0) so the human approving the tool call sees a heads-up
    naming the path and the trailer requirement the CI gate enforces.
    """
    path = _extract_tool_path(_read_hook_payload(stream))
    if path is None:
        return EXIT_OK
    normalised = _normalize_path(path)
    if normalised not in PROTECTED_PATHS:
        return EXIT_OK
    reason = (
        f"{normalised} is a protected core contract (CLAUDE.md 'File "
        f"Ownership'). Approve only if this change will land in a commit "
        f"whose message contains {BREAKING_CHANGE_MARKER!r} — "
        f"scripts/check_protected_paths.py enforces this in CI."
    )
    decision = {
        "hookSpecificOutput": {
            "hookEventName": _HOOK_EVENT_NAME,
            "permissionDecision": _HOOK_PERMISSION_ASK,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))
    return EXIT_OK


def _post_tool_use_emit_path(stream: IO[str]) -> int:
    """Print the edited file's path (or nothing). Always returns ``EXIT_OK``.

    Companion to the ``PostToolUse`` ruff-autofix hook, which had the same
    ``$CLAUDE_TOOL_INPUT_path`` defect. ``.claude/settings.json`` pipes this
    mode's stdout into ``xargs -r python -m ruff check --fix`` — ``xargs -r``
    treats empty input as "run nothing", so a payload naming no path is a
    silent no-op rather than an error.
    """
    path = _extract_tool_path(_read_hook_payload(stream))
    if path:
        print(path)
    return EXIT_OK


# ── Entry point ───────────────────────────────────────────────────────────────


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    """Lint frontmatter, or run a hook mode; see the module docstring."""
    _configure_logging()
    parser = argparse.ArgumentParser(description="Lint Claude Code agent and skill frontmatter.")
    parser.add_argument(
        "--check-protected-paths",
        dest="protected_path",
        default=None,
        help="Legacy staged-diff check (pre-commit only). See --hook for the PreToolUse mode.",
    )
    parser.add_argument(
        "--hook",
        choices=("pre-tool-use", "post-tool-use"),
        default=None,
        help="Run a Claude Code hook mode, reading the tool-call JSON from stdin.",
    )
    parser.add_argument(
        "--emit-path",
        action="store_true",
        help="With --hook post-tool-use, print the edited file's path for piping elsewhere.",
    )
    args = parser.parse_args(argv)

    if args.hook == "pre-tool-use":
        return _pre_tool_use_hook(sys.stdin)
    if args.hook == "post-tool-use":
        return _post_tool_use_emit_path(sys.stdin) if args.emit_path else EXIT_OK

    if args.protected_path is not None:
        return _check_protected_path(args.protected_path)

    if not _SCHEMA_DEPS_AVAILABLE:
        logger.error(
            "Schema-lint mode requires the 'dev' extra (pydantic, pyyaml); "
            "run `pip install -e '.[dev]'`, or use --hook for a stdlib-only mode."
        )
        return EXIT_SCHEMA

    skill_paths = sorted(glob.glob(SKILLS_GLOB, recursive=True))
    agent_paths = sorted(glob.glob(AGENTS_GLOB, recursive=True))

    failures: list[str] = []
    for path in skill_paths:
        failures.extend(_validate_skill(path))
    for path in agent_paths:
        failures.extend(_validate_agent(path, agent_paths))

    if failures:
        for msg in failures:
            logger.error("Frontmatter lint failed: %s", msg)
        return EXIT_SCHEMA

    logger.info(
        "Frontmatter lint passed",
        extra={"skills": len(skill_paths), "agents": len(agent_paths)},
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
