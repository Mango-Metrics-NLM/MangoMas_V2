"""CI gate: a loop PR may change only what an approved backlog item allows.

The loop scope check of ADR-0042 / spec-0052. It runs on ``pull_request_target``
from ``.github/workflows/loop-scope.yml``, so GitHub executes the **default
branch's** copy of this script, of that workflow and of the policy it reads,
never the pull request's. It reads the pull request only through the GitHub
API (its metadata, the file listing with patches, the head tree, and every
other pull request) and never checks out or executes PR code. A PR that edits
this script, the workflow or the policy is judged by the copies already on the
default branch, and the edit itself is a trusted-base violation.

Classification
--------------
A head that starts with the policy's ``implementation_prefix``
(``claude/loop-``) is an implementation branch, and one that starts with
``proposal_prefix`` (``claude/proposal-``) is a proposal branch. A PR opened by
one of ``loop_identities`` from any other head is misrouted and fails.
Everything else (human branches, Dependabot, interactive ``claude/<name>``
sessions) is ungoverned and passes. The prefix is a label, not an identity
(spec-0052 R2): until a publication boundary is recorded, these rules catch
mistakes, not evasion, and the loop does not run.

Fail-closed choices
-------------------
ADR-0030 made two deliberate trade-offs for human-authored PRs: a bare marker
approves everything, and an unreadable base policy falls back to the working
tree. An author optimising against the gate gets the opposite: an unreadable
policy fails every ``claude/`` head (other heads pass with a warning, so a
human PR can restore it), and so do a truncated file listing, a missing patch,
a truncated tree, a head that moved during the check, a governed head from a
fork or aimed at a base other than ``base_branch``, an unknown backlog item and,
for implementation branches, ``LOOP_ENABLED`` not set to ``true``.

Exit codes
----------
``EXIT_OK = 0``
    The PR is ungoverned, or governed and inside its scope.
``EXIT_VIOLATION = 1``
    A governed PR broke at least one rule. Every finding is printed.
``EXIT_ERROR = 2``
    The check could not run: missing environment, an unreadable event, or a
    GitHub API failure. Like a violation, this fails the required check.

Run (CI provides the environment; see the workflow)::

    make loop-scope
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

import _governance

EXIT_OK: Final[int] = 0
EXIT_VIOLATION: Final[int] = 1
EXIT_ERROR: Final[int] = 2

DEFAULT_PYPROJECT_PATH: Final[Path] = Path("pyproject.toml")
DEFAULT_BACKLOG_DIR: Final[Path] = Path("backlog")
DEFAULT_API_URL: Final[str] = "https://api.github.com"

# Every Claude Code branch lives under this namespace. It is fixed here rather
# than read from the policy because it is what applies when the policy cannot
# be read at all.
GOVERNED_NAMESPACE: Final[str] = "claude/"
LOOP_ENABLED_VALUE: Final[str] = "true"
DEFAULT_MAX_DIFF_LINES: Final[int] = 300  # spec-0053 R2

ITEM_ID_RE: Final[re.Pattern[str]] = re.compile(r"LOOP-\d{4}")
# `claude/loop-LOOP-0007-2` names item LOOP-0007, attempt 2 (spec-0052 R2).
_IMPLEMENTATION_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<item>LOOP-\d{4})-(?P<attempt>[1-9]\d*)"
)

# GitHub's documented ceiling for "List pull request files".
GITHUB_FILE_LISTING_CAP: Final[int] = 3000
PAGE_SIZE: Final[int] = 100
MAX_PAGES: Final[int] = 100
REQUEST_TIMEOUT_SECONDS: Final[float] = 30.0
HTTP_OK: Final[int] = 200
API_VERSION: Final[str] = "2022-11-28"
USER_AGENT: Final[str] = "mangomas-loop-scope-check"
_REPOSITORY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_NEXT_LINK_RE: Final[re.Pattern[str]] = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_BACKTICK_RUN_RE: Final[re.Pattern[str]] = re.compile(r"`+")
_MIN_FENCE: Final[int] = 3

# Git tree modes a governed branch may not introduce.
LINK_MODES: Final[Mapping[str, str]] = {"120000": "symlink", "160000": "submodule"}

ENV_EVENT_PATH: Final[str] = "GITHUB_EVENT_PATH"
ENV_REPOSITORY: Final[str] = "GITHUB_REPOSITORY"
ENV_API_URL: Final[str] = "GITHUB_API_URL"
# The variable *name*; the value only ever comes from the environment, never
# from a command-line argument where it would show in a process listing.
ENV_AUTH: Final[str] = "GITHUB_TOKEN"
ENV_LOOP_ENABLED: Final[str] = "LOOP_ENABLED"
ENV_LOOP_STAGE: Final[str] = "LOOP_STAGE"
ENV_STEP_SUMMARY: Final[str] = "GITHUB_STEP_SUMMARY"

STATUS_ADDED: Final[str] = "added"
STATUS_REMOVED: Final[str] = "removed"
STATUS_RENAMED: Final[str] = "renamed"
STATUS_COPIED: Final[str] = "copied"
_NEW_FILE_STATUSES: Final[frozenset[str]] = frozenset({STATUS_ADDED, STATUS_COPIED})
_MODIFY_STATUSES: Final[frozenset[str]] = frozenset({"modified", "changed", "unchanged"})
KNOWN_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_REMOVED, STATUS_RENAMED} | _NEW_FILE_STATUSES | _MODIFY_STATUSES
)

# Comments and calls that silence a gate (spec-0052 R4). An implementation diff
# may add one only when its backlog item lists the name in
# `allowed_suppressions`. The coverage pragma is matched the way coverage.py
# itself does, with or without the colon.
SUPPRESSION_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = {
    "pragma-no-cover": re.compile(r"pragma\s*:?\s*no\s*cover", re.IGNORECASE),
    "pragma-no-branch": re.compile(r"pragma\s*:?\s*no\s*branch", re.IGNORECASE),
    "noqa": re.compile(r"#\s*noqa\b", re.IGNORECASE),
    "ruff-noqa": re.compile(r"#\s*ruff\s*:\s*noqa\b", re.IGNORECASE),
    "type-ignore": re.compile(r"#\s*type\s*:\s*ignore\b", re.IGNORECASE),
    "mypy-directive": re.compile(r"#\s*mypy\s*:", re.IGNORECASE),
    "pyright-directive": re.compile(r"#\s*pyright\s*:", re.IGNORECASE),
    "fmt-directive": re.compile(r"#\s*fmt\s*:\s*(?:off|skip)\b", re.IGNORECASE),
    "pytest-skip": re.compile(r"\bpytest\s*\.\s*(?:skip|xfail|importorskip)\s*\("),
    "skip-mark": re.compile(r"\bmark\s*\.\s*(?:skipif|skip|xfail)\b"),
    "unittest-skip": re.compile(
        r"\bunittest\s*\.\s*(?:skipIf|skipUnless|skip|expectedFailure)\b"
        r"|@\s*(?:skipIf|skipUnless|skip|expectedFailure)\b"
        r"|\.skipTest\s*\("
    ),
}

_GLOB_TOKENS: Final[Mapping[str, str]] = {"*": "[^/]*", "?": "[^/]"}


class PolicyError(Exception):
    """The ``[tool.mangomas.governance.loop]`` table is missing or malformed."""


class ItemError(Exception):
    """A backlog item is missing, unparseable or malformed."""


class GitHubError(Exception):
    """A GitHub API call failed or returned something of an unexpected shape."""


class ConfigError(Exception):
    """The environment or the event payload the check needs is missing."""


def _q(value: str) -> str:
    """Quote an untrusted string for output.

    JSON escaping turns control characters (a newline in a file name, say) into
    visible escapes, so a hostile path can never start its own line, and with
    it a ``::`` workflow command, in the job log.
    """
    return json.dumps(value)


# ── Globs ─────────────────────────────────────────────────────────────────────


def compile_glob(pattern: str, *, case_sensitive: bool) -> re.Pattern[str]:
    """Compile a repository glob: ``**`` spans whole segments, ``*`` and ``?`` one.

    ``**`` as the last segment matches one or more segments (everything
    beneath a directory); anywhere else it matches zero or more, so
    ``**/conftest.py`` also matches a root ``conftest.py``.
    """
    if not pattern or pattern.startswith("/") or "\\" in pattern:
        raise PolicyError(f"glob {_q(pattern)} must be a relative POSIX path pattern")
    segments = pattern.split("/")
    parts: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment in {"", ".", ".."}:
            raise PolicyError(f"glob {_q(pattern)} has an empty, '.' or '..' segment")
        if segment == "**":
            parts.append(".+" if last else "(?:[^/]+/)*")
            continue
        if "**" in segment:
            raise PolicyError(f"glob {_q(pattern)}: '**' must be a whole segment")
        body = "".join(_GLOB_TOKENS.get(char, re.escape(char)) for char in segment)
        parts.append(body if last else f"{body}/")
    return re.compile("".join(parts), 0 if case_sensitive else re.IGNORECASE)


@dataclass(frozen=True)
class GlobSet:
    """An ordered set of globs that reports which one matched."""

    patterns: tuple[str, ...]
    compiled: tuple[re.Pattern[str], ...]

    @classmethod
    def of(cls, patterns: Iterable[str], *, case_sensitive: bool) -> GlobSet:
        unique = tuple(dict.fromkeys(patterns))
        return cls(unique, tuple(compile_glob(p, case_sensitive=case_sensitive) for p in unique))

    def first_match(self, path: str) -> str | None:
        """Return the first pattern that matches *path* in full, or ``None``."""
        for pattern, regex in zip(self.patterns, self.compiled, strict=True):
            if regex.fullmatch(path):
                return pattern
        return None


def path_problem(path: str) -> str | None:
    """Say why *path* is not a plain repository path, or return ``None``.

    GitHub lists repository-relative POSIX paths. Anything else (a backslash,
    which Windows checkouts read as a separator, a control character, an empty
    or ``..`` segment) has no legitimate place in a governed diff.
    """
    if not path or path.startswith("/"):
        return "empty or absolute"
    if "\\" in path or any(ord(char) < ord(" ") for char in path):
        return "contains a backslash or a control character"
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        return "has an empty, '.' or '..' segment"
    return None


# ── Policy ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopPolicy:
    """``[tool.mangomas.governance.loop]``, validated.

    Deny lists (``denied_anywhere``, ``trusted_base``) match case-insensitively,
    because macOS and Windows checkouts fold case; allow lists match exactly.
    ``trusted_base`` also holds every ``protected_paths`` entry.
    """

    implementation_prefix: str
    proposal_prefix: str
    base_branch: str
    loop_identities: frozenset[str]
    stages: tuple[str, ...]
    max_open_implementation_prs: int
    max_consecutive_failures: int
    halt_reset_after_pr: int
    denied_anywhere: GlobSet
    trusted_base: GlobSet
    work_area: GlobSet
    proposal_new_paths: GlobSet
    proposal_index_files: GlobSet

    def deny_match(self, path: str) -> str | None:
        """Return the deny glob *path* hits (``denied_anywhere`` first), or ``None``."""
        return self.denied_anywhere.first_match(path) or self.trusted_base.first_match(path)


_POLICY_STR_KEYS: Final[tuple[str, ...]] = (
    "implementation_prefix",
    "proposal_prefix",
    "base_branch",
)
_POLICY_LIST_KEYS: Final[tuple[str, ...]] = (
    "stages",
    "denied_anywhere",
    "trusted_base",
    "work_area",
    "proposal_new_paths",
    "proposal_index_files",
)
# Minimum value of each integer limit.
_POLICY_INT_KEYS: Final[Mapping[str, int]] = {
    "max_open_implementation_prs": 1,
    "max_consecutive_failures": 1,
    "halt_reset_after_pr": 0,
}
_POLICY_KEYS: Final[frozenset[str]] = frozenset(
    {*_POLICY_STR_KEYS, *_POLICY_LIST_KEYS, *_POLICY_INT_KEYS, "loop_identities"}
)


def _table_str(table: Mapping[str, object], key: str, *, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{where}: {key!r} must be a non-empty string")
    return value


def _table_str_list(
    table: Mapping[str, object], key: str, *, where: str, allow_empty: bool = False
) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise PolicyError(f"{where}: {key!r} must be a list of non-empty strings")
    if not value and not allow_empty:
        raise PolicyError(f"{where}: {key!r} must not be empty")
    return tuple(str(v) for v in value)


def _table_int(table: Mapping[str, object], key: str, *, where: str, minimum: int) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PolicyError(f"{where}: {key!r} must be an integer >= {minimum}")
    return value


def parse_loop_policy(toml_text: str, *, source: str) -> LoopPolicy:
    """Return the validated loop policy in *toml_text*.

    Raises :class:`PolicyError` on a missing table, an unknown or missing key,
    a wrong type, an empty glob list or a bad glob, so a typo can never quietly
    disable a rule.
    """
    where = f"[tool.mangomas.governance.loop] in {source}"
    try:
        doc = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"malformed TOML in {source}: {exc}") from exc
    try:
        table = doc["tool"]["mangomas"]["governance"]["loop"]
    except (KeyError, TypeError) as exc:
        raise PolicyError(f"{where} is missing") from exc
    if not isinstance(table, dict):
        raise PolicyError(f"{where} must be a table")
    unknown = sorted(set(table) - _POLICY_KEYS)
    missing = sorted(_POLICY_KEYS - set(table))
    if unknown or missing:
        raise PolicyError(f"{where}: unknown keys {unknown}, missing keys {missing}")
    try:
        protected, _aliases = _governance.parse_governance(toml_text, source=source)
    except _governance.GovernanceLoadError as exc:
        raise PolicyError(str(exc)) from exc

    strings = {key: _table_str(table, key, where=where) for key in _POLICY_STR_KEYS}
    lists = {key: _table_str_list(table, key, where=where) for key in _POLICY_LIST_KEYS}
    limits = {
        key: _table_int(table, key, where=where, minimum=minimum)
        for key, minimum in _POLICY_INT_KEYS.items()
    }
    identities = _table_str_list(table, "loop_identities", where=where, allow_empty=True)
    prefixes = (strings["implementation_prefix"], strings["proposal_prefix"])
    for prefix in prefixes:
        if not prefix.startswith(GOVERNED_NAMESPACE) or prefix == GOVERNED_NAMESPACE:
            raise PolicyError(f"{where}: prefix {_q(prefix)} must sit inside {GOVERNED_NAMESPACE}")
    if prefixes[0].startswith(prefixes[1]) or prefixes[1].startswith(prefixes[0]):
        raise PolicyError(f"{where}: the two prefixes must not overlap")
    if len(set(lists["stages"])) != len(lists["stages"]):
        raise PolicyError(f"{where}: 'stages' must not repeat a stage")

    def deny(*keys: str, extra: Iterable[str] = ()) -> GlobSet:
        patterns = [p for key in keys for p in lists[key]]
        return GlobSet.of([*patterns, *extra], case_sensitive=False)

    def allow(key: str) -> GlobSet:
        return GlobSet.of(lists[key], case_sensitive=True)

    return LoopPolicy(
        implementation_prefix=prefixes[0],
        proposal_prefix=prefixes[1],
        base_branch=strings["base_branch"],
        loop_identities=frozenset(identity.casefold() for identity in identities),
        stages=lists["stages"],
        max_open_implementation_prs=limits["max_open_implementation_prs"],
        max_consecutive_failures=limits["max_consecutive_failures"],
        halt_reset_after_pr=limits["halt_reset_after_pr"],
        denied_anywhere=deny("denied_anywhere"),
        trusted_base=deny(
            "trusted_base",
            extra=sorted(_governance.normalize_repo_path(path) for path in protected),
        ),
        work_area=allow("work_area"),
        proposal_new_paths=allow("proposal_new_paths"),
        proposal_index_files=allow("proposal_index_files"),
    )


def load_loop_policy(pyproject_path: Path) -> LoopPolicy:
    """Read and validate the loop policy from *pyproject_path*."""
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"cannot read {pyproject_path}: {exc}") from exc
    return parse_loop_policy(text, source=str(pyproject_path))


# ── Backlog items ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BacklogItem:
    """The spec-0053 fields the scope check enforces; the rest belong to M2's validator."""

    item_id: str
    allowed_paths: GlobSet
    allowed_suppressions: frozenset[str]
    max_diff_lines: int
    depends_on: tuple[str, ...]
    min_stage: str
    max_attempts: int


