"""Token-based detector for host-destructive shell commands (spec-0043 / ADR-0039).

Stdlib-only: the ``PreToolUse`` hook in ``lint_agent_frontmatter.py`` imports
this module before ``pydantic``/``yaml`` are guaranteed to exist.

A regex anchored on ``<program> <flag>`` misses reordered options
(``rm build -rf``, ``git push origin main --force``) and Git global options
(``git -C repo reset --hard``). This module instead splits the command into
shell segments, finds each known program anywhere in a segment (so ``sudo``,
``env``, ``xargs`` and absolute program paths do not hide it), and inspects
that program's arguments position-independently. Nested ``bash -c`` /
``pwsh -Command`` / ``cmd /c`` payloads are re-scanned up to a fixed depth.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Sequence
from typing import Final

RM_REASON: Final[str] = "recursive or forced `rm` deletion is blocked by the harness"
REMOVE_ITEM_REASON: Final[str] = (
    "recursive or forced `Remove-Item` deletion is blocked by the harness"
)
WINDOWS_DELETE_REASON: Final[str] = (
    "recursive Windows shell deletion (`/s`) is blocked by the harness"
)
GIT_RESET_REASON: Final[str] = (
    "`git reset --hard` is blocked by the harness to prevent worktree data loss"
)
GIT_CLEAN_REASON: Final[str] = (
    "`git clean -f` is blocked by the harness to prevent untracked file loss"
)
GIT_PUSH_REASON: Final[str] = "force-pushing is blocked by the harness"
GIT_OUTPUT_REASON: Final[str] = (
    "`git diff/log/show --output` arbitrary file overwrite is blocked by the harness"
)
GUARD_PROBE_REASON: Final[str] = "overriding `guard_probe.py --manifest` is blocked by the harness"
NESTED_DEPTH_REASON: Final[str] = (
    "shell nesting deeper than the inspectable limit is blocked by the harness"
)

MAX_NESTED_SHELL_DEPTH: Final[int] = 3

_SEGMENT_SEPARATORS: Final[frozenset[str]] = frozenset({";", "&", "&&", "|", "||", "(", ")"})
_END_OF_OPTIONS: Final[str] = "--"
_SHORT_FLAG_CLUSTER_RE: Final[re.Pattern[str]] = re.compile(r"-[A-Za-z]+")
_GIT_GLOBAL_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
)
_POSIX_SHELLS: Final[frozenset[str]] = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_POWERSHELLS: Final[frozenset[str]] = frozenset({"pwsh", "powershell"})
_CMD_SHELL_FLAGS: Final[frozenset[str]] = frozenset({"/c", "/k"})

Checker = Callable[[Sequence[str]], str | None]


def _unquote(token: str) -> str:
    """Strip one pair of matching outer quotes (``shlex`` non-POSIX keeps them)."""
    if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _program_name(token: str) -> str:
    """Normalise ``/usr/bin/rm`` / ``C:\\Windows\\git.EXE`` to ``rm`` / ``git``."""
    # Strip quote/escape residue left by escaped nesting (``\'bash``) first.
    name = token.strip("\"'\\").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.removesuffix(".exe")


def _options(args: Sequence[str]) -> list[str]:
    """Arguments up to an end-of-options ``--`` marker (later tokens are operands)."""
    if _END_OF_OPTIONS in args:
        return list(args[: list(args).index(_END_OF_OPTIONS)])
    return list(args)


def _has_short_flag(args: Sequence[str], letters: str) -> bool:
    """True if any ``-abc`` cluster in *args* contains one of *letters*."""
    return any(
        _SHORT_FLAG_CLUSTER_RE.fullmatch(arg) is not None and any(ch in arg[1:] for ch in letters)
        for arg in _options(args)
    )


def _is_powershell_abbrev(arg: str, parameter: str, min_len: int) -> bool:
    """PowerShell accepts any unambiguous prefix of a parameter name."""
    lowered = arg.lower()
    return len(lowered) >= min_len and parameter.startswith(lowered)


def _check_rm(args: Sequence[str]) -> str | None:
    options = _options(args)
    if _has_short_flag(options, "rRf") or {"--recursive", "--force"} & set(options):
        return RM_REASON
    return None


def _check_powershell_remove(args: Sequence[str]) -> str | None:
    for arg in args:
        if _is_powershell_abbrev(arg, "-recurse", 2) or _is_powershell_abbrev(arg, "-force", 3):
            return REMOVE_ITEM_REASON
    return None


def _check_windows_delete(args: Sequence[str]) -> str | None:
    if any(arg.startswith("/") and "/s" in arg.lower() for arg in args):
        return WINDOWS_DELETE_REASON
    return _check_powershell_remove(args)


def _git_subcommand(args: Sequence[str]) -> tuple[str, list[str]]:
    """Skip Git global options (``-C repo``, ``-c k=v``, ``--no-pager``) to the subcommand."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return arg.lower(), list(args[index + 1 :])
    return "", []


def _check_git_push(args: Sequence[str]) -> str | None:
    options = _options(args)
    forced = _has_short_flag(options, "f") or any(arg.startswith("--force") for arg in options)
    plus_refspec = any(arg.startswith("+") for arg in args)
    return GIT_PUSH_REASON if forced or plus_refspec else None


