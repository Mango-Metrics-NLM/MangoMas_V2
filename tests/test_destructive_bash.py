"""Token-based destructive-command detector behind the ``PreToolUse`` deny gate."""

from __future__ import annotations

import pytest

from tests._script_loader import load_script_module

guard = load_script_module("_destructive_bash.py")


@pytest.mark.parametrize(
    ("command", "expected_reason"),
    [
        # Reordered options and Git global options (PR #119 review).
        ("rm build -rf", guard.RM_REASON),
        ("del /q /s build", guard.WINDOWS_DELETE_REASON),
        ("git reset HEAD --hard", guard.GIT_RESET_REASON),
        ("git clean -d -f", guard.GIT_CLEAN_REASON),
        ("git push origin main --force", guard.GIT_PUSH_REASON),
        ("git -C repo reset --hard", guard.GIT_RESET_REASON),
        ("git -c core.pager=cat --no-pager push -f origin", guard.GIT_PUSH_REASON),
        ("git --git-dir .git push origin main --force-with-lease", guard.GIT_PUSH_REASON),
        ("git push origin +main", guard.GIT_PUSH_REASON),
        ("git push -uf origin main", guard.GIT_PUSH_REASON),
        ("git log --output out.txt", guard.GIT_OUTPUT_REASON),
        ("git show HEAD --output=src/x.py", guard.GIT_OUTPUT_REASON),
        # Wrappers, absolute program paths and case.
        ("sudo rm -rf /", guard.RM_REASON),
        ("env FOO=1 /bin/rm -R build", guard.RM_REASON),
        ("find . -name '*.pyc' | xargs rm -f", guard.RM_REASON),
        ("RM -RF build", guard.RM_REASON),
        ("C:\\Git\\bin\\git.EXE reset --hard", guard.GIT_RESET_REASON),
        # PowerShell parameter abbreviations and Remove-Item aliases.
        ("Remove-Item build -r", guard.REMOVE_ITEM_REASON),
        ("ri build -Force", guard.REMOVE_ITEM_REASON),
        ("rd build -Recurse", guard.REMOVE_ITEM_REASON),
        ("RD /S /Q build", guard.WINDOWS_DELETE_REASON),
        ("erase /q/s build", guard.WINDOWS_DELETE_REASON),
        # Segments, newlines and nested shells.
        ("git status && git reset --hard", guard.GIT_RESET_REASON),
        ("ls\nrm -rf build", guard.RM_REASON),
        ('bash -c "rm -rf build"', guard.RM_REASON),
        ("bash -lc 'git clean -fdx'", guard.GIT_CLEAN_REASON),
        ('pwsh -NoProfile -Command "Remove-Item build -Recurse"', guard.REMOVE_ITEM_REASON),
        ("cmd /c rd /s /q build", guard.WINDOWS_DELETE_REASON),
        ("python scripts\\guard_probe.py --manifest evil.json", guard.GUARD_PROBE_REASON),
        # Unbalanced quoting falls back to a whitespace split, still fail-closed.
        ('echo "unterminated; rm -rf build', guard.RM_REASON),
    ],
)
def test_destructive_commands_are_detected(command: str, expected_reason: str) -> None:
    assert guard.destructive_bash_reason(command) == expected_reason


@pytest.mark.parametrize(
    "command",
    [
        "",
        "rm file.txt",
        "rm -- -rf",
        "rm -i notes.txt",
        "git",
        "git -C",
        "git status",
        "git -C repo status",
        "git push origin main",
        "git clean -n",
        "git log --oneline",
        'git commit -m "git reset --hard is blocked"',
        'echo "rm -rf build"',
        "grep -r rm .",
        "Remove-Item file.txt",
        "del /q file.txt",
        "rmdir -p a/b",
        "bash -c",
        "cmd /c",
        'pwsh -NoProfile -Command "git status"',
        "python scripts/guard_probe.py",
    ],
)
def test_ordinary_commands_are_allowed(command: str) -> None:
    assert guard.destructive_bash_reason(command) is None


def test_nesting_beyond_the_inspectable_depth_is_denied() -> None:
    command = "cmd /c " * (guard.MAX_NESTED_SHELL_DEPTH + 1) + "git status"
    assert guard.destructive_bash_reason(command) == guard.NESTED_DEPTH_REASON


def test_nesting_within_the_depth_limit_is_inspected_not_blocked() -> None:
    command = "cmd /c " * guard.MAX_NESTED_SHELL_DEPTH + "git status"
    assert guard.destructive_bash_reason(command) is None


def test_escaped_nested_quoting_still_reaches_the_inner_command() -> None:
    assert guard.destructive_bash_reason("bash -c 'bash -c \\'rm -rf build\\''") == guard.RM_REASON


def test_every_rule_names_a_checker() -> None:
    """The rule table is the documented surface; each entry must be callable."""
    assert set(guard.DESTRUCTIVE_BASH_RULES) >= {"rm", "remove-item", "del", "git"}
    for checker in guard.DESTRUCTIVE_BASH_RULES.values():
        assert checker([]) is None