def parse_backlog_item(toml_text: str, *, item_id: str, source: str) -> BacklogItem:
    """Return the item in *toml_text*, which must declare ``id = item_id``."""
    try:
        doc = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ItemError(f"backlog item {source} is not valid TOML: {exc}") from exc
    try:
        declared = _table_str(doc, "id", where=source)
        allowed = _table_str_list(doc, "allowed_paths", where=source)
        suppressions = _table_str_list(
            {"allowed_suppressions": doc.get("allowed_suppressions", [])},
            "allowed_suppressions",
            where=source,
            allow_empty=True,
        )
        depends_on = _table_str_list(
            {"depends_on": doc.get("depends_on", [])}, "depends_on", where=source, allow_empty=True
        )
        max_diff_lines = _table_int(
            {"max_diff_lines": doc.get("max_diff_lines", DEFAULT_MAX_DIFF_LINES)},
            "max_diff_lines",
            where=source,
            minimum=1,
        )
        min_stage = _table_str(doc, "min_stage", where=source)
        budget = doc.get("budget")
        if not isinstance(budget, dict):
            raise PolicyError(f"{source}: 'budget' must be a table")
        max_attempts = _table_int(budget, "max_attempts", where=f"{source} budget", minimum=1)
        allowed_paths = GlobSet.of(allowed, case_sensitive=True)
    except PolicyError as exc:
        raise ItemError(str(exc)) from exc
    if declared != item_id:
        raise ItemError(f"{source} declares id {_q(declared)}, not {item_id}")
    unknown = sorted(set(suppressions) - set(SUPPRESSION_PATTERNS))
    if unknown:
        raise ItemError(f"{source}: unknown allowed_suppressions {unknown}")
    bad_dependencies = [d for d in depends_on if not ITEM_ID_RE.fullmatch(d) or d == item_id]
    if bad_dependencies:
        raise ItemError(f"{source}: invalid depends_on entries {bad_dependencies}")
    return BacklogItem(
        item_id=item_id,
        allowed_paths=allowed_paths,
        allowed_suppressions=frozenset(suppressions),
        max_diff_lines=max_diff_lines,
        depends_on=depends_on,
        min_stage=min_stage,
        max_attempts=max_attempts,
    )


