"""Frontmatter linter for Claude Code agents and skills.

Validates every ``.github/agents/**/*.agent.md`` and
``.github/skills/**/SKILL.md`` against the project's Pydantic v2 frontmatter
schemas.  Also supports a ``--check-protected-paths`` mode used by the
``.claude/settings.json`` PreToolUse hook to block edits to stable contracts
without an explicit approval marker.

Exit codes
----------
``EXIT_OK = 0``
    All files passed schema validation.
``EXIT_SCHEMA = 1``
    At least one file failed schema validation, sub_agents resolution, or
    file-discovery.
``EXIT_PROTECTED = 2``
    The ``--check-protected-paths`` flag is set and the staged diff of the
    requested path lacks the breaking-change marker.

Run::

    python scripts/lint_agent_frontmatter.py
    python scripts/lint_agent_frontmatter.py --check-protected-paths src/mangomas/core/agent.py
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mangomas.harness.governance import (
    BREAKING_CHANGE_MARKER,
    PROTECTED_PATHS,
    has_breaking_change_marker,
)
from mangomas.harness.governance import normalize_path as _normalize_path
from mangomas.harness.governance import read_staged_diff as _staged_diff

# ── Module-level constants (single source of truth, no magic literals) ────────
#
# ``PROTECTED_PATHS`` / ``BREAKING_CHANGE_MARKER(_ALIASES)`` / path-normalize /
# staged-diff reading live in ``mangomas.harness.governance`` (ADR-0011) so the
# same governance primitives are shared with the ``ConfigChange`` audit hook
# and get real coverage-gate credit — ``pyproject.toml`` scopes coverage to
# ``source = ["mangomas"]``, so logic left only in ``scripts/`` is invisible to
# the 95% floor. Re-exported above for backwards compatibility: this module's
# own tests and the ``PreToolUse`` hook still reference ``linter.PROTECTED_PATHS``
# / ``linter._staged_diff`` etc. by their historical names.

AGENTS_GLOB: Final[str] = ".github/agents/**/*.agent.md"
SKILLS_GLOB: Final[str] = ".github/skills/**/SKILL.md"

FRONTMATTER_DELIMITER: Final[str] = "---"
FRONTMATTER_SPLIT_PARTS: Final[int] = 3  # [pre, frontmatter, body]
DESCRIPTION_MIN_LENGTH: Final[int] = 30
# Allowed tool tokens are enforced by the ``Literal`` annotation on
# ``AgentFrontmatter.tools`` — the Pydantic model is the live source of truth,
# no parallel constant required.

EXIT_OK: Final[int] = 0
EXIT_SCHEMA: Final[int] = 1
EXIT_PROTECTED: Final[int] = 2

logger = logging.getLogger(__name__)


# ── Pydantic v2 schemas ───────────────────────────────────────────────────────


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


# ── Protected-path enforcement (hook mode) ────────────────────────────────────
#
# ``_staged_diff`` is ``mangomas.harness.governance.read_staged_diff`` imported
# under its historical name (see module-level imports above).


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
    matched_marker = has_breaking_change_marker(diff)
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


# ── Entry point ───────────────────────────────────────────────────────────────


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    """Lint frontmatter; return one of ``EXIT_OK``/``EXIT_SCHEMA``/``EXIT_PROTECTED``."""
    _configure_logging()
    parser = argparse.ArgumentParser(description="Lint Claude Code agent and skill frontmatter.")
    parser.add_argument(
        "--check-protected-paths",
        dest="protected_path",
        default=None,
        help="If set, treat the given path as a candidate edit and block when missing the marker.",
    )
    args = parser.parse_args(argv)

    if args.protected_path is not None:
        return _check_protected_path(args.protected_path)

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
