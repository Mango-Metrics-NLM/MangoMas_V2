"""Frontmatter linter for Claude Code agents and skills — and its hook modes.

Validates every ``.github/agents/**/*.agent.md`` and
``.claude/skills/**/SKILL.md`` against the project's Pydantic v2 frontmatter
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
    file-discovery — the last of which includes a glob matching fewer files
    than its floor (see ``MIN_AGENT_FILES`` / ``MIN_SKILL_FILES``), or a
    ``--min-agents``/``--min-skills`` value below ``MIN_FLOOR_LOWER_BOUND``.
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
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

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

# Root and suffix are the single source of truth; the glob is derived from them.
# They used to be independent literals — the glob here, plus a `.agent.md`
# `removesuffix` and a `.github/agents/` prefix buried in the resolver — so a
# relocation could update one and leave the others silently wrong.
# Agents moved to .claude/ (spec-0018) and flattened: Claude Code reads nothing
# from .github/, and the `mango-<slug>` filenames already carry every bit of
# grouping the old <parent>/<child> directories did. Flat also sidesteps the
# unverified question of whether VS Code Copilot recurses this directory.
AGENTS_ROOT: Final[str] = ".claude/agents"
AGENT_FILE_SUFFIX: Final[str] = ".md"
AGENTS_GLOB: Final[str] = f"{AGENTS_ROOT}/**/*{AGENT_FILE_SUFFIX}"
# Skills moved to .claude/ (spec-0018): Claude Code reads nothing from .github/,
# and VS Code Copilot scans .claude/skills/ too, so one tree serves both.
SKILLS_GLOB: Final[str] = ".claude/skills/**/SKILL.md"

# Minimum files each glob must match before the lint can honestly claim to have
# validated anything (spec-0018 R1). Deliberately 1, not the current roster size:
# the claim this script makes is "a non-empty corpus was validated", not "the
# roster we expect was validated". A floor of 1 catches the entire failure class
# — a glob that silently matches nothing, which previously fell through to
# EXIT_OK — while never needing a bump when the corpus legitimately grows. The
# roster itself is asserted by set equality in tests/tooling/test_corpus_contract.py,
# where a change names the file that appeared or vanished.
MIN_AGENT_FILES: Final[int] = 1
MIN_SKILL_FILES: Final[int] = 1

# The lowest floor the --min-agents/--min-skills overrides may express. Zero is
# rejected alongside negatives, and for the same reason: a floor of 0 (or -1)
# means *no* file count can ever fail _below_floor, which restores exactly the
# silently-passing gate spec-0018 R1 exists to kill. An override is for pointing
# the floor at a different corpus size, never for switching the guard off.
MIN_FLOOR_LOWER_BOUND: Final[int] = 1

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
# Allowed tool tokens are checked by ``_invalid_tool_tokens`` rather than a
# ``Literal`` annotation: an MCP tool's name depends on the caller's
# ``.mcp.json`` and cannot be enumerated ahead of time.

EXIT_OK: Final[int] = 0
EXIT_SCHEMA: Final[int] = 1
EXIT_PROTECTED: Final[int] = 2

# ── Claude Code agent-format validators (spec-0018 / ADR-0024) ────────────────
#
# Unwired on purpose in this commit: defined, tested, and called by nothing.
# The schema swap that calls them is a separate, near-mechanical diff, so the
# logic lands where it can be reviewed on its own.

# Agent identity. Kebab-case, matching the filename stem. The `mango-` prefix
# that scopes the shared roster is asserted by a corpus contract test rather
# than here — this pattern also has to accept a contributor's own local agent.
AGENT_SLUG_PATTERN: Final[str] = r"^[a-z0-9]+(-[a-z0-9]+)*$"
_AGENT_SLUG_RE: Final[re.Pattern[str]] = re.compile(AGENT_SLUG_PATTERN)

# Tool tokens Claude Code actually recognises. The previous corpus used
# Copilot's aliases (`read`/`edit`/`search`/`execute`), which are valid *there*
# and meaningless here — and because omitting `tools` inherits every tool, an
# unrecognised list is the dangerous kind of wrong.
_CLAUDE_CODE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Bash",
        "BashOutput",
        "Edit",
        "ExitPlanMode",
        "Glob",
        "Grep",
        "KillShell",
        "NotebookEdit",
        "Read",
        "Skill",
        "SlashCommand",
        "Task",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)
# Tools provided by an MCP server are namespaced and cannot be enumerated here —
# they depend on the caller's `.mcp.json`. Accept the shape, not the name.
_MCP_TOOL_PREFIX: Final[str] = "mcp__"

# `Agent(child-a, child-b)` / `Task(...)`: a delegation-scoping form that works
# on the main thread's `--agent` flag and is **silently ignored inside a
# subagent definition** — the subagent gets unrestricted delegation instead of
# the named subset. Rejected rather than accepted-and-ignored, because a rule
# that looks like a restriction and isn't is worse than no rule.
_AGENT_SCOPED_TOOL_RE: Final[re.Pattern[str]] = re.compile(r"^(Agent|Task)\s*\(")

# Fields Claude Code itself accepts, which this project declines to use. These
# cannot be left to `extra="forbid"`: it would report "Extra inputs are not
# permitted" for a field that is, in fact, permitted by the tool — sending the
# reader to look for a typo that isn't there.
_POLICY_REJECTED_FIELDS: Final[dict[str, str]] = {
    "permissionMode": (
        "per-agent permissionMode is not used here; permissions are governed "
        "centrally in .claude/settings.json so one file describes the whole posture"
    ),
    "hooks": (
        "per-agent hooks are not used here; hooks are declared once in "
        ".claude/settings.json where they can be reviewed together"
    ),
}

# Fields carried over from the GitHub Copilot agent format. Valid there, wrong
# here — and worth their own message, since every file in the migrating corpus
# has at least one.
_LEGACY_FORMAT_FIELDS: Final[dict[str, str]] = {
    "argument-hint": (
        "argument-hint is a Copilot agent field with no Claude Code equivalent; "
        "fold the guidance into the description or the body"
    ),
    "sub_agents": (
        "sub_agents was this repo's own parent/child convention and is not a "
        "Claude Code field; agents are a flat namespace, so drop it"
    ),
}


def _normalize_tools(raw: object) -> list[str] | None:
    """Return *raw* as a list of tool tokens, or ``None`` if it isn't one.

    Accepts both the comma-separated string Claude Code documents and a YAML
    list, so a contributor writing either gets validated rather than skipped.
    """
    if isinstance(raw, str):
        return [token.strip() for token in raw.split(",") if token.strip()]
    if isinstance(raw, list) and all(isinstance(token, str) for token in raw):
        return [token.strip() for token in raw if token.strip()]
    return None


def _invalid_tool_tokens(tokens: list[str]) -> list[str]:
    """Return the tokens Claude Code would not recognise as tools."""
    return [
        token
        for token in tokens
        if token not in _CLAUDE_CODE_TOOL_NAMES and not token.startswith(_MCP_TOOL_PREFIX)
    ]


def _scoped_delegation_tokens(tokens: list[str]) -> list[str]:
    """Return tokens using the silently-ignored ``Agent(...)`` scoping form."""
    return [token for token in tokens if _AGENT_SCOPED_TOOL_RE.match(token)]


def _policy_rejected_fields(fm: dict[str, object]) -> list[str]:
    """Return messages for fields Claude Code allows but this project does not."""
    return [
        f"{field!r} is not allowed: {reason}"
        for field, reason in _POLICY_REJECTED_FIELDS.items()
        if field in fm
    ]


def _legacy_format_fields(fm: dict[str, object]) -> list[str]:
    """Return messages for fields left over from the Copilot agent format."""
    return [
        f"{field!r} is not a Claude Code agent field: {reason}"
        for field, reason in _LEGACY_FORMAT_FIELDS.items()
        if field in fm
    ]


def _model_value_is_valid(value: str) -> bool:
    """Return whether *value* is a plausible Claude Code model selector.

    Claude Code takes an alias (``sonnet``/``opus``/``haiku``/``inherit``) or a
    model id. Both are lowercase and unspaced, so rejecting whitespace and
    uppercase retires the whole ``Claude Sonnet 4.5 (copilot)`` class of value
    in one rule, without this script having to track a live model list.
    """
    return bool(value) and value == value.lower() and not any(ch.isspace() for ch in value)


def _invalid_agent_fields(fm: dict[str, object]) -> list[str]:
    """Return every policy/format problem in *fm*, before schema validation.

    Runs ahead of the Pydantic model so each rejected field gets a message
    explaining *why it is wrong here*, rather than ``extra="forbid"``'s generic
    "Extra inputs are not permitted".
    """
    errors = _policy_rejected_fields(fm) + _legacy_format_fields(fm)

    name = fm.get("name")
    if isinstance(name, str) and not _AGENT_SLUG_RE.match(name):
        errors.append(f"name {name!r} is not a kebab-case slug matching {AGENT_SLUG_PATTERN}")

    model = fm.get("model")
    if isinstance(model, str) and not _model_value_is_valid(model):
        errors.append(
            f"model {model!r} is not a Claude Code model selector; expected an "
            f"alias (sonnet/opus/haiku/inherit) or a model id, lowercase and unspaced"
        )

    if "tools" in fm:
        tokens = _normalize_tools(fm["tools"])
        if tokens is None:
            errors.append("tools must be a comma-separated string or a list of strings")
        else:
            errors.extend(
                f"tool {token!r} uses the Agent(...) scoping form, which Claude Code "
                f"ignores inside a subagent definition — the agent would get "
                f"unrestricted delegation, not the named subset"
                for token in _scoped_delegation_tokens(tokens)
            )
            unknown = _invalid_tool_tokens(tokens)
            if unknown:
                errors.append(
                    f"unrecognised tool token(s) {unknown!r}; expected Claude Code tool "
                    f"names or an {_MCP_TOOL_PREFIX}* MCP tool"
                )
    return errors


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
        """Frontmatter schema for a Claude Code agent (spec-0018 / ADR-0024).

        ``extra="forbid"`` rather than ``allow``: under ``allow`` a typo'd
        ``toolz:`` passes silently, and since omitting ``tools`` makes an agent
        inherit *every* tool, the result is a fully-privileged agent nobody
        asked for. Bounded-and-loud beats unbounded-and-invisible.

        ``tools`` is required for the same reason, and is not a ``Literal``:
        an MCP tool name depends on the caller's ``.mcp.json`` and cannot be
        enumerated here. Token validity is checked by ``_invalid_agent_fields``
        before this model runs.
        """

        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        name: str = Field(min_length=1)
        description: str = Field(min_length=DESCRIPTION_MIN_LENGTH)
        tools: str | list[str]
        model: str = Field(min_length=1)


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
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # Translated rather than propagated: a YAMLError is not a ValueError,
        # so an unquoted `description:` containing a colon used to escape the
        # callers' `except (OSError, ValueError)` and surface as a raw
        # traceback instead of a lint message naming the file.
        raise ValueError(f"invalid YAML in frontmatter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter is not a YAML mapping")
    return loaded


def _normalize_path(p: str) -> str:
    """Normalize path separators and strip leading './' without destroying '.github'."""
    normalized = p.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


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


def _validate_agent(path: str) -> list[str]:
    """Return a list of error messages (empty when valid).

    Field-level policy runs *before* Pydantic so each rejected field gets a
    message saying why it is wrong here. ``extra="forbid"`` would otherwise
    report "Extra inputs are not permitted" for ``permissionMode`` — a field
    Claude Code genuinely accepts — and send the reader hunting a typo.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        fm = _split_frontmatter(text)
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]

    field_errors = [f"{path}: {message}" for message in _invalid_agent_fields(fm)]
    if field_errors:
        return field_errors

    expected_slug = os.path.basename(_normalize_path(path)).removesuffix(AGENT_FILE_SUFFIX)
    if fm.get("name") != expected_slug:
        return [
            f"{path}: name {fm.get('name')!r} must equal the filename stem "
            f"{expected_slug!r} — Claude Code resolves an agent by its name field, so a "
            f"mismatch makes the file's location misleading"
        ]

    try:
        AgentFrontmatter.model_validate(fm)
    except ValidationError as exc:
        return [f"{path}: schema violation: {exc}"]
    return []


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