def load_backlog_item(backlog_dir: Path, item_id: str) -> BacklogItem:
    """Read ``<backlog_dir>/<item_id>.toml`` from the default-branch checkout."""
    if not ITEM_ID_RE.fullmatch(item_id):
        raise ItemError(f"{_q(item_id)} is not a backlog item ID (LOOP-NNNN)")
    path = backlog_dir / f"{item_id}.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ItemError(f"no approved backlog item {item_id} on the default branch") from exc
    return parse_backlog_item(text, item_id=item_id, source=path.as_posix())


# ── Pull request evidence ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_ref: str
    head_sha: str
    head_repo: str | None  # None when the head repository was deleted
    base_ref: str
    base_repo: str
    author: str
    changed_files: int
    additions: int
    deletions: int


@dataclass(frozen=True)
class PullSummary:
    """One entry of the repository's pull-request history."""

    number: int
    head_ref: str
    head_repo: str | None
    is_open: bool
    merged: bool


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    changes: int
    patch: str | None
    previous_path: str | None = None

    @property
    def paths(self) -> tuple[str, ...]:
        """Both sides of a rename or copy; the path alone otherwise."""
        if self.previous_path is None:
            return (self.path,)
        return (self.path, self.previous_path)


class EvidenceSource(Protocol):
    """Where the check reads a pull request from: the GitHub API, or a test fake."""

    def pull(self, number: int) -> PullRequest: ...

    def files(self, number: int) -> list[ChangedFile]: ...

    def tree(self, sha: str) -> Mapping[str, str] | None: ...

    def history(self) -> list[PullSummary]: ...