def _check_git(args: Sequence[str]) -> str | None:
    subcommand, rest = _git_subcommand(args)
    options = _options(rest)
    if subcommand == "reset" and "--hard" in options:
        return GIT_RESET_REASON
    if subcommand == "clean" and (_has_short_flag(options, "f") or "--force" in options):
        return GIT_CLEAN_REASON
    if subcommand == "push":
        return _check_git_push(rest)
    if subcommand in {"diff", "log", "show"} and any(
        arg == "--output" or arg.startswith("--output=") for arg in options
    ):
        return GIT_OUTPUT_REASON
    return None


_GUARD_PROBE_MODULES: Final[frozenset[str]] = frozenset({"guard_probe", "scripts.guard_probe"})


_PYTHON_VALUE_OPTIONS: Final[frozenset[str]] = frozenset({"-W", "-X", "--check-hash-based-pycs"})
_VERSIONED_PYTHON_RE: Final[re.Pattern[str]] = re.compile(
    r"python\d+\.\d+(?:\.\d+)*|py\d+(?:\.\d+)*"
)


_MIN_MANIFEST_PREFIX_LEN = 3


def _is_manifest_flag(arg: str) -> bool:
    flag = arg.split("=", 1)[0]
    return len(flag) >= _MIN_MANIFEST_PREFIX_LEN and "--manifest".startswith(flag)


def _check_guard_probe(args: Sequence[str]) -> str | None:
    if any(_is_manifest_flag(arg) for arg in args):
        return GUARD_PROBE_REASON
    return None


def _check_python(args: Sequence[str]) -> str | None:
    """Block ``python -m [scripts.]guard_probe --manifest ...`` (spec-0050 D-11)."""
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in {"--", "-"} or not arg.startswith("-"):
            return None
        if arg in _PYTHON_VALUE_OPTIONS:
            idx += 2
            continue
        if arg.startswith("--"):
            idx += 1
            continue
        for pos, ch in enumerate(arg[1:]):
            if ch == "c":
                return None
            if ch in {"W", "X"} and pos + 2 == len(arg):
                idx += 1
                break
            if ch == "m":
                after = arg[2 + pos :]
                if after:
                    module, rest = after, args[idx + 1 :]
                elif idx + 1 < len(args):
                    module, rest = args[idx + 1], args[idx + 2 :]
                else:
                    return None
                if module in _GUARD_PROBE_MODULES:
                    return _check_guard_probe(rest)
                return None
        idx += 1
    return None


# Program name → checker over that program's arguments; ``None`` means allowed.
# PowerShell resolves ``del``/``erase``/``rd``/``rmdir``/``ri`` to Remove-Item,
# so the Windows-delete checker also honours ``-Recurse``/``-Force``.
DESTRUCTIVE_BASH_RULES: Final[dict[str, Checker]] = {
    "rm": _check_rm,
    "remove-item": _check_powershell_remove,
    "ri": _check_powershell_remove,
    "del": _check_windows_delete,
    "erase": _check_windows_delete,
    "rd": _check_windows_delete,
    "rmdir": _check_windows_delete,
    "git": _check_git,
    "guard_probe.py": _check_guard_probe,
    "python": _check_python,
    "python3": _check_python,
    "py": _check_python,
}


def _tokenize(command: str) -> list[str]:
    """Quote-aware tokens with shell operators split out; whitespace split on bad quoting."""
    flattened = command.replace("\r\n", " ; ").replace("\n", " ; ")
    lexer = shlex.shlex(flattened, posix=False, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        return [_unquote(token) for token in lexer]
    except ValueError:
        return re.sub(r"([;&|()])", r" \1 ", flattened).split()


def _segments(tokens: Sequence[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _nested_payload(program: str, args: Sequence[str]) -> str | None:
    """Return the inner command of ``bash -c``/``pwsh -Command``/``cmd /c``, if any."""
    for index, arg in enumerate(args):
        is_posix_c = program in _POSIX_SHELLS and _has_short_flag([arg], "c")
        is_pwsh_c = program in _POWERSHELLS and _is_powershell_abbrev(arg, "-command", 2)
        if (is_posix_c or is_pwsh_c) and index + 1 < len(args):
            return args[index + 1]
        if program == "cmd" and arg.lower() in _CMD_SHELL_FLAGS:
            return " ".join(args[index + 1 :])
    return None


def _segment_reason(segment: Sequence[str], depth: int) -> str | None:
    for index, token in enumerate(segment):
        program = _program_name(token)
        args = segment[index + 1 :]
        checker = DESTRUCTIVE_BASH_RULES.get(program)
        if checker is None and _VERSIONED_PYTHON_RE.fullmatch(program) is not None:
            checker = _check_python
        if checker is not None and (reason := checker(args)) is not None:
            return reason
        payload = _nested_payload(program, args)
        if payload is not None and (reason := destructive_bash_reason(payload, depth + 1)):
            return reason
    return None


def destructive_bash_reason(command: str, depth: int = 0) -> str | None:
    """Return the block reason if *command* is host-destructive, else ``None``."""
    if depth > MAX_NESTED_SHELL_DEPTH:
        return NESTED_DEPTH_REASON
    for segment in _segments(_tokenize(command)):
        reason = _segment_reason(segment, depth)
        if reason is not None:
            return reason
    return None
