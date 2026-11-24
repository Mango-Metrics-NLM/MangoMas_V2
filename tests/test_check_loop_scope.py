"""Tests for ``scripts/check_loop_scope.py`` (ADR-0042 / spec-0052).

Every spec-0052 scenario is pinned in both directions: the rule fires on the
violation and stays quiet on the legitimate change next to it. The rule
functions are pure, so most tests hand them evidence directly; ``check`` runs
over ``FakeSource``; ``main`` runs end to end over ``FakeTransport``. Nothing
here touches the network.

The shipped policy is loaded from the repository's real ``pyproject.toml``, so
``test_shipped_policy_denies_every_trusted_base_row`` fails when a trusted-base
glob is narrowed (``MUT-LOOP-01``). Four more guard-probe entries
(``MUT-LOOP-02``..``05``) name tests in this file.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests._script_loader import load_script_module

scope = load_script_module("check_loop_scope.py")

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_SHIPPED_POLICY = scope.load_loop_policy(_REPO_ROOT / "pyproject.toml")

_REPOSITORY: Final[str] = "owner/repo"
_FORK: Final[str] = "someone/repo"
_API: Final[str] = "https://api.example.test"
_AUTH_VALUE: Final[str] = "test-auth-value"
_HEAD_SHA: Final[str] = "a" * 40
_NEW_SHA: Final[str] = "b" * 40
_NUMBER: Final[int] = 7
_ITEM_ID: Final[str] = "LOOP-0007"
_OTHER_ITEM: Final[str] = "LOOP-0003"
_IMPL_HEAD: Final[str] = f"claude/loop-{_ITEM_ID}-1"
_OTHER_IMPL_HEAD: Final[str] = f"claude/loop-{_OTHER_ITEM}-1"
_PROPOSAL_HEAD: Final[str] = "claude/proposal-req-42"
_ALLOWED_GLOB: Final[str] = "src/mangomas/adapters/llm/vertex/**"
_ALLOWED_FILE: Final[str] = "src/mangomas/adapters/llm/vertex/_helpers.py"
_LOOP_IDENTITY: Final[str] = "mango-loop[bot]"
_REGULAR_MODE: Final[str] = "100644"
_PLAIN_PATCH: Final[str] = "@@ -1 +1 @@\n-old = 1\n+new = 1"
_AUTO: Final = object()

IMPLEMENTATION = scope.BranchClass.IMPLEMENTATION
PROPOSAL = scope.BranchClass.PROPOSAL
UNGOVERNED = scope.BranchClass.UNGOVERNED
MISROUTED = scope.BranchClass.MISROUTED


# ── Builders and fakes ────────────────────────────────────────────────────────


def _pull(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "number": _NUMBER,
        "head_ref": _IMPL_HEAD,
        "head_sha": _HEAD_SHA,
        "head_repo": _REPOSITORY,
        "base_ref": "dev",
        "base_repo": _REPOSITORY,
        "author": "maintainer",
        "changed_files": 1,
        "additions": 1,
        "deletions": 1,
    }
    fields.update(overrides)
    return scope.PullRequest(**fields)


def _file(
    path: str = _ALLOWED_FILE,
    *,
    status: str = "modified",
    patch: str | None = _PLAIN_PATCH,
    changes: int = 2,
    previous: str | None = None,
) -> Any:
    return scope.ChangedFile(
        path=path, status=status, changes=changes, patch=patch, previous_path=previous
    )


def _summary(
    number: int,
    head_ref: str,
    *,
    is_open: bool = False,
    merged: bool = False,
    head_repo: str | None = _REPOSITORY,
) -> Any:
    return scope.PullSummary(
        number=number, head_ref=head_ref, head_repo=head_repo, is_open=is_open, merged=merged
    )


def _item(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "item_id": _ITEM_ID,
        "allowed_paths": scope.GlobSet.of([_ALLOWED_GLOB], case_sensitive=True),
        "allowed_suppressions": frozenset(),
        "max_diff_lines": scope.DEFAULT_MAX_DIFF_LINES,
        "depends_on": (),
        "min_stage": "shadow",
        "max_attempts": 3,
    }
    fields.update(overrides)
    return scope.BacklogItem(**fields)


def _patch_adding(line: str) -> str:
    return f"@@ -1 +1,2 @@\n context = 0\n+{line}"


@dataclass
class FakeSource:
    """An :class:`EvidenceSource` over canned data that records every call."""

    pulls: list[Any]
    changed: list[Any] = field(default_factory=list)
    head_tree: Mapping[str, str] | None = None
    prs: list[Any] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def pull(self, number: int) -> Any:
        assert number == _NUMBER
        index = min(self.calls.count("pull"), len(self.pulls) - 1)
        self.calls.append("pull")
        return self.pulls[index]

    def files(self, number: int) -> list[Any]:
        assert number == _NUMBER
        self.calls.append("files")
        return list(self.changed)

    def tree(self, _sha: str) -> Mapping[str, str] | None:
        self.calls.append("tree")
        return self.head_tree

    def history(self) -> list[Any]:
        self.calls.append("history")
        return list(self.prs)


def _context(
    source: FakeSource,
    *,
    items: Mapping[str, Any] | None = None,
    enabled: str = "true",
    stage: str = "",
) -> Any:
    catalogue = {_ITEM_ID: _item()} if items is None else dict(items)

    def load_item(item_id: str) -> Any:
        if item_id not in catalogue:
            raise scope.ItemError(f"no approved backlog item {item_id} on the default branch")
        return catalogue[item_id]

    return scope.CheckContext(
        repository=_REPOSITORY,
        source=source,
        load_item=load_item,
        switches=scope.Switches(enabled, stage),
    )


def _run(
    policy: Any = _SHIPPED_POLICY,
    *,
    pull_overrides: Mapping[str, Any] | None = None,
    files: Sequence[Any] | None = None,
    history: Sequence[Any] = (),
    items: Mapping[str, Any] | None = None,
    enabled: str = "true",
    stage: str = "",
    tree: Any = _AUTO,
    pull_after: Any = None,
) -> Any:
    changed = [_file()] if files is None else list(files)
    pull = _pull(**{"changed_files": len(changed), **(pull_overrides or {})})
    head_tree = (
        {f.path: _REGULAR_MODE for f in changed if f.status != "removed"} if tree is _AUTO else tree
    )
    source = FakeSource(
        pulls=[pull] if pull_after is None else [pull, pull_after],
        changed=changed,
        head_tree=head_tree,
        prs=list(history),
    )
    context = _context(source, items=items, enabled=enabled, stage=stage)
    return scope.check(policy, policy_problem="", number=_NUMBER, context=context)


def _rules(verdict: Any) -> list[str]:
    return [finding.rule for finding in verdict.findings]


def _proposal(
    files: Sequence[Any], *, pull_overrides: Mapping[str, Any] | None = None, **kwargs: Any
) -> Any:
    overrides = {"head_ref": _PROPOSAL_HEAD, **(pull_overrides or {})}
    return _run(pull_overrides=overrides, files=files, **kwargs)


# ── Globs ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/conftest.py", "conftest.py", True),
        ("**/conftest.py", "tests/a/conftest.py", True),
        ("**/conftest.py", "tests/a/my_conftest.py", False),
        ("scripts/**", "scripts/check.py", True),
        ("scripts/**", "scripts/a/b.py", True),
        ("scripts/**", "scripts", False),
        ("scripts/**", "scriptsx/a.py", False),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/a/b.py", False),
        ("Dockerfile*", "Dockerfile.oracle", True),
        ("requirements*.txt", "requirements-dev.txt", True),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/y/b", True),
        ("file?.md", "file1.md", True),
        ("file?.md", "file10.md", False),
        ("**/.claude/**", ".claude/settings.json", True),
        ("**/.claude/**", "docs/.claude/notes.md", True),
        ("docs/a+b.md", "docs/a+b.md", True),
        ("docs/a+b.md", "docs/aab.md", False),
    ],
)
def test_glob_semantics(pattern: str, path: str, expected: bool) -> None:
    compiled = scope.compile_glob(pattern, case_sensitive=True)
    assert bool(compiled.fullmatch(path)) is expected


def test_deny_globs_fold_case_and_allow_globs_do_not() -> None:
    deny = scope.GlobSet.of(["src/mangomas/core/**"], case_sensitive=False)
    allow = scope.GlobSet.of(["src/mangomas/core/**"], case_sensitive=True)
    assert deny.first_match("SRC/Mangomas/CORE/agent.py") == "src/mangomas/core/**"
    assert allow.first_match("SRC/Mangomas/CORE/agent.py") is None


@pytest.mark.parametrize(
    "pattern", ["", "/abs/**", "a\\b", "a//b", "a/./b", "a/../b", "a/", "a**/b"]
)
def test_malformed_globs_are_rejected(pattern: str) -> None:
    with pytest.raises(scope.PolicyError):
        scope.compile_glob(pattern, case_sensitive=True)


_SEGMENT = st.text(alphabet="abcXYZ019._-", min_size=1, max_size=8).filter(
    lambda s: s not in {".", ".."}
)
_REST = st.lists(_SEGMENT, min_size=1, max_size=4).map("/".join)


@given(_SEGMENT, _REST)
def test_a_single_star_never_crosses_a_separator(directory: str, rest: str) -> None:
    compiled = scope.compile_glob(f"{directory}/*", case_sensitive=True)
    assert bool(compiled.fullmatch(f"{directory}/{rest}")) is ("/" not in rest)


@given(st.lists(_SEGMENT, max_size=4), _SEGMENT)
def test_a_leading_double_star_matches_at_any_depth(parents: list[str], name: str) -> None:
    compiled = scope.compile_glob(f"**/{name}", case_sensitive=True)
    assert compiled.fullmatch("/".join([*parents, name]))


@given(_SEGMENT, _SEGMENT, _REST)
def test_a_trailing_double_star_never_matches_a_sibling(
    directory: str, suffix: str, rest: str
) -> None:
    compiled = scope.compile_glob(f"{directory}/**", case_sensitive=True)
    assert compiled.fullmatch(f"{directory}/{rest}")
    assert not compiled.fullmatch(f"{directory}{suffix}/{rest}")
    assert not compiled.fullmatch(directory)


@pytest.mark.parametrize(
    "path", ["", "/etc/passwd", "a\\b.py", "a/../b", "a//b", "./a", "a\nb", "a\x00b"]
)
def test_path_problem_flags_paths_that_are_not_plain(path: str) -> None:
    assert scope.path_problem(path) is not None


def test_path_problem_accepts_a_plain_path() -> None:
    assert scope.path_problem(_ALLOWED_FILE) is None


# ── Policy ────────────────────────────────────────────────────────────────────

_DROP: Final = object()


def _policy_toml(**overrides: object) -> str:
    loop: dict[str, object] = {
        "implementation_prefix": "claude/loop-",
        "proposal_prefix": "claude/proposal-",
        "base_branch": "dev",
        "loop_identities": [],
        "stages": ["shadow", "l0"],
        "max_open_implementation_prs": 1,
        "max_consecutive_failures": 2,
        "halt_reset_after_pr": 0,
        "denied_anywhere": ["**/conftest.py"],
        "trusted_base": ["tests/**"],
        "work_area": ["src/**"],
        "proposal_new_paths": ["specs/**"],
        "proposal_index_files": ["specs/README.md"],
    }
    loop.update(overrides)
    lines = [
        "[tool.mangomas.governance]",
        'protected_paths = ["src\\\\core.py"]',
        'breaking_change_marker_aliases = ["BREAKING-CHANGE"]',
        "",
        "[tool.mangomas.governance.loop]",
    ]
    lines += [f"{key} = {json.dumps(value)}" for key, value in loop.items() if value is not _DROP]
    return "\n".join(lines) + "\n"


def test_a_valid_policy_parses_and_protects_the_protected_paths() -> None:
    policy = scope.parse_loop_policy(_policy_toml(loop_identities=["Mango-Loop[bot]"]), source="t")
    # The Windows-style protected path is normalised before it joins the deny list.
    assert policy.deny_match("src/core.py") == "src/core.py"
    assert policy.deny_match("tests/test_a.py") == "tests/**"
    assert policy.loop_identities == frozenset({"mango-loop[bot]"})
    assert policy.stages == ("shadow", "l0")


@pytest.mark.parametrize(
    "overrides",
    [
        {"surprise": 1},
        {"work_area": _DROP},
        {"work_area": []},
        {"stages": "shadow"},
        {"trusted_base": [1]},
        {"trusted_base": [""]},
        {"max_open_implementation_prs": True},
        {"max_consecutive_failures": 0},
        {"halt_reset_after_pr": -1},
        {"base_branch": ""},
        {"implementation_prefix": "loop-"},
        {"proposal_prefix": "claude/"},
        {"proposal_prefix": "claude/loop-proposal-"},
        {"stages": ["shadow", "shadow"]},
        {"work_area": ["src//x"]},
        {"loop_identities": "bot"},
    ],
    ids=",".join,
)
def test_a_malformed_policy_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(scope.PolicyError):
        scope.parse_loop_policy(_policy_toml(**overrides), source="t")


@pytest.mark.parametrize(
    "toml_text",
    [
        "[tool.mangomas.governance]\nprotected_paths = ['a']\n"
        "breaking_change_marker_aliases = ['B']\n",
        "not = [valid toml",
        "[tool]\nmangomas = 3\n",
        "[tool.mangomas.governance]\nloop = 3\n",
        _policy_toml().replace('protected_paths = ["src\\\\core.py"]', "protected_paths = []"),
    ],
    ids=["no-loop-table", "bad-toml", "tool-not-a-table", "loop-not-a-table", "no-protected-paths"],
)
def test_an_incomplete_policy_document_is_rejected(toml_text: str) -> None:
    with pytest.raises(scope.PolicyError):
        scope.parse_loop_policy(toml_text, source="t")


def test_an_unreadable_policy_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(scope.PolicyError):
        scope.load_loop_policy(tmp_path / "missing.toml")


# One representative path per row of spec-0052's trusted-base table, plus the
# any-depth list and the protected paths. MUT-LOOP-01 narrows `.github/**`.
_SHIPPED_DENIED: Final[tuple[str, ...]] = (
    ".github/workflows/deploy.yml",
    ".github/dependabot.yml",
    "Makefile",
    "GNUmakefile",
    "requirements-dev.txt",
    "uv.lock",
    ".pre-commit-config.yaml",
    "typings/stub.txt",
    "scripts/check_coverage.py",
    "tests/test_example.py",
    "examples/demo.py",
    "evals/datasets/selfcheck_echo_v1.jsonl",
    "eval_harness_bridge/src/bridge.py",
    "src/mangomas/eval/runner.py",
    "src/mangomas/cli/commands/eval.py",
    "src/mangomas/cli/exit_codes.py",
    "src/mangomas/config/evaluation.py",
    "src/mangomas/harness/tool_policy.py",
    "src/mangomas/config/repair.py",
    "src/mangomas/composition/harness.py",
    "src/mangomas/core/loop.py",
    "src/mangomas/errors.py",
    "src/mangomas/registry.py",
    "deploy/environments.yaml",
    "Dockerfile",
    ".gitleaks.toml",
    ".env.example",
    "mango-integration-contracts/src/contract.py",
    "backlog/LOOP-0001.toml",
    "specs/0099-example.md",
    "docs/adr/0099-example.md",
    "docs/plans/example.md",
    "docs/analysis/example.md",
    "docs/proposals/req-1/proposal.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "NEXT_STEPS.md",
    ".claude/settings.json",
    "src/mangomas/agents/CLAUDE.md",
    "AGENTS.md",
    "src/mangomas/adapters/ruff.toml",
    "src/mangomas/conftest.py",
    "src/mangomas/adapters/stub.pyi",
    "src/mangomas/sitecustomize.py",
    "src/mangomas/hook.pth",
    "docs/.gitattributes",
    "pyproject.toml",
    ".mcp.json",
    "scripts/check_loop_scope.py",
    ".github/workflows/loop-scope.yml",
)


@pytest.mark.parametrize("path", _SHIPPED_DENIED)
def test_shipped_policy_denies_every_trusted_base_row(path: str) -> None:
    assert _SHIPPED_POLICY.deny_match(path) is not None, path


@pytest.mark.parametrize(
    "path",
    [
        "src/mangomas/agents/chat.py",
        _ALLOWED_FILE,
        "docs/workflow/graphs.md",
        "README.md",
        "CHANGELOG.md",
    ],
)
def test_shipped_policy_leaves_the_work_area_open(path: str) -> None:
    assert _SHIPPED_POLICY.deny_match(path) is None
    assert _SHIPPED_POLICY.work_area.first_match(path) is not None


def test_shipped_policy_matches_the_spec() -> None:
    assert _SHIPPED_POLICY.implementation_prefix == "claude/loop-"
    assert _SHIPPED_POLICY.proposal_prefix == "claude/proposal-"
    assert _SHIPPED_POLICY.base_branch == "dev"
    assert _SHIPPED_POLICY.stages == ("shadow", "l0", "l1", "l2")
    assert _SHIPPED_POLICY.max_open_implementation_prs == 1
    assert _SHIPPED_POLICY.loop_identities == frozenset()
    # Contract tests may not ride a proposal until M2 isolates them (spec-0054 R4).
    assert _SHIPPED_POLICY.proposal_new_paths.first_match("tests/contract/loop_0007/t.py") is None


# ── Classification ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("head", "author", "expected"),
    [
        (_IMPL_HEAD, "maintainer", IMPLEMENTATION),
        (_PROPOSAL_HEAD, "maintainer", PROPOSAL),
        ("claude/modest-heisenberg-6gengs", "maintainer", UNGOVERNED),
        ("feature/new-thing", "maintainer", UNGOVERNED),
        ("dependabot/pip/ruff-0.16", "dependabot[bot]", UNGOVERNED),
        ("claude/other", _LOOP_IDENTITY, MISROUTED),
        ("claude/other", _LOOP_IDENTITY.upper(), MISROUTED),
        (_IMPL_HEAD, _LOOP_IDENTITY, IMPLEMENTATION),
    ],
)
def test_classification(head: str, author: str, expected: Any) -> None:
    policy = replace(_SHIPPED_POLICY, loop_identities=frozenset({_LOOP_IDENTITY}))
    assert scope.classify(policy, head_ref=head, author=author) is expected


def test_a_loop_identity_on_an_ungoverned_head_fails_and_a_person_passes() -> None:
    policy = replace(_SHIPPED_POLICY, loop_identities=frozenset({_LOOP_IDENTITY}))
    loop = _run(policy, pull_overrides={"head_ref": "claude/other", "author": _LOOP_IDENTITY})
    assert loop.branch_class is MISROUTED
    assert "identity" in _rules(loop)
    person = _run(policy, pull_overrides={"head_ref": "claude/other", "author": "maintainer"})
    assert person.branch_class is UNGOVERNED
    assert person.passed


def test_an_ungoverned_head_costs_one_api_call() -> None:
    source = FakeSource(pulls=[_pull(head_ref="feature/new-thing")])
    verdict = scope.check(
        _SHIPPED_POLICY, policy_problem="", number=_NUMBER, context=_context(source)
    )
    assert verdict.passed
    assert source.calls == ["pull"]


def test_a_missing_policy_fails_the_governed_namespace() -> None:
    for head in (_IMPL_HEAD, _PROPOSAL_HEAD, "claude/modest-heisenberg-6gengs"):
        source = FakeSource(pulls=[_pull(head_ref=head)])
        verdict = scope.check(
            None, policy_problem="missing", number=_NUMBER, context=_context(source)
        )
        assert verdict.branch_class is None
        assert _rules(verdict) == ["policy"], head
        assert source.calls == ["pull"]


def test_a_missing_policy_lets_other_heads_pass_with_a_warning() -> None:
    source = FakeSource(pulls=[_pull(head_ref="feature/restore-policy")])
    verdict = scope.check(None, policy_problem="missing", number=_NUMBER, context=_context(source))
    assert verdict.passed
    assert verdict.warnings


@pytest.mark.parametrize(
    "head_ref", ["claude/loop-seven", f"claude/loop-{_ITEM_ID}", f"claude/loop-{_ITEM_ID}-0"]
)
def test_an_implementation_head_that_names_no_item_fails(head_ref: str) -> None:
    assert scope.implementation_item_id(_SHIPPED_POLICY, head_ref) is None
    assert _rules(_run(pull_overrides={"head_ref": head_ref})) == ["item"]


def test_implementation_item_id_reads_the_item_from_the_head() -> None:
    assert scope.implementation_item_id(_SHIPPED_POLICY, f"claude/loop-{_ITEM_ID}-12") == _ITEM_ID
    assert scope.implementation_item_id(_SHIPPED_POLICY, _PROPOSAL_HEAD) is None


# ── Origin and listing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [{"head_repo": _FORK}, {"head_repo": None}, {"base_ref": "qa"}, {"base_repo": _FORK}],
    ids=["fork", "deleted-fork", "targets-qa", "foreign-base"],
)
def test_a_governed_head_from_a_fork_or_to_another_base_fails(overrides: dict[str, Any]) -> None:
    assert "origin" in _rules(_run(pull_overrides=overrides))
    proposal = _proposal([_file("specs/0099-example.md", status="added")], pull_overrides=overrides)
    assert "origin" in _rules(proposal)


def test_a_governed_head_from_this_repository_to_dev_passes_the_origin_rule() -> None:
    assert scope.origin_findings(_SHIPPED_POLICY, _pull(), repository=_REPOSITORY) == []
    assert scope.origin_findings(_SHIPPED_POLICY, _pull(), repository=_REPOSITORY.upper()) == []


def test_a_truncated_listing_fails() -> None:
    verdict = _run(pull_overrides={"changed_files": 2})
    assert _rules(verdict) == ["listing"]


def test_a_complete_listing_passes() -> None:
    assert _run().passed


def test_a_listing_beyond_githubs_cap_fails() -> None:
    count = scope.GITHUB_FILE_LISTING_CAP + 1
    findings = scope.listing_findings(
        _pull(changed_files=count), [_file()] * count, head_sha_after=_HEAD_SHA
    )
    assert [f.rule for f in findings] == ["listing"]


def test_a_head_that_moved_during_the_check_fails() -> None:
    verdict = _run(pull_after=_pull(head_sha=_NEW_SHA))
    assert _rules(verdict) == ["listing"]


def test_an_unknown_file_status_fails() -> None:
    assert "listing" in _rules(_run(files=[_file(status="mystery")]))


@pytest.mark.parametrize("path", ["src\\mangomas\\x.py", "src/mangomas/a\nb.py", "a/../b.py"])
def test_a_path_that_is_not_plain_fails(path: str) -> None:
    verdict = _run(files=[_file(path)])
    assert "path" in _rules(verdict)
    proposal = _proposal([_file(path, status="added")])
    assert "path" in _rules(proposal)


# ── Tree: no links ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("mode", "kind"), [("120000", "symlink"), ("160000", "submodule")])
def test_a_governed_branch_may_not_add_a_link(mode: str, kind: str) -> None:
    verdict = _run(tree={_ALLOWED_FILE: mode})
    assert [(f.rule, kind in f.message) for f in verdict.findings] == [("tree", True)]


def test_a_regular_file_passes_the_tree_rule() -> None:
    assert scope.tree_findings([_file()], {_ALLOWED_FILE: _REGULAR_MODE}) == []


def test_a_truncated_tree_fails() -> None:
    assert _rules(_run(tree=None)) == ["tree"]


def test_a_path_missing_from_the_tree_fails() -> None:
    assert [f.rule for f in scope.tree_findings([_file()], {})] == ["tree"]


def test_a_removed_file_is_not_looked_up_in_the_tree() -> None:
    assert scope.tree_findings([_file(status="removed")], {}) == []


# ── Implementation paths ──────────────────────────────────────────────────────


def test_an_implementation_changing_the_makefile_fails_and_names_the_glob() -> None:
    verdict = _run(files=[_file("Makefile")])
    assert [(f.rule, f.path, f.pattern) for f in verdict.findings] == [
        ("trusted-base", "Makefile", "Makefile")
    ]


@pytest.mark.parametrize(
    ("path", "glob"),
    [
        ("src/mangomas/adapters/ruff.toml", "**/ruff.toml"),
        ("src/mangomas/adapters/llm/vertex/stub.pyi", "**/*.pyi"),
        ("src/mangomas/adapters/llm/vertex/conftest.py", "**/conftest.py"),
        ("src/mangomas/CORE/agent.py", "src/mangomas/core/**"),
    ],
)
def test_an_implementation_adding_a_nested_config_or_stub_fails(path: str, glob: str) -> None:
    verdict = _run(files=[_file(path, status="added")])
    assert [(f.rule, f.pattern) for f in verdict.findings] == [("trusted-base", glob)]


def test_an_implementation_inside_its_allowed_paths_passes() -> None:
    verdict = _run()
    assert verdict.passed
    assert verdict.branch_class is IMPLEMENTATION


def test_an_implementation_editing_the_loop_workflow_fails() -> None:
    verdict = _run(files=[_file(".github/workflows/loop-scope.yml")])
    assert _rules(verdict) == ["trusted-base"]


def test_renaming_the_makefile_into_the_work_area_fails() -> None:
    renamed = _file(
        "docs/Makefile.txt", status="renamed", previous="Makefile", changes=0, patch=None
    )
    verdict = _run(files=[renamed])
    assert ("trusted-base", "Makefile") in [(f.rule, f.path) for f in verdict.findings]


def test_deleting_a_trusted_file_fails() -> None:
    removed = _file("tests/test_example.py", status="removed", patch="@@ -1 +0,0 @@\n-x = 1")
    assert _rules(_run(files=[removed])) == ["trusted-base"]


def test_a_path_outside_the_work_area_fails() -> None:
    assert _rules(_run(files=[_file(".vscode/settings.json", status="added")])) == ["work-area"]


def test_a_path_outside_the_items_allowed_paths_fails() -> None:
    assert _rules(_run(files=[_file("src/mangomas/agents/chat.py")])) == ["allowed-paths"]


# ── Suppressions ──────────────────────────────────────────────────────────────


def test_an_unallowed_type_ignore_fails() -> None:
    patch = _patch_adding("value = load()  # type: ignore[attr-defined]")
    assert _rules(_run(files=[_file(patch=patch)])) == ["suppression"]


def test_an_allowed_type_ignore_passes() -> None:
    patch = _patch_adding("value = load()  # type: ignore[attr-defined]")
    item = _item(allowed_suppressions=frozenset({"type-ignore"}))
    assert _run(files=[_file(patch=patch)], items={_ITEM_ID: item}).passed


@pytest.mark.parametrize(
    ("name", "line"),
    [
        ("pragma-no-cover", "if x:  # pragma: no cover"),
        ("pragma-no-cover", "if x:  # PRAGMA NO COVER"),
        ("pragma-no-branch", "for x in y:  # pragma: no branch"),
        ("noqa", "import os  # noqa: F401"),
        ("ruff-noqa", "# ruff: noqa: E501"),
        ("type-ignore", "y = 1  # type:ignore"),
        ("mypy-directive", "# mypy: ignore-errors"),
        ("pyright-directive", "# pyright: basic"),
        ("fmt-directive", "# fmt: off"),
        ("fmt-directive", "x = 1  # fmt: skip"),
        ("pytest-skip", "pytest.skip('later')"),
        ("pytest-skip", "pytest.importorskip('numpy')"),
        ("pytest-skip", "pytest.xfail('flaky')"),
        ("skip-mark", "@pytest.mark.skipif(True, reason='x')"),
        ("skip-mark", "pytestmark = pytest.mark.xfail"),
        ("unittest-skip", "@unittest.skip('x')"),
        ("unittest-skip", "@skipIf(True, 'x')"),
        ("unittest-skip", "self.skipTest('x')"),
    ],
)
def test_each_suppression_is_recognised_unless_allowed(name: str, line: str) -> None:
    files = [_file(patch=_patch_adding(line))]
    findings = scope.suppression_findings(files, allowed=frozenset())
    assert any(f"a {name} suppression" in f.message for f in findings), findings
    everything = frozenset(scope.SUPPRESSION_PATTERNS)
    assert scope.suppression_findings(files, allowed=everything) == []


@pytest.mark.parametrize(
    "line",
    [
        "value = 1  # a comment about types",
        "skip_count = 0",
        "def mark_done() -> None:",
        "# the pragma we avoid",
        "text = 'no cover letter'",
    ],
)
def test_ordinary_lines_are_not_suppressions(line: str) -> None:
    files = [_file(patch=_patch_adding(line))]
    assert scope.suppression_findings(files, allowed=frozenset()) == []


def test_a_removed_suppression_does_not_count() -> None:
    patch = "@@ -1,2 +1 @@\n-value = 1  # type: ignore\n+value = 1"
    assert scope.suppression_findings([_file(patch=patch)], allowed=frozenset()) == []


def test_a_suppression_is_reported_once_per_file() -> None:
    patch = "@@ -1 +1,3 @@\n context = 0\n+import os  # noqa: F401\n+import re  # noqa: F401"
    assert len(scope.suppression_findings([_file(patch=patch)], allowed=frozenset())) == 1


def test_a_missing_patch_fails_and_a_pure_rename_needs_none() -> None:
    missing = scope.suppression_findings([_file(patch=None, changes=3)], allowed=frozenset())
    assert [f.rule for f in missing] == ["patch"]
    renamed = _file(status="renamed", previous="src/mangomas/old.py", patch=None, changes=0)
    assert scope.suppression_findings([renamed], allowed=frozenset()) == []


# ── Size, switches and stage ──────────────────────────────────────────────────


def test_a_diff_over_max_diff_lines_fails() -> None:
    verdict = _run(pull_overrides={"additions": 250, "deletions": 51})
    assert _rules(verdict) == ["diff-size"]


def test_a_diff_at_max_diff_lines_passes() -> None:
    assert _run(pull_overrides={"additions": 250, "deletions": 50}).passed


@pytest.mark.parametrize("value", ["", "false", "TRUE", " true"])
def test_an_implementation_fails_unless_loop_enabled_is_exactly_true(value: str) -> None:
    assert _rules(_run(enabled=value)) == ["switch"]


def test_proposals_ignore_loop_enabled() -> None:
    assert _proposal([_file("specs/0099-example.md", status="added")], enabled="").passed


def test_an_item_above_the_loop_stage_fails() -> None:
    items = {_ITEM_ID: _item(min_stage="l1")}
    assert _rules(_run(items=items)) == ["stage"]
    assert _rules(_run(items=items, stage="l0")) == ["stage"]


def test_an_item_at_or_below_the_loop_stage_passes() -> None:
    items = {_ITEM_ID: _item(min_stage="l0")}
    assert _run(items=items, stage="l0").passed
    assert _run(items=items, stage="l1").passed


def test_an_unknown_stage_fails() -> None:
    assert _rules(_run(stage="beta")) == ["stage"]
    assert _rules(_run(items={_ITEM_ID: _item(min_stage="l9")})) == ["stage"]


# ── History: work in progress, completion, dependencies, attempts, halt ──────


def test_a_second_open_implementation_pr_fails() -> None:
    verdict = _run(history=[_summary(5, _OTHER_IMPL_HEAD, is_open=True)])
    assert _rules(verdict) == ["wip"]


def test_no_other_open_implementation_pr_passes() -> None:
    history = [
        _summary(5, _OTHER_IMPL_HEAD, merged=True),
        _summary(6, "feature/new-thing", is_open=True),
        _summary(_NUMBER, _IMPL_HEAD, is_open=True),
    ]
    assert _run(history=history).passed


def test_pull_requests_from_forks_neither_block_nor_satisfy_the_rules() -> None:
    fork_open = _summary(5, _OTHER_IMPL_HEAD, is_open=True, head_repo=_FORK)
    fork_merged = _summary(4, f"claude/loop-{_ITEM_ID}-1", merged=True, head_repo=_FORK)
    assert _run(history=[fork_open, fork_merged]).passed
    depends = {_ITEM_ID: _item(depends_on=(_OTHER_ITEM,))}
    fork_dependency = _summary(3, _OTHER_IMPL_HEAD, merged=True, head_repo=None)
    assert _rules(_run(items=depends, history=[fork_dependency])) == ["depends-on"]


def test_an_item_with_a_merged_implementation_pr_fails() -> None:
    verdict = _run(history=[_summary(3, f"claude/loop-{_ITEM_ID}-1", merged=True)])
    assert _rules(verdict) == ["done"]


def test_an_unmerged_dependency_fails_and_a_merged_one_passes() -> None:
    items = {_ITEM_ID: _item(depends_on=(_OTHER_ITEM,))}
    assert _rules(_run(items=items)) == ["depends-on"]
    assert _run(items=items, history=[_summary(2, _OTHER_IMPL_HEAD, merged=True)]).passed


def test_exhausted_attempts_fail_and_remaining_attempts_pass() -> None:
    items = {_ITEM_ID: _item(max_attempts=2)}
    one = [_summary(3, f"claude/loop-{_ITEM_ID}-1")]
    assert _run(items=items, history=one).passed
    two = [*one, _summary(4, f"claude/loop-{_ITEM_ID}-2")]
    assert _rules(_run(items=items, history=two)) == ["attempts"]


def test_consecutive_failures_halt_the_loop() -> None:
    failures = [_summary(n, f"claude/loop-LOOP-000{n}-1") for n in (4, 5, 6)]
    assert _rules(_run(history=failures)) == ["halt"]


def test_a_merge_breaks_the_failure_streak() -> None:
    history = [
        *[_summary(n, f"claude/loop-LOOP-000{n}-1") for n in (2, 3, 4)],
        _summary(5, "claude/loop-LOOP-0005-1", merged=True),
        _summary(6, "claude/loop-LOOP-0006-1"),
    ]
    assert scope.failure_streak(_SHIPPED_POLICY, history) == [6]
    assert _run(history=history).passed


def test_raising_halt_reset_after_pr_lifts_the_halt() -> None:
    failures = [_summary(n, f"claude/loop-LOOP-000{n}-1") for n in (4, 5, 6)]
    policy = replace(_SHIPPED_POLICY, halt_reset_after_pr=6)
    assert _run(policy, history=failures).passed


# ── Backlog items ─────────────────────────────────────────────────────────────


def _item_toml(**overrides: object) -> str:
    fields: dict[str, object] = {
        "id": _ITEM_ID,
        "title": "Extract helpers",
        "allowed_paths": [_ALLOWED_GLOB],
        "min_stage": "shadow",
    }
    fields.update(overrides)
    lines = [f"{key} = {json.dumps(value)}" for key, value in fields.items() if value is not _DROP]
    return "\n".join([*lines, "", "[budget]", "max_attempts = 2", "max_wall_minutes = 30"]) + "\n"


def test_an_unknown_item_fails() -> None:
    verdict = _run(items={})
    assert _rules(verdict) == ["item"]
    assert _ITEM_ID in verdict.findings[0].message


def test_parse_backlog_item_reads_the_enforced_fields_and_defaults() -> None:
    item = scope.parse_backlog_item(_item_toml(), item_id=_ITEM_ID, source="t")
    assert item.max_diff_lines == scope.DEFAULT_MAX_DIFF_LINES
    assert item.allowed_suppressions == frozenset()
    assert item.depends_on == ()
    assert item.max_attempts == 2
    assert item.allowed_paths.first_match(_ALLOWED_FILE) == _ALLOWED_GLOB


@pytest.mark.parametrize(
    "text",
    [
        "id = [",
        _item_toml(id="LOOP-0008"),
        _item_toml(allowed_paths=_DROP),
        _item_toml(allowed_paths=[]),
        _item_toml(allowed_paths=["src//x"]),
        _item_toml(allowed_suppressions=["everything"]),
        _item_toml(depends_on=["loop-1"]),
        _item_toml(depends_on=[_ITEM_ID]),
        _item_toml(max_diff_lines=0),
        _item_toml(min_stage=_DROP),
        _item_toml().replace("max_attempts = 2", "max_attempts = 0"),
        _item_toml().split("[budget]", maxsplit=1)[0],
    ],
    ids=[
        "bad-toml",
        "id-mismatch",
        "no-allowed-paths",
        "empty-allowed-paths",
        "bad-glob",
        "unknown-suppression",
        "bad-dependency",
        "self-dependency",
        "zero-diff-lines",
        "no-min-stage",
        "zero-attempts",
        "no-budget",
    ],
)
def test_a_malformed_item_is_rejected(text: str) -> None:
    with pytest.raises(scope.ItemError):
        scope.parse_backlog_item(text, item_id=_ITEM_ID, source="t")


def test_load_backlog_item_reads_the_default_branch_directory(tmp_path: Path) -> None:
    (tmp_path / f"{_ITEM_ID}.toml").write_text(_item_toml(), encoding="utf-8")
    assert scope.load_backlog_item(tmp_path, _ITEM_ID).item_id == _ITEM_ID
    with pytest.raises(scope.ItemError):
        scope.load_backlog_item(tmp_path, _OTHER_ITEM)
    with pytest.raises(scope.ItemError):
        scope.load_backlog_item(tmp_path, "../LOOP-0007")


# ── Proposals ─────────────────────────────────────────────────────────────────


def test_a_proposal_editing_an_existing_adr_fails() -> None:
    verdict = _proposal([_file("docs/adr/0042-governed-self-improvement-loop.md")])
    assert _rules(verdict) == ["record"]


def test_a_proposal_adding_an_adr_and_an_index_row_passes() -> None:
    verdict = _proposal(
        [_file("docs/adr/0043-new-decision.md", status="added"), _file("specs/README.md")]
    )
    assert verdict.passed
    assert verdict.branch_class is PROPOSAL


def test_a_proposal_adding_a_contract_pytest_ini_fails() -> None:
    verdict = _proposal([_file("tests/contract/loop_0007/pytest.ini", status="added")])
    assert sorted(_rules(verdict)) == ["denied", "proposal-paths"]


def test_a_proposal_may_not_touch_source() -> None:
    assert _rules(_proposal([_file("src/mangomas/agents/chat.py")])) == ["record"]
    added = _proposal([_file("src/mangomas/agents/new.py", status="added")])
    assert _rules(added) == ["proposal-paths"]


@pytest.mark.parametrize("status", ["removed", "renamed"])
def test_a_proposal_may_not_delete_or_rename_a_record(status: str) -> None:
    previous = "specs/0001-old.md" if status == "renamed" else None
    verdict = _proposal([_file("specs/0001-example.md", status=status, previous=previous)])
    assert _rules(verdict) == ["record"]


def test_a_proposal_may_not_add_steering_files_under_its_own_paths() -> None:
    verdict = _proposal([_file("docs/proposals/req-42/CLAUDE.md", status="added")])
    assert _rules(verdict) == ["denied"]


def test_a_proposal_copy_counts_as_a_new_file() -> None:
    copied = _file("specs/0099-copy.md", status="copied", previous="specs/0001-example.md")
    assert _proposal([copied]).passed


# ── GitHub client ─────────────────────────────────────────────────────────────


def _response(body: object, *, status: int = 200, link: str | None = None) -> Any:
    headers = {"link": link} if link else {}
    return scope.HttpResponse(status, headers, json.dumps(body).encode())


@dataclass
class FakeTransport:
    """Serves canned responses by URL and records every request."""

    routes: dict[str, Any]
    seen: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str]) -> Any:
        self.seen.append((url, dict(headers)))
        return self.routes[url]


def _client(routes: dict[str, Any], **kwargs: Any) -> tuple[Any, FakeTransport]:
    transport = FakeTransport(routes)
    client = scope.GitHubClient(
        api_url=_API, repository=_REPOSITORY, auth=_AUTH_VALUE, transport=transport, **kwargs
    )
    return client, transport


def _pull_json(head_ref: str = _IMPL_HEAD, *, changed_files: int = 1) -> dict[str, Any]:
    return {
        "number": _NUMBER,
        "head": {"ref": head_ref, "sha": _HEAD_SHA, "repo": {"full_name": _REPOSITORY}},
        "base": {"ref": "dev", "repo": {"full_name": _REPOSITORY}},
        "user": {"login": "maintainer"},
        "changed_files": changed_files,
        "additions": 1,
        "deletions": 1,
    }


def _file_json(
    path: str, *, status: str = "modified", patch: str | None = _PLAIN_PATCH
) -> dict[str, Any]:
    data: dict[str, Any] = {"filename": path, "status": status, "changes": 2}
    if patch is not None:
        data["patch"] = patch
    return data


_PULL_URL: Final[str] = f"{_API}/repos/{_REPOSITORY}/pulls/{_NUMBER}"
_FILES_URL: Final[str] = f"{_PULL_URL}/files?per_page={scope.PAGE_SIZE}"
_TREE_URL: Final[str] = f"{_API}/repos/{_REPOSITORY}/git/trees/{_HEAD_SHA}?recursive=1"
_HISTORY_URL: Final[str] = f"{_API}/repos/{_REPOSITORY}/pulls?state=all&per_page={scope.PAGE_SIZE}"


def test_the_client_reads_a_pull_request_with_its_headers() -> None:
    client, transport = _client({_PULL_URL: _response(_pull_json())})
    pull = client.pull(_NUMBER)
    assert pull == _pull()
    _url, headers = transport.seen[0]
    assert headers["Authorization"] == f"Bearer {_AUTH_VALUE}"
    assert headers["X-GitHub-Api-Version"] == scope.API_VERSION


def test_the_client_sends_no_authorization_without_a_credential() -> None:
    transport = FakeTransport({_PULL_URL: _response(_pull_json())})
    client = scope.GitHubClient(
        api_url=_API, repository=_REPOSITORY, auth=None, transport=transport
    )
    client.pull(_NUMBER)
    assert "Authorization" not in transport.seen[0][1]


def test_the_client_follows_pagination() -> None:
    page_two = f"{_FILES_URL}&page=2"
    renamed = {**_file_json("docs/b.md", status="renamed"), "previous_filename": "docs/a.md"}
    client, _ = _client(
        {
            _FILES_URL: _response(
                [_file_json(_ALLOWED_FILE)], link=f'<{page_two}>; rel="next", <x>; rel="last"'
            ),
            page_two: _response([renamed], link=f'<{_FILES_URL}>; rel="first"'),
        }
    )
    files = client.files(_NUMBER)
    assert [f.path for f in files] == [_ALLOWED_FILE, "docs/b.md"]
    assert files[1].previous_path == "docs/a.md"


def test_a_next_link_off_the_api_origin_is_refused() -> None:
    client, _ = _client(
        {_FILES_URL: _response([], link='<https://elsewhere.example.test/x>; rel="next"')}
    )
    with pytest.raises(scope.GitHubError, match="off the API origin"):
        client.files(_NUMBER)


def test_a_listing_longer_than_the_page_cap_fails() -> None:
    client, _ = _client(
        {_FILES_URL: _response([], link=f'<{_FILES_URL}&page=2>; rel="next"')}, max_pages=1
    )
    with pytest.raises(scope.GitHubError, match="pages"):
        client.files(_NUMBER)


@pytest.mark.parametrize(
    ("url", "response", "call"),
    [
        (_PULL_URL, _response({}, status=404), "pull"),
        (_PULL_URL, scope.HttpResponse(200, {}, b"<html>"), "pull"),
        (_PULL_URL, _response([]), "pull"),
        (_FILES_URL, _response({"not": "a list"}), "files"),
        (
            _HISTORY_URL,
            _response([{"number": 1, "state": "draft", "head": {"ref": "x"}}]),
            "history",
        ),
    ],
    ids=["http-404", "not-json", "not-an-object", "not-an-array", "unknown-state"],
)
def test_unexpected_responses_fail(url: str, response: Any, call: str) -> None:
    client, _ = _client({url: response})
    with pytest.raises(scope.GitHubError):
        if call == "pull":
            client.pull(_NUMBER)
        elif call == "files":
            client.files(_NUMBER)
        else:
            client.history()


def test_the_client_reads_the_tree_and_reports_truncation() -> None:
    entries = [{"path": _ALLOWED_FILE, "mode": _REGULAR_MODE, "type": "blob"}]
    client, _ = _client({_TREE_URL: _response({"tree": entries, "truncated": False})})
    assert client.tree(_HEAD_SHA) == {_ALLOWED_FILE: _REGULAR_MODE}
    for body in ({"tree": entries, "truncated": True}, {"tree": entries}):
        client, _ = _client({_TREE_URL: _response(body)})
        assert client.tree(_HEAD_SHA) is None
    client, _ = _client({_TREE_URL: _response({"tree": "x", "truncated": False})})
    with pytest.raises(scope.GitHubError):
        client.tree(_HEAD_SHA)
    with pytest.raises(scope.GitHubError):
        client.tree("../../etc")


def test_the_client_reads_history() -> None:
    history = [
        {
            "number": 3,
            "state": "closed",
            "merged_at": "2026-11-20T00:00:00Z",
            "head": {"ref": "a", "repo": None},
        },
        {
            "number": 4,
            "state": "open",
            "merged_at": None,
            "head": {"ref": "b", "repo": {"full_name": _REPOSITORY}},
        },
    ]
    client, _ = _client({_HISTORY_URL: _response(history)})
    assert client.history() == [
        _summary(3, "a", merged=True, head_repo=None),
        _summary(4, "b", is_open=True),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("head"),
        lambda d: d["base"].update(repo=None),
        lambda d: d.update(changed_files=True),
        lambda d: d["head"].update(ref=3),
        lambda d: d["head"].update(repo={"full_name": 3}),
    ],
    ids=["no-head", "no-base-repo", "bool-count", "non-string-ref", "bad-repo"],
)
def test_a_malformed_pull_request_is_rejected(mutate: Any) -> None:
    data = _pull_json()
    mutate(data)
    with pytest.raises(scope.GitHubError):
        scope.parse_pull(data)


def test_a_malformed_file_entry_is_rejected() -> None:
    with pytest.raises(scope.GitHubError):
        scope.parse_changed_file({**_file_json(_ALLOWED_FILE), "patch": 3})


@pytest.mark.parametrize(
    ("api_url", "repository"),
    [
        ("http://api.example.test", _REPOSITORY),
        (_API, "not-a-repository"),
        ("https://", _REPOSITORY),
    ],
)
def test_the_client_requires_https_and_an_owner_name_repository(
    api_url: str, repository: str
) -> None:
    with pytest.raises(scope.ConfigError):
        scope.GitHubClient(api_url=api_url, repository=repository, auth=None)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ('<https://a/2>; rel="last"', None),
        ('<https://a/1>; rel="prev", <https://a/3>; rel="next"', "https://a/3"),
    ],
)
def test_next_link(header: str | None, expected: str | None) -> None:
    assert scope.next_link(header) == expected


class _FakeHTTPResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_urllib_transport_returns_status_headers_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float) -> _FakeHTTPResponse:
        assert request.full_url == _PULL_URL
        assert request.get_method() == "GET"
        assert request.get_header("Accept") == "application/json"
        assert timeout == scope.REQUEST_TIMEOUT_SECONDS
        return _FakeHTTPResponse(200, {"Link": "<x>"}, b"[]")

    monkeypatch.setattr(scope.urllib.request, "urlopen", fake_urlopen)
    response = scope.urllib_transport(_PULL_URL, {"Accept": "application/json"})
    assert response == scope.HttpResponse(200, {"link": "<x>"}, b"[]")


def test_urllib_transport_turns_an_http_error_into_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> Any:
        headers = email.message.Message()
        headers["Retry-After"] = "60"
        raise urllib.error.HTTPError(_PULL_URL, 403, "Forbidden", headers, io.BytesIO(b"denied"))

    monkeypatch.setattr(scope.urllib.request, "urlopen", fake_urlopen)
    response = scope.urllib_transport(_PULL_URL, {})
    assert response == scope.HttpResponse(403, {"retry-after": "60"}, b"denied")


def test_urllib_transport_raises_on_a_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(*_args: object, **_kwargs: object) -> Any:
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(scope.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(scope.GitHubError, match="unreachable"):
        scope.urllib_transport(_PULL_URL, {})


# ── Entry point ───────────────────────────────────────────────────────────────


def _environ(tmp_path: Path, **overrides: str) -> dict[str, str]:
    event = tmp_path / "event.json"
    if not event.exists():
        event.write_text(json.dumps({"pull_request": {"number": _NUMBER}}), encoding="utf-8")
    environ = {
        scope.ENV_EVENT_PATH: str(event),
        scope.ENV_REPOSITORY: _REPOSITORY,
        scope.ENV_API_URL: _API,
        scope.ENV_AUTH: _AUTH_VALUE,
        scope.ENV_LOOP_ENABLED: "true",
        scope.ENV_STEP_SUMMARY: str(tmp_path / "summary.md"),
    }
    environ.update(overrides)
    return environ


def _governed_routes(files: list[dict[str, Any]], head_ref: str = _IMPL_HEAD) -> dict[str, Any]:
    tree = [{"path": f["filename"], "mode": _REGULAR_MODE} for f in files]
    return {
        _PULL_URL: _response(_pull_json(head_ref, changed_files=len(files))),
        _FILES_URL: _response(files),
        _TREE_URL: _response({"tree": tree, "truncated": False}),
        _HISTORY_URL: _response([]),
    }


def _main(
    tmp_path: Path,
    routes: dict[str, Any],
    *,
    pyproject: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    backlog = tmp_path / "backlog"
    backlog.mkdir(exist_ok=True)
    (backlog / f"{_ITEM_ID}.toml").write_text(_item_toml(), encoding="utf-8")
    argv = [
        "--pyproject",
        str(pyproject or _REPO_ROOT / "pyproject.toml"),
        "--backlog-dir",
        str(backlog),
    ]
    return int(
        scope.main(argv, environ=_environ(tmp_path, **(env or {})), transport=FakeTransport(routes))
    )


def test_main_passes_an_ungoverned_pr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    routes = {_PULL_URL: _response(_pull_json("feature/new-thing"))}
    assert _main(tmp_path, routes) == scope.EXIT_OK
    assert "RESULT: pass" in capsys.readouterr().out
    assert "### Loop scope check" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_main_passes_a_clean_implementation_pr(tmp_path: Path) -> None:
    routes = _governed_routes([_file_json(_ALLOWED_FILE)])
    assert _main(tmp_path, routes, env={scope.ENV_STEP_SUMMARY: ""}) == scope.EXIT_OK
    assert not (tmp_path / "summary.md").exists(), "no summary file outside Actions"


def test_main_fails_a_violation_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = _governed_routes([_file_json("Makefile")])
    assert _main(tmp_path, routes) == scope.EXIT_VIOLATION
    out = capsys.readouterr().out
    assert 'FAIL [trusted-base] "Makefile" (matches "Makefile")' in out
    assert "RESULT: fail" in out


def test_main_escapes_a_hostile_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hostile = "docs/proposals/x\n::error::pwned.md"
    routes = _governed_routes([_file_json(hostile, status="added")], head_ref=_PROPOSAL_HEAD)
    assert _main(tmp_path, routes) == scope.EXIT_VIOLATION
    out = capsys.readouterr().out
    assert not any(line.startswith("::") for line in out.splitlines())
    assert "\\n::error::pwned.md" in out
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert not any(line.startswith("::") for line in summary.splitlines())


def test_main_judges_by_an_unreadable_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "pyproject.toml"
    broken.write_text("[tool.mangomas]\n", encoding="utf-8")
    governed = {_PULL_URL: _response(_pull_json("claude/anything"))}
    assert _main(tmp_path, governed, pyproject=broken) == scope.EXIT_VIOLATION
    ungoverned = {_PULL_URL: _response(_pull_json("feature/restore-policy"))}
    assert _main(tmp_path, ungoverned, pyproject=broken) == scope.EXIT_OK
    assert "WARNING" in capsys.readouterr().out


@pytest.mark.parametrize(
    "event",
    [
        "not json",
        json.dumps([]),
        json.dumps({"pull_request": {"number": True}}),
        json.dumps({"pull_request": {"number": 0}}),
    ],
)
def test_main_needs_a_pull_request_event(tmp_path: Path, event: str) -> None:
    (tmp_path / "event.json").write_text(event, encoding="utf-8")
    assert _main(tmp_path, {}) == scope.EXIT_ERROR


def test_main_needs_its_environment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main(tmp_path, {}, env={scope.ENV_REPOSITORY: ""}) == scope.EXIT_ERROR
    assert (
        _main(tmp_path, {}, env={scope.ENV_EVENT_PATH: str(tmp_path / "none.json")})
        == scope.EXIT_ERROR
    )
    assert scope.ENV_REPOSITORY in capsys.readouterr().err


def test_main_reports_an_api_failure_as_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = {_PULL_URL: _response({}, status=500)}
    assert _main(tmp_path, routes) == scope.EXIT_ERROR
    assert "HTTP 500" in capsys.readouterr().err


def test_main_falls_back_to_the_public_api_url(tmp_path: Path) -> None:
    url = f"{scope.DEFAULT_API_URL}/repos/{_REPOSITORY}/pulls/{_NUMBER}"
    routes = {url: _response(_pull_json("feature/new-thing"))}
    assert _main(tmp_path, routes, env={scope.ENV_API_URL: ""}) == scope.EXIT_OK


def test_main_survives_an_unwritable_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    routes = {_PULL_URL: _response(_pull_json("feature/new-thing"))}
    code = _main(tmp_path, routes, env={scope.ENV_STEP_SUMMARY: str(tmp_path)})
    assert code == scope.EXIT_OK
    assert "could not write the step summary" in capsys.readouterr().err


def test_the_summary_fence_outgrows_any_backtick_run() -> None:
    summary = scope.render_summary(["path ````x````"])
    assert "`````text" in summary
    assert scope.render_summary(["plain"]).count("```") == 2