# ── Verdicts and rules ────────────────────────────────────────────────────────


class BranchClass(Enum):
    UNGOVERNED = "ungoverned"
    IMPLEMENTATION = "implementation"
    PROPOSAL = "proposal"
    MISROUTED = "misrouted"


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    path: str | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class Verdict:
    branch_class: BranchClass | None  # None when the policy could not be read
    findings: tuple[Finding, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class Switches:
    """The repository variables the maintainer controls (spec-0052, Config)."""

    loop_enabled: str = ""
    loop_stage: str = ""


def _same_repository(left: str | None, right: str) -> bool:
    return left is not None and left.casefold() == right.casefold()


def classify(policy: LoopPolicy, *, head_ref: str, author: str) -> BranchClass:
    """Classify a PR by its head (a label) and its author (an identity)."""
    if head_ref.startswith(policy.implementation_prefix):
        return BranchClass.IMPLEMENTATION
    if head_ref.startswith(policy.proposal_prefix):
        return BranchClass.PROPOSAL
    if author.casefold() in policy.loop_identities:
        return BranchClass.MISROUTED
    return BranchClass.UNGOVERNED


def implementation_item_id(policy: LoopPolicy, head_ref: str) -> str | None:
    """Return the item an implementation head names, or ``None`` if it names none."""
    if not head_ref.startswith(policy.implementation_prefix):
        return None
    match = _IMPLEMENTATION_SUFFIX_RE.fullmatch(head_ref[len(policy.implementation_prefix) :])
    return match.group("item") if match else None


def origin_findings(policy: LoopPolicy, pull: PullRequest, *, repository: str) -> list[Finding]:
    """A governed head must come from this repository and target ``base_branch``."""
    findings: list[Finding] = []
    if not _same_repository(pull.base_repo, repository):
        findings.append(Finding("origin", f"the PR targets {_q(pull.base_repo)}, not {repository}"))
    if not _same_repository(pull.head_repo, repository):
        findings.append(
            Finding("origin", "a governed head must come from this repository, not a fork")
        )
    if pull.base_ref != policy.base_branch:
        findings.append(
            Finding(
                "origin",
                f"a governed head must target {_q(policy.base_branch)}, not {_q(pull.base_ref)}",
            )
        )
    return findings


def listing_findings(
    pull: PullRequest, files: Sequence[ChangedFile], *, head_sha_after: str
) -> list[Finding]:
    """The listing must be complete, current, and made of plain paths."""
    findings: list[Finding] = []
    if pull.changed_files > GITHUB_FILE_LISTING_CAP:
        findings.append(
            Finding(
                "listing",
                f"GitHub lists at most {GITHUB_FILE_LISTING_CAP} files; this PR changes "
                f"{pull.changed_files}",
            )
        )
    if len(files) != pull.changed_files:
        findings.append(
            Finding(
                "listing",
                f"the file listing is truncated: {len(files)} of {pull.changed_files} files",
            )
        )
    if head_sha_after != pull.head_sha:
        findings.append(
            Finding("listing", "the head moved during the check; the run for the new head decides")
        )
    for changed in files:
        if changed.status not in KNOWN_STATUSES:
            findings.append(
                Finding("listing", f"unknown file status {_q(changed.status)}", changed.path)
            )
        for path in changed.paths:
            problem = path_problem(path)
            if problem is not None:
                findings.append(Finding("path", f"not a plain repository path: {problem}", path))
    return findings


def tree_findings(files: Sequence[ChangedFile], tree: Mapping[str, str] | None) -> list[Finding]:
    """A governed diff may not add a symlink or a submodule."""
    if tree is None:
        return [Finding("tree", "the head tree listing is truncated, so links cannot be ruled out")]
    findings: list[Finding] = []
    for changed in files:
        if changed.status == STATUS_REMOVED:
            continue
        mode = tree.get(changed.path)
        if mode is None:
            findings.append(Finding("tree", "missing from the head tree", changed.path))
        elif mode in LINK_MODES:
            findings.append(
                Finding("tree", f"governed branches may not add a {LINK_MODES[mode]}", changed.path)
            )
    return findings


def implementation_path_findings(
    policy: LoopPolicy, item: BacklogItem, files: Sequence[ChangedFile]
) -> list[Finding]:
    """Every path, on both sides of a rename, must be allowed; deny wins (R3)."""
    findings: list[Finding] = []
    for changed in files:
        for path in changed.paths:
            if path_problem(path) is not None:
                continue  # reported by listing_findings
            denied = policy.deny_match(path)
            if denied is not None:
                findings.append(
                    Finding("trusted-base", "the loop may never change this path", path, denied)
                )
            elif policy.work_area.first_match(path) is None:
                findings.append(Finding("work-area", "outside the loop's work area", path))
            elif item.allowed_paths.first_match(path) is None:
                findings.append(
                    Finding("allowed-paths", f"outside {item.item_id}'s allowed_paths", path)
                )
    return findings


def added_lines(patch: str) -> Iterator[str]:
    """Yield the lines a GitHub ``patch`` adds (it carries no ``+++`` header)."""
    for line in patch.splitlines():
        if line.startswith("+"):
            yield line[1:]


def suppression_findings(files: Sequence[ChangedFile], *, allowed: frozenset[str]) -> list[Finding]:
    """No added line may silence a gate unless the item allows it (R4)."""
    findings: list[Finding] = []
    for changed in files:
        if changed.patch is None:
            if changed.changes > 0:
                findings.append(
                    Finding(
                        "patch",
                        "GitHub returned no patch (binary or too large), so the added lines "
                        "cannot be checked",
                        changed.path,
                    )
                )
            continue
        seen: set[str] = set()
        for line in added_lines(changed.patch):
            for name, pattern in SUPPRESSION_PATTERNS.items():
                if name not in allowed and name not in seen and pattern.search(line):
                    seen.add(name)
                    findings.append(
                        Finding(
                            "suppression",
                            f"adds a {name} suppression the item does not allow",
                            changed.path,
                        )
                    )
    return findings


def diff_size_findings(pull: PullRequest, item: BacklogItem) -> list[Finding]:
    size = pull.additions + pull.deletions
    if size > item.max_diff_lines:
        return [
            Finding(
                "diff-size",
                f"{size} changed lines exceed {item.item_id}'s max_diff_lines "
                f"({item.max_diff_lines})",
            )
        ]
    return []


def enabled_findings(switches: Switches) -> list[Finding]:
    if switches.loop_enabled != LOOP_ENABLED_VALUE:
        return [
            Finding(
                "switch",
                f"LOOP_ENABLED is {_q(switches.loop_enabled)}, not {_q(LOOP_ENABLED_VALUE)}: "
                "the loop is off",
            )
        ]
    return []


def stage_findings(policy: LoopPolicy, item: BacklogItem, switches: Switches) -> list[Finding]:
    stage = switches.loop_stage or policy.stages[0]
    if stage not in policy.stages:
        return [Finding("stage", f"LOOP_STAGE {_q(stage)} is not one of {list(policy.stages)}")]
    if item.min_stage not in policy.stages:
        return [Finding("stage", f"{item.item_id}'s min_stage {_q(item.min_stage)} is no stage")]
    if policy.stages.index(item.min_stage) > policy.stages.index(stage):
        return [
            Finding(
                "stage",
                f"{item.item_id} needs stage {item.min_stage}; the loop is at {stage}",
            )
        ]
    return []


def failure_streak(policy: LoopPolicy, implementation_prs: Iterable[PullSummary]) -> list[int]:
    """Closed, unmerged implementation PRs since the last merge, newest first."""
    closed = sorted(
        (p for p in implementation_prs if not p.is_open and p.number > policy.halt_reset_after_pr),
        key=lambda p: p.number,
        reverse=True,
    )
    streak: list[int] = []
    for summary in closed:
        if summary.merged:
            break
        streak.append(summary.number)
    return streak


def history_findings(
    policy: LoopPolicy,
    item: BacklogItem,
    *,
    pull: PullRequest,
    history: Sequence[PullSummary],
    repository: str,
) -> list[Finding]:
    """Work in progress, completion, dependencies, attempts and the halt (R3).

    Only heads from this repository count, so a fork can neither block the
    loop's limits nor satisfy them.
    """
    implementation_prs = [
        p
        for p in history
        if p.number != pull.number
        and _same_repository(p.head_repo, repository)
        and p.head_ref.startswith(policy.implementation_prefix)
    ]
    findings: list[Finding] = []
    open_others = sorted(p.number for p in implementation_prs if p.is_open)
    if len(open_others) >= policy.max_open_implementation_prs:
        findings.append(
            Finding(
                "wip",
                f"implementation PR(s) {open_others} already open; the limit is "
                f"{policy.max_open_implementation_prs}",
            )
        )
    for_item = [
        p for p in implementation_prs if implementation_item_id(policy, p.head_ref) == item.item_id
    ]
    merged = sorted(p.number for p in for_item if p.merged)
    if merged:
        findings.append(Finding("done", f"{item.item_id} was already merged in {merged}"))
    if len(for_item) >= item.max_attempts:
        findings.append(
            Finding(
                "attempts",
                f"{item.item_id} has had {len(for_item)} earlier attempt(s); "
                f"budget.max_attempts is {item.max_attempts}",
            )
        )
    merged_items = {
        implementation_item_id(policy, p.head_ref) for p in implementation_prs if p.merged
    }
    findings.extend(
        Finding("depends-on", f"{item.item_id} depends on {dependency}, which is not merged")
        for dependency in item.depends_on
        if dependency not in merged_items
    )
    streak = failure_streak(policy, implementation_prs)
    if len(streak) >= policy.max_consecutive_failures:
        findings.append(
            Finding(
                "halt",
                f"the loop is halted after {len(streak)} closed, unmerged implementation PRs "
                f"{streak}; a human PR raising halt_reset_after_pr resumes it",
            )
        )
    return findings


def proposal_path_findings(policy: LoopPolicy, files: Sequence[ChangedFile]) -> list[Finding]:
    """Proposal branches only add records and touch the indexes (R5)."""
    findings: list[Finding] = []
    for changed in files:
        for path in changed.paths:
            denied = None if path_problem(path) else policy.denied_anywhere.first_match(path)
            if denied is not None:
                findings.append(
                    Finding(
                        "denied", "no governed branch may add or change this file", path, denied
                    )
                )
        if changed.status in {STATUS_REMOVED, STATUS_RENAMED}:
            findings.append(
                Finding(
                    "record",
                    f"proposal branches may not delete or rename a file ({changed.status})",
                    changed.previous_path or changed.path,
                )
            )
        elif changed.status in _NEW_FILE_STATUSES:
            if policy.proposal_new_paths.first_match(changed.path) is None:
                findings.append(
                    Finding(
                        "proposal-paths",
                        "new files must sit under proposal_new_paths",
                        changed.path,
                    )
                )
        elif changed.status in _MODIFY_STATUSES and (
            policy.proposal_index_files.first_match(changed.path) is None
        ):
            findings.append(
                Finding(
                    "record",
                    "proposal branches may modify only the index files; existing records "
                    "stay human edits",
                    changed.path,
                )
            )
    return findings


def _implementation_findings(
    policy: LoopPolicy,
    *,
    pull: PullRequest,
    files: Sequence[ChangedFile],
    context: CheckContext,
) -> list[Finding]:
    findings = enabled_findings(context.switches)
    item_id = implementation_item_id(policy, pull.head_ref)
    if item_id is None:
        findings.append(
            Finding(
                "item",
                f"head {_q(pull.head_ref)} names no item as "
                f"{policy.implementation_prefix}LOOP-NNNN-<attempt>",
            )
        )
        return findings
    try:
        item = context.load_item(item_id)
    except ItemError as exc:
        findings.append(Finding("item", str(exc)))
        return findings
    findings += implementation_path_findings(policy, item, files)
    findings += suppression_findings(files, allowed=item.allowed_suppressions)
    findings += diff_size_findings(pull, item)
    findings += stage_findings(policy, item, context.switches)
    findings += history_findings(
        policy, item, pull=pull, history=context.source.history(), repository=context.repository
    )
    return findings


@dataclass(frozen=True)
class CheckContext:
    """Everything :func:`check` reads besides the policy and the PR number."""

    repository: str
    source: EvidenceSource
    load_item: Callable[[str], BacklogItem]
    switches: Switches


def _unreadable_policy_verdict(pull: PullRequest, problem: str) -> Verdict:
    if pull.head_ref.startswith(GOVERNED_NAMESPACE):
        return Verdict(
            None,
            findings=(
                Finding(
                    "policy",
                    f"the loop policy is unreadable ({problem}); every {GOVERNED_NAMESPACE} "
                    "head fails until a human PR restores it",
                ),
            ),
        )
    return Verdict(
        None,
        warnings=(
            f"the loop policy is unreadable ({problem}); this head is outside "
            f"{GOVERNED_NAMESPACE}, so it passes",
        ),
    )


def check(
    policy: LoopPolicy | None, *, policy_problem: str, number: int, context: CheckContext
) -> Verdict:
    """Judge PR *number*. Ungoverned heads cost one API call; governed ones are read in full."""
    source = context.source
    pull = source.pull(number)
    if policy is None:
        return _unreadable_policy_verdict(pull, policy_problem)
    branch_class = classify(policy, head_ref=pull.head_ref, author=pull.author)
    if branch_class is BranchClass.UNGOVERNED:
        return Verdict(branch_class)
    findings = origin_findings(policy, pull, repository=context.repository)
    if branch_class is BranchClass.MISROUTED:
        findings.append(
            Finding(
                "identity",
                f"{_q(pull.author)} is a loop identity, but head {_q(pull.head_ref)} is outside "
                f"{policy.implementation_prefix} and {policy.proposal_prefix}",
            )
        )
        return Verdict(branch_class, tuple(findings))
    files = source.files(number)
    findings += listing_findings(pull, files, head_sha_after=source.pull(number).head_sha)
    findings += tree_findings(files, source.tree(pull.head_sha))
    if branch_class is BranchClass.PROPOSAL:
        findings += proposal_path_findings(policy, files)
    else:
        findings += _implementation_findings(policy, pull=pull, files=files, context=context)
    return Verdict(branch_class, tuple(findings))


# ── GitHub API ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]  # lower-cased names
    body: bytes


Transport = Callable[[str, Mapping[str, str]], HttpResponse]


def _lower_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {name.lower(): value for name, value in items}


def urllib_transport(url: str, headers: Mapping[str, str]) -> HttpResponse:
    """GET *url* with the stdlib. :class:`GitHubClient` admits only https API URLs."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310 -- https on the API origin only, enforced by GitHubClient
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 -- https on the API origin only, enforced by GitHubClient
            return HttpResponse(
                response.status, _lower_headers(response.headers.items()), response.read()
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, _lower_headers(exc.headers.items()), exc.read())
    except OSError as exc:
        raise GitHubError(f"GET {url} failed: {exc}") from exc


def next_link(header: str | None) -> str | None:
    """Return the ``rel="next"`` URL of a ``Link`` header, or ``None``."""
    if not header:
        return None
    match = _NEXT_LINK_RE.search(header)
    return match.group(1) if match else None


def _as_mapping(value: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise GitHubError(f"{what}: expected a JSON object")
    return value


def _str_field(data: Mapping[str, object], key: str, *, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise GitHubError(f"{what}: {key!r} is not a string")
    return value


def _optional_str_field(data: Mapping[str, object], key: str, *, what: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise GitHubError(f"{what}: {key!r} is not a string")
    return value


def _int_field(data: Mapping[str, object], key: str, *, what: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GitHubError(f"{what}: {key!r} is not an integer")
    return value


def _repo_name(side: Mapping[str, object], *, what: str) -> str | None:
    repo = side.get("repo")
    if repo is None:
        return None
    return _str_field(_as_mapping(repo, what=what), "full_name", what=what)


def parse_pull(value: object) -> PullRequest:
    data = _as_mapping(value, what="pull request")
    head = _as_mapping(data.get("head"), what="pull request head")
    base = _as_mapping(data.get("base"), what="pull request base")
    user = _as_mapping(data.get("user"), what="pull request user")
    base_repo = _repo_name(base, what="pull request base repo")
    if base_repo is None:
        raise GitHubError("pull request base repo is missing")
    return PullRequest(
        number=_int_field(data, "number", what="pull request"),
        head_ref=_str_field(head, "ref", what="pull request head"),
        head_sha=_str_field(head, "sha", what="pull request head"),
        head_repo=_repo_name(head, what="pull request head repo"),
        base_ref=_str_field(base, "ref", what="pull request base"),
        base_repo=base_repo,
        author=_str_field(user, "login", what="pull request user"),
        changed_files=_int_field(data, "changed_files", what="pull request"),
        additions=_int_field(data, "additions", what="pull request"),
        deletions=_int_field(data, "deletions", what="pull request"),
    )


def parse_summary(value: object) -> PullSummary:
    data = _as_mapping(value, what="pull request summary")
    head = _as_mapping(data.get("head"), what="pull request summary head")
    state = _str_field(data, "state", what="pull request summary")
    if state not in {"open", "closed"}:
        raise GitHubError(f"pull request summary: unknown state {_q(state)}")
    return PullSummary(
        number=_int_field(data, "number", what="pull request summary"),
        head_ref=_str_field(head, "ref", what="pull request summary head"),
        head_repo=_repo_name(head, what="pull request summary head repo"),
        is_open=state == "open",
        merged=_optional_str_field(data, "merged_at", what="pull request summary") is not None,
    )


def parse_changed_file(value: object) -> ChangedFile:
    data = _as_mapping(value, what="pull request file")
    return ChangedFile(
        path=_str_field(data, "filename", what="pull request file"),
        status=_str_field(data, "status", what="pull request file"),
        changes=_int_field(data, "changes", what="pull request file"),
        patch=_optional_str_field(data, "patch", what="pull request file"),
        previous_path=_optional_str_field(data, "previous_filename", what="pull request file"),
    )


class GitHubClient:
    """Read-only GitHub REST client; implements :class:`EvidenceSource`."""

    def __init__(
        self,
        *,
        api_url: str,
        repository: str,
        auth: str | None,
        transport: Transport = urllib_transport,
        max_pages: int = MAX_PAGES,
    ) -> None:
        parts = urllib.parse.urlsplit(api_url)
        if parts.scheme != "https" or not parts.netloc:
            raise ConfigError(f"{ENV_API_URL} must be an https URL, not {_q(api_url)}")
        if not _REPOSITORY_RE.fullmatch(repository):
            raise ConfigError(f"{ENV_REPOSITORY} must be owner/name, not {_q(repository)}")
        self._base = api_url.rstrip("/")
        self._origin = (parts.scheme, parts.netloc)
        self._repository = repository
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if auth:
            self._headers["Authorization"] = f"Bearer {auth}"
        self._transport = transport
        self._max_pages = max_pages

    def _get(self, url: str) -> HttpResponse:
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme, parts.netloc) != self._origin:
            raise GitHubError(f"refusing to follow {_q(url)} off the API origin")
        response = self._transport(url, self._headers)
        if response.status != HTTP_OK:
            raise GitHubError(f"GET {parts.path} returned HTTP {response.status}")
        return response

    @staticmethod
    def _decode(response: HttpResponse, *, what: str) -> object:
        try:
            decoded: object = json.loads(response.body)
        except ValueError as exc:
            raise GitHubError(f"{what}: the response is not JSON") from exc
        return decoded

    def _get_object(self, path: str) -> Mapping[str, object]:
        response = self._get(f"{self._base}{path}")
        return _as_mapping(self._decode(response, what=path), what=path)

    def _get_pages(self, path: str) -> list[object]:
        """Every page of a list endpoint; a listing longer than the cap fails."""
        url = f"{self._base}{path}"
        items: list[object] = []
        for _ in range(self._max_pages):
            response = self._get(url)
            page = self._decode(response, what=path)
            if not isinstance(page, list):
                raise GitHubError(f"{path}: expected a JSON array")
            items.extend(page)
            following = next_link(response.headers.get("link"))
            if following is None:
                return items
            url = following
        raise GitHubError(f"{path}: more than {self._max_pages} pages; refusing a partial listing")

    def pull(self, number: int) -> PullRequest:
        return parse_pull(self._get_object(f"/repos/{self._repository}/pulls/{number}"))

    def files(self, number: int) -> list[ChangedFile]:
        path = f"/repos/{self._repository}/pulls/{number}/files?per_page={PAGE_SIZE}"
        return [parse_changed_file(item) for item in self._get_pages(path)]

    def tree(self, sha: str) -> Mapping[str, str] | None:
        """``{path: mode}`` of the head tree, or ``None`` when GitHub truncated it."""
        if not _SHA_RE.fullmatch(sha):
            raise GitHubError(f"{_q(sha)} is not a commit SHA")
        data = self._get_object(f"/repos/{self._repository}/git/trees/{sha}?recursive=1")
        if data.get("truncated") is not False:
            return None
        entries = data.get("tree")
        if not isinstance(entries, list):
            raise GitHubError("git tree: 'tree' is not a list")
        modes: dict[str, str] = {}
        for entry in entries:
            fields = _as_mapping(entry, what="git tree entry")
            modes[_str_field(fields, "path", what="git tree entry")] = _str_field(
                fields, "mode", what="git tree entry"
            )
        return modes

    def history(self) -> list[PullSummary]:
        path = f"/repos/{self._repository}/pulls?state=all&per_page={PAGE_SIZE}"
        return [parse_summary(item) for item in self._get_pages(path)]


# ── Entry point ───────────────────────────────────────────────────────────────


def read_event_number(event_path: Path) -> int:
    """Return ``pull_request.number`` from the workflow's event payload."""
    try:
        event: object = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read the event payload {event_path}: {exc}") from exc
    pull = event.get("pull_request") if isinstance(event, dict) else None
    number = pull.get("number") if isinstance(pull, dict) else None
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ConfigError("the event has no pull_request.number; run this on pull_request_target")
    return number


def _require_env(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    if not value:
        raise ConfigError(f"{name} is not set; this check runs inside GitHub Actions")
    return value


def _one_line(text: str) -> str:
    return text.replace("\r", "\\r").replace("\n", "\\n")


def render_report(number: int, verdict: Verdict) -> list[str]:
    """Log lines; each starts with fixed text, so none can be a workflow command."""
    label = verdict.branch_class.value if verdict.branch_class else "policy unreadable"
    lines = [f"Loop scope check, PR #{number}: {label}"]
    lines.extend(f"  WARNING: {_one_line(warning)}" for warning in verdict.warnings)
    for finding in verdict.findings:
        where = f" {_q(finding.path)}" if finding.path is not None else ""
        glob = f" (matches {_q(finding.pattern)})" if finding.pattern is not None else ""
        lines.append(f"  FAIL [{finding.rule}]{where}{glob}: {_one_line(finding.message)}")
    if verdict.passed:
        lines.append("RESULT: pass")
    else:
        lines.append(f"RESULT: fail, {len(verdict.findings)} finding(s)")
    return lines


def render_summary(lines: Sequence[str]) -> str:
    """Markdown for ``GITHUB_STEP_SUMMARY``: the report inside an unbreakable fence."""
    body = "\n".join(lines)
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(body)), default=0)
    fence = "`" * max(_MIN_FENCE, longest + 1)
    return f"### Loop scope check\n\n{fence}text\n{body}\n{fence}\n"


