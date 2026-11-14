"""Stdlib-only Claude Code PreToolUse / PostToolUse hook runtime helpers.

Extracted from ``scripts/lint_agent_frontmatter.py`` (ADR-0021 / spec-0017 /
spec-0043) so hook stdin parsing, decision envelope formatting, and shell-safe
path emission stay decoupled from Pydantic/PyYAML frontmatter linting.
"""

from __future__ import annotations

import json
import logging
import shlex
import sys
from collections.abc import Callable, Collection
from typing import IO, Final

import _destructive_bash
import _stdin_json

HOOK_EVENT_NAME: Final[str] = "PreToolUse"
HOOK_PERMISSION_ASK: Final[str] = "ask"
HOOK_PERMISSION_DENY: Final[str] = "deny"
EXIT_OK: Final[int] = 0

# Characters that carry meaning to a shell, or break the one-path-per-line
# framing `xargs` relies on. Mirrors `mango_contracts.proposed_action.SHELL_INTENT_MARKERS`.
SHELL_UNSAFE_PATH_CHARACTERS: Final[frozenset[str]] = frozenset(";|&`$()<>\n\r\t\"'\\*?!{}[]~")


def read_hook_payload(stream: IO[str], *, log: logging.Logger) -> dict[str, object]:
    """Return the hook's stdin JSON, or ``{}`` on any parse failure."""
    return _stdin_json.read_json_payload(stream, logger=log)


def extract_tool_path(payload: dict[str, object]) -> str | None:
    """Return the path a ``PreToolUse``/``PostToolUse`` payload names, if any."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def extract_bash_command(payload: dict[str, object]) -> str | None:
    """Return ``tool_input.command`` from a ``Bash`` payload, if any."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("command")
    if isinstance(value, str) and value:
        return value
    return None


def protected_paths_in_command(command: str, protected_paths: Collection[str]) -> list[str]:
    """Return sorted *protected_paths* mentioned in *command* (backslash-normalised)."""
    normalised = command.replace("\\", "/").lower()
    return sorted(path for path in protected_paths if path.lower() in normalised)


def destructive_bash_reason(command: str) -> str | None:
    """Return the block reason if *command* is host-destructive, else ``None``."""
    return _destructive_bash.destructive_bash_reason(command)


def emit_decision(decision_value: str, reason: str) -> None:
    """Print a ``PreToolUse`` ``hookSpecificOutput`` JSON decision envelope."""
    decision = {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "permissionDecision": decision_value,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))


def emit_ask(reason: str) -> None:
    """Print a ``permissionDecision: "ask"`` envelope."""
    emit_decision(HOOK_PERMISSION_ASK, reason)


def emit_deny(reason: str) -> None:
    """Print a ``permissionDecision: "deny"`` envelope for destructive Bash calls."""
    emit_decision(HOOK_PERMISSION_DENY, reason)


def is_shell_safe_path(path: str) -> bool:
    """Return whether *path* is free of shell metacharacters and framing breaks."""
    return not any(char in SHELL_UNSAFE_PATH_CHARACTERS for char in path)


def pre_tool_use_hook(
    stream: IO[str],
    *,
    log: logging.Logger,
    canonical_protected_path: Callable[[str], str | None],
    protected_paths: Collection[str],
    breaking_change_marker: str,
) -> int:
    """Protected-path advisory (`ask`) + destructive-Bash gate (`deny`). Always exits 0."""
    payload = read_hook_payload(stream, log=log)
    path = extract_tool_path(payload)
    if path is not None:
        normalised = canonical_protected_path(path)
        if normalised is None:
            return EXIT_OK
        emit_ask(
            f"{normalised} is a protected core contract (CLAUDE.md 'File "
            f"Ownership'). Approve only if this change will land in a commit "
            f"whose message contains {breaking_change_marker!r} — "
            f"scripts/check_protected_paths.py enforces this in CI."
        )
        return EXIT_OK

    command = extract_bash_command(payload)
    if command is None:
        return EXIT_OK
    reason = destructive_bash_reason(command)
    if reason is not None:
        emit_deny(reason)
        return EXIT_OK
    mentioned = protected_paths_in_command(command, protected_paths)
    if not mentioned:
        return EXIT_OK
    emit_ask(
        f"This command mentions protected core contract(s): "
        f"{', '.join(mentioned)} (CLAUDE.md 'File Ownership'). If it writes "
        f"to them, the change must land in a commit whose message contains "
        f"{breaking_change_marker!r} — scripts/check_protected_paths.py "
        f"enforces this in CI. This is a mention-level advisory: a read-only "
        f"command (grep, cat, git diff) trips it too."
    )
    return EXIT_OK


def post_tool_use_emit_path(stream: IO[str], *, log: logging.Logger) -> int:
    """Print the edited file's path (or nothing). Always returns ``EXIT_OK``."""
    path = extract_tool_path(read_hook_payload(stream, log=log))
    if not path:
        return EXIT_OK
    normalized = path.replace("\\", "/")
    if not is_shell_safe_path(normalized):
        # stderr, not stdout: stdout is the pipeline's input.
        print(
            f"lint_agent_frontmatter: refusing to emit path with shell syntax: {path!r}",
            file=sys.stderr,
        )
        return EXIT_OK
    print(shlex.quote(normalized) if any(ch.isspace() for ch in normalized) else normalized)
    return EXIT_OK