def _extract_bash_command(payload: dict[str, object]) -> str | None:
    """Return ``tool_input.command`` from a ``Bash`` payload, if any."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("command")
    if isinstance(value, str) and value:
        return value
    return None


def _protected_paths_in_command(command: str) -> list[str]:
    """Return the protected paths *command* mentions, backslash-normalised.

    A plain substring scan, deliberately: parsing shell to decide whether a
    mention is a write reopens the bypass family (redirects, heredocs,
    ``sed -i``, ``python -c``, ``tee``) that made ADR-0021 declare this layer
    advisory in the first place. The accepted false positive is a read-only
    mention — ``grep``, ``cat``, ``git diff`` of a protected path also trips
    the advisory, and the reason text says so.
    """
    normalised = command.replace("\\", "/")
    return sorted(path for path in PROTECTED_PATHS if path in normalised)


def _emit_ask(reason: str) -> None:
    """Print a ``permissionDecision: "ask"`` envelope (never ``deny``)."""
    decision = {
        "hookSpecificOutput": {
            "hookEventName": _HOOK_EVENT_NAME,
            "permissionDecision": _HOOK_PERMISSION_ASK,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))


def _pre_tool_use_hook(stream: IO[str]) -> int:
    """Advisory protected-path check. Always returns ``EXIT_OK``.

    For an edit to a protected core contract — a ``file_path``/
    ``notebook_path`` payload from ``Edit``/``Write``/``NotebookEdit``, or a
    ``Bash`` payload whose ``command`` merely *mentions* a protected path —
    prints a ``permissionDecision: "ask"`` JSON response (Claude Code
    processes hook JSON only on exit 0) so the human approving the tool call
    sees a heads-up naming the path and the trailer requirement the CI gate
    enforces. The Bash branch narrows the gap ADR-0021 concedes (shell
    writes bypass the Edit-matcher hook entirely) without reversing its
    recorded rejection of a hard block: always ``ask``, never ``deny``,
    never a non-zero exit.
    """
    payload = _read_hook_payload(stream)
    path = _extract_tool_path(payload)
    if path is not None:
        normalised = _normalize_path(path)
        if normalised not in PROTECTED_PATHS:
            return EXIT_OK
        _emit_ask(
            f"{normalised} is a protected core contract (CLAUDE.md 'File "
            f"Ownership'). Approve only if this change will land in a commit "
            f"whose message contains {BREAKING_CHANGE_MARKER!r} — "
            f"scripts/check_protected_paths.py enforces this in CI."
        )
        return EXIT_OK

    command = _extract_bash_command(payload)
    if command is None:
        return EXIT_OK
    mentioned = _protected_paths_in_command(command)
    if not mentioned:
        return EXIT_OK
    _emit_ask(
        f"This command mentions protected core contract(s): "
        f"{', '.join(mentioned)} (CLAUDE.md 'File Ownership'). If it writes "
        f"to them, the change must land in a commit whose message contains "
        f"{BREAKING_CHANGE_MARKER!r} — scripts/check_protected_paths.py "
        f"enforces this in CI. This is a mention-level advisory: a read-only "
        f"command (grep, cat, git diff) trips it too."
    )
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


@dataclass(frozen=True)
class LintResult:
    """Outcome of one schema-lint run.

    ``main()`` returns only an ``int``, so before this existed no test could
    assert *how many* files were discovered — which is precisely the fact that
    distinguishes "the corpus is clean" from "the globs matched nothing".
    """

    exit_code: int
    skill_count: int
    agent_count: int
    failures: tuple[str, ...] = ()


def _below_floor(label: str, paths: list[str], pattern: str, minimum: int) -> str | None:
    """Return an error message when *paths* is under *minimum*, else ``None``."""
    if len(paths) >= minimum:
        return None
    return (
        f"{pattern!r} matched {len(paths)} {label} file(s), below the minimum of "
        f"{minimum}. The gate would otherwise pass without validating anything — "
        f"check the glob against the corpus location."
    )


def _invalid_floor(flag: str, value: int) -> str | None:
    """Return an error message when a floor override is below its lower bound."""
    if value >= MIN_FLOOR_LOWER_BOUND:
        return None
    return (
        f"{flag}={value} is below the minimum of {MIN_FLOOR_LOWER_BOUND}. A floor "
        f"of {value} can never fail, which would silently restore the gate that "
        f"passes without validating anything. Use a value >= {MIN_FLOOR_LOWER_BOUND}."
    )


def run_schema_lint(
    *,
    min_agents: int = MIN_AGENT_FILES,
    min_skills: int = MIN_SKILL_FILES,
) -> LintResult:
    """Validate every discovered skill and agent file; return a structured result."""
    skill_paths = sorted(glob.glob(SKILLS_GLOB, recursive=True))
    agent_paths = sorted(glob.glob(AGENTS_GLOB, recursive=True))

    failures: list[str] = []
    for message in (
        _below_floor("skill", skill_paths, SKILLS_GLOB, min_skills),
        _below_floor("agent", agent_paths, AGENTS_GLOB, min_agents),
    ):
        if message is not None:
            failures.append(message)

    for path in skill_paths:
        failures.extend(_validate_skill(path))
    for path in agent_paths:
        failures.extend(_validate_agent(path))

    exit_code = EXIT_SCHEMA if failures else EXIT_OK
    return LintResult(
        exit_code=exit_code,
        skill_count=len(skill_paths),
        agent_count=len(agent_paths),
        failures=tuple(failures),
    )


def _schema_lint_mode(*, min_agents: int, min_skills: int) -> int:
    """Run the default (no-flag) schema lint and return its exit code.

    Split out of ``main()`` so the dispatcher stays a flat list of mode
    branches, and so the floor/dependency preconditions live next to the run
    they guard rather than among the argument definitions.
    """
    # Floors are validated here rather than via argparse's ``type=``: a type
    # callable raises SystemExit(2), and 2 is already EXIT_PROTECTED, so a bad
    # flag would be indistinguishable from a missing breaking-change marker.
    # Checked before the dependency probe so the message appears even when the
    # dev extras are not installed.
    floor_errors = [
        message
        for message in (
            _invalid_floor("--min-agents", min_agents),
            _invalid_floor("--min-skills", min_skills),
        )
        if message is not None
    ]
    if floor_errors:
        for message in floor_errors:
            logger.error("Frontmatter lint failed: %s", message)
        return EXIT_SCHEMA

    if not _SCHEMA_DEPS_AVAILABLE:
        logger.error(
            "Schema-lint mode requires the 'dev' extra (pydantic, pyyaml); "
            "run `pip install -e '.[dev]'`, or use --hook for a stdlib-only mode."
        )
        return EXIT_SCHEMA

    result = run_schema_lint(min_agents=min_agents, min_skills=min_skills)

    if result.failures:
        for msg in result.failures:
            logger.error("Frontmatter lint failed: %s", msg)
        return result.exit_code

    # Counts go in the message, not extra={}: the configured format string drops
    # extra, so a "0 skills, 0 agents" run used to be indistinguishable from a
    # healthy one in CI output.
    logger.info(
        "Frontmatter lint passed: %d skills, %d agents",
        result.skill_count,
        result.agent_count,
    )
    return result.exit_code


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
    parser.add_argument(
        "--min-agents",
        type=int,
        default=MIN_AGENT_FILES,
        help=(
            f"Minimum agent files the glob must match "
            f"(default: {MIN_AGENT_FILES}, minimum: {MIN_FLOOR_LOWER_BOUND})."
        ),
    )
    parser.add_argument(
        "--min-skills",
        type=int,
        default=MIN_SKILL_FILES,
        help=(
            f"Minimum skill files the glob must match "
            f"(default: {MIN_SKILL_FILES}, minimum: {MIN_FLOOR_LOWER_BOUND})."
        ),
    )
    args = parser.parse_args(argv)

    if args.hook == "pre-tool-use":
        return _pre_tool_use_hook(sys.stdin)
    if args.hook == "post-tool-use":
        return _post_tool_use_emit_path(sys.stdin) if args.emit_path else EXIT_OK

    if args.protected_path is not None:
        return _check_protected_path(args.protected_path)

    return _schema_lint_mode(min_agents=args.min_agents, min_skills=args.min_skills)


if __name__ == "__main__":
    sys.exit(main())