def _write_summary(path: str, text: str) -> None:
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        print(f"WARNING: could not write the step summary: {_one_line(str(exc))}", file=sys.stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: Transport = urllib_transport,
) -> int:
    parser = argparse.ArgumentParser(
        description="Loop scope check (spec-0052). Runs on pull_request_target in CI.",
        allow_abbrev=False,
    )
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT_PATH)
    parser.add_argument("--backlog-dir", type=Path, default=DEFAULT_BACKLOG_DIR)
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    backlog_dir: Path = args.backlog_dir

    try:
        policy: LoopPolicy | None = load_loop_policy(args.pyproject)
        problem = ""
    except PolicyError as exc:
        policy, problem = None, str(exc)

    try:
        repository = _require_env(env, ENV_REPOSITORY)
        number = read_event_number(Path(_require_env(env, ENV_EVENT_PATH)))
        client = GitHubClient(
            api_url=env.get(ENV_API_URL) or DEFAULT_API_URL,
            repository=repository,
            auth=env.get(ENV_AUTH) or None,
            transport=transport,
        )
        context = CheckContext(
            repository=repository,
            source=client,
            load_item=lambda item_id: load_backlog_item(backlog_dir, item_id),
            switches=Switches(env.get(ENV_LOOP_ENABLED, ""), env.get(ENV_LOOP_STAGE, "")),
        )
        verdict = check(policy, policy_problem=problem, number=number, context=context)
    except (ConfigError, GitHubError) as exc:
        print(f"ERROR: the loop scope check could not run: {_one_line(str(exc))}", file=sys.stderr)
        return EXIT_ERROR

    lines = render_report(number, verdict)
    print("\n".join(lines))
    summary_path = env.get(ENV_STEP_SUMMARY)
    if summary_path:
        _write_summary(summary_path, render_summary(lines))
    return EXIT_OK if verdict.passed else EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())
