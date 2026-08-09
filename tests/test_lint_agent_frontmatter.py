"""Tests for ``scripts/lint_agent_frontmatter.py``."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Final

import pytest

from tests import constants
from tests._script_loader import load_script_module

linter = load_script_module("lint_agent_frontmatter.py")

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCRIPT_PATH: Final[Path] = _REPO_ROOT / "scripts" / "lint_agent_frontmatter.py"


def _arbitrary_protected_path() -> str:
    """Return any one path the linter considers protected (order-independent)."""
    # Picking deterministically from the frozenset keeps tests stable across runs.
    paths: frozenset[str] = linter.PROTECTED_PATHS
    return sorted(paths)[0]


def _arbitrary_unprotected_path() -> str:
    """Return a path the linter is guaranteed to consider unprotected."""
    return "src/mangomas/agents/chat.py"


def _glob_root(pattern: str) -> str:
    """Return the fixed directory prefix of a recursive glob.

    Fixture trees are built from the linter's own globs rather than from
    hardcoded literals, so relocating a corpus root updates every test that
    depends on it. A hardcoded fixture path does not merely go stale — under
    the non-empty floor it keeps the test *passing for the wrong reason*
    (zero files discovered still yields EXIT_SCHEMA), which is the silent
    class of failure this whole spec exists to remove.
    """
    return pattern.split("/**", 1)[0]


# ── Schema validation (skills) ────────────────────────────────────────────────


def test_valid_skill_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    assert linter._validate_skill(str(skill_path)) == []


def test_short_description_skill_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(constants.MALFORMED_SKILL_FRONTMATTER_SHORT_DESCRIPTION, encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_missing_frontmatter_returns_error(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    errors = linter._validate_skill(str(skill_path))
    assert len(errors) == 1
    assert "missing frontmatter" in errors[0]


# ── Schema validation (agents) ────────────────────────────────────────────────


def test_valid_agent_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")
    assert linter._validate_agent(str(parent_path), [str(parent_path)]) == []


def test_missing_tools_agent_returns_error(tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_agent_unknown_tool_returns_error(tmp_path: Path) -> None:
    invalid = constants.VALID_AGENT_FRONTMATTER.replace("tools: [read, search]", "tools: [delete]")
    parent_path = tmp_path / "parent.agent.md"
    parent_path.write_text(invalid, encoding="utf-8")
    errors = linter._validate_agent(str(parent_path), [str(parent_path)])
    assert len(errors) == 1
    assert "schema violation" in errors[0]


# ── sub_agents resolution ─────────────────────────────────────────────────────


def test_sub_agents_resolves_to_existing_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent declaring ``sub_agents: [child]`` is valid when the child file exists."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    parent_path = agents_dir / "myparent.agent.md"
    child_dir = agents_dir / "myparent"
    child_dir.mkdir()
    child_path = child_dir / "mychild.agent.md"

    parent_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - mychild\n---\n\nBody.\n"
    )
    parent_path.write_text(parent_body, encoding="utf-8")
    child_path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent.agent.md",
        [
            ".github/agents/myparent.agent.md",
            ".github/agents/myparent/mychild.agent.md",
        ],
    )
    assert errors == []


def test_sub_agents_missing_child_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent declaring an unresolvable sub_agents slug returns ``missing file``."""
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    parent_path = agents_dir / "myparent.agent.md"
    parent_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - ghost\n---\n\nBody.\n"
    )
    parent_path.write_text(parent_body, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent.agent.md",
        [".github/agents/myparent.agent.md"],
    )
    assert len(errors) == 1
    assert "missing file" in errors[0]


def test_sub_agents_on_child_file_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child file declaring its own sub_agents is rejected — hierarchy is two-deep only."""
    agents_dir = tmp_path / ".github" / "agents" / "myparent"
    agents_dir.mkdir(parents=True)
    child_path = agents_dir / "mychild.agent.md"
    child_body = constants.VALID_AGENT_FRONTMATTER.replace(
        "---\n\nBody content.\n", "sub_agents:\n  - grandchild\n---\n\nBody.\n"
    )
    child_path.write_text(child_body, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    errors = linter._validate_agent(
        ".github/agents/myparent/mychild.agent.md",
        [".github/agents/myparent/mychild.agent.md"],
    )
    assert len(errors) == 1
    assert "only valid on parent" in errors[0]


# ── Protected-path enforcement ────────────────────────────────────────────────


def test_protected_path_check_unprotected_returns_ok() -> None:
    """An unprotected path always returns EXIT_OK."""
    assert linter._check_protected_path(_arbitrary_unprotected_path()) == linter.EXIT_OK


def test_protected_path_without_marker_returns_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path with no marker in staged diff returns EXIT_PROTECTED."""

    def fake_diff(_path: str) -> str:
        return "no marker here, just code changes\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_PROTECTED


def test_protected_path_with_marker_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protected path whose diff contains the marker returns EXIT_OK."""

    def fake_diff(_path: str) -> str:
        return f"some diff\n{linter.BREAKING_CHANGE_MARKER}\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_OK


@pytest.mark.parametrize(
    "protected",
    [
        "src/mangomas/core/agent.py",
        "src/mangomas/core/orchestrator.py",
        "src/mangomas/core/tools.py",
        "src/mangomas/errors.py",
        "src/mangomas/registry.py",
    ],
)
def test_protected_paths_cover_documented_core_contracts(protected: str) -> None:
    """Every core contract documented in CLAUDE.md is gated by the linter."""
    assert protected in linter.PROTECTED_PATHS


def test_protected_path_accepts_legacy_alias_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy ``# approved-breaking-change`` marker is still accepted."""

    def fake_diff(_path: str) -> str:
        return "some diff\n# approved-breaking-change\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_OK


def test_protected_path_windows_backslash_without_marker_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows backslash path must normalize to a protected path and be gated.

    Regression guard: ``str.lstrip('./')`` left backslash paths unrecognised, so
    a ``.\\src\\...\\agent.py`` edit bypassed the hook entirely.
    """

    def fake_diff(_path: str) -> str:
        return "no marker here, just code changes\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    windows_path = r".\src\mangomas\core\agent.py"
    assert linter._check_protected_path(windows_path) == linter.EXIT_PROTECTED


def test_protected_path_windows_backslash_with_marker_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normalized Windows path with the marker in its diff is approved."""

    def fake_diff(_path: str) -> str:
        return f"some diff\n{linter.BREAKING_CHANGE_MARKER}\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    windows_path = r".\src\mangomas\core\agent.py"
    assert linter._check_protected_path(windows_path) == linter.EXIT_OK


def test_protected_path_marker_embedded_in_prose_is_not_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard mirroring check_protected_paths.py's: a bare
    substring test would treat a diff line explaining a change is *not*
    breaking as an approval, since the literal marker text still appears
    mid-sentence."""

    def fake_diff(_path: str) -> str:
        return "This is NOT a BREAKING-CHANGE, just an internal cleanup.\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_PROTECTED


def test_protected_path_marker_on_an_added_diff_line_is_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real ``git diff`` output prefixes every content line with ``+``/``-``/
    a context space — unlike the other tests in this file, which use a bare
    marker line as a simplifying fake. An *added* line (``+``-prefixed)
    must still be recognized."""

    def fake_diff(_path: str) -> str:
        return f"+{linter.BREAKING_CHANGE_MARKER}: widened protocol\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_OK


def test_protected_path_marker_on_a_deleted_diff_line_is_not_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A *deleted* marker line (``-``-prefixed real diff output) must not
    count as an approval — the change being staged removes the marker, it
    doesn't add it."""

    def fake_diff(_path: str) -> str:
        return f"-{linter.BREAKING_CHANGE_MARKER}: no longer breaking\n"

    monkeypatch.setattr(linter, "_staged_diff", fake_diff)
    assert linter._check_protected_path(_arbitrary_protected_path()) == linter.EXIT_PROTECTED


def test_staged_diff_returns_empty_on_git_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_staged_diff`` swallows git failures and returns an empty string."""
    import subprocess  # noqa: PLC0415 -- local import keeps top-of-file lean

    class _FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository"

    def fake_run(*_args: object, **_kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert linter._staged_diff("nonexistent.py") == ""


# ── main() integration ────────────────────────────────────────────────────────


def test_main_returns_ok_on_clean_repo() -> None:
    """Running the linter against the actual repo must pass."""
    assert linter.main([]) == linter.EXIT_OK


# ── Non-empty corpus floor (spec-0018 R1/R2) ──────────────────────────────────
#
# The defect these guard: ``main()`` used to fall straight through to
# "Frontmatter lint passed" and EXIT_OK when both globs matched ZERO files, and
# the counts went into an ``extra={}`` the log format drops — so a corpus that
# moved out from under the globs produced a green, silent, entirely vacuous gate.


def test_run_schema_lint_reports_nonzero_counts_on_the_real_repo() -> None:
    """The vacuity check ``test_main_returns_ok_on_clean_repo`` cannot make:
    that the run actually discovered files rather than passing on an empty set."""
    result = linter.run_schema_lint()
    assert result.exit_code == linter.EXIT_OK
    assert result.skill_count >= linter.MIN_SKILL_FILES
    assert result.agent_count >= linter.MIN_AGENT_FILES
    assert result.failures == ()


def test_empty_corpus_fails_instead_of_passing_vacuously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero matched files must be a loud failure, not EXIT_OK."""
    monkeypatch.chdir(tmp_path)
    result = linter.run_schema_lint()
    assert result.exit_code == linter.EXIT_SCHEMA
    assert result.skill_count == 0
    assert result.agent_count == 0
    assert len(result.failures) == 2


def test_floor_failure_names_the_offending_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare "0 files" error would send a reader hunting. The message must
    name the glob, since a corpus that moved is the likeliest cause."""
    monkeypatch.chdir(tmp_path)
    failures = linter.run_schema_lint().failures
    assert any(linter.SKILLS_GLOB in message for message in failures)
    assert any(linter.AGENTS_GLOB in message for message in failures)


def test_floor_is_a_minimum_not_an_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A surplus over the floor must pass — otherwise every legitimate corpus
    addition would break the gate and the floor would need constant bumping."""
    skills_dir = tmp_path / _glob_root(linter.SKILLS_GLOB)
    agents_dir = tmp_path / _glob_root(linter.AGENTS_GLOB)
    for index in range(3):
        skill = skills_dir / f"skill-{index}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    agents_dir.mkdir(parents=True)
    for index in range(3):
        (agents_dir / f"agent-{index}.agent.md").write_text(
            constants.VALID_AGENT_FRONTMATTER, encoding="utf-8"
        )

    monkeypatch.chdir(tmp_path)
    result = linter.run_schema_lint(min_agents=1, min_skills=1)
    assert result.exit_code == linter.EXIT_OK
    assert (result.skill_count, result.agent_count) == (3, 3)


def test_floors_are_overridable_via_cli_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tooling knobs get a script-level ``Final`` plus a flag, never a bare literal."""
    monkeypatch.chdir(tmp_path)
    assert linter.main(["--min-agents", "0", "--min-skills", "0"]) == linter.EXIT_OK


def test_passing_log_message_carries_the_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression guard: the counts previously went to ``extra={}``, which the
    configured format string drops — so "0 skills, 0 agents" was invisible."""
    caplog.set_level(logging.INFO, logger=linter.__name__)
    assert linter.main([]) == linter.EXIT_OK
    passed = [r for r in caplog.records if "Frontmatter lint passed" in r.getMessage()]
    assert len(passed) == 1
    message = passed[0].getMessage()
    assert "skills" in message and "agents" in message
    assert "0 skills" not in message


def test_main_protected_mode_unprotected_path() -> None:
    """``--check-protected-paths`` against an unprotected path returns EXIT_OK."""
    assert linter.main(["--check-protected-paths", _arbitrary_unprotected_path()]) == linter.EXIT_OK


def test_main_schema_failure_returns_exit_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed agent file makes ``main()`` exit with ``EXIT_SCHEMA``.

    Fixture roots come from the globs (see :func:`_glob_root`): hardcoded ones
    would keep this test green while discovering nothing, since an empty
    corpus now also returns ``EXIT_SCHEMA``. The count assertions below pin
    the distinction — the files must actually have been found and rejected on
    their *schema*, not skipped and rejected on the floor.
    """
    agents_dir = tmp_path / _glob_root(linter.AGENTS_GLOB)
    agents_dir.mkdir(parents=True)
    skills_dir = tmp_path / _glob_root(linter.SKILLS_GLOB) / "broken"
    skills_dir.mkdir(parents=True)

    (skills_dir / "SKILL.md").write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    (agents_dir / "broken.agent.md").write_text(
        constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8"
    )

    monkeypatch.chdir(tmp_path)
    result = linter.run_schema_lint()
    assert result.exit_code == linter.EXIT_SCHEMA
    assert (result.skill_count, result.agent_count) == (1, 1)
    assert any("schema violation" in failure for failure in result.failures)
    assert linter.main([]) == linter.EXIT_SCHEMA


# ── Hook modes (ADR-0021 / spec-0017) ─────────────────────────────────────────
#
# These deliberately do NOT monkeypatch anything internal to the hook
# functions — that is exactly the pattern that hid the original defects
# ($CLAUDE_TOOL_INPUT_path expanding empty, and the import-time crash on a
# bare interpreter). Every test below either calls the public hook function
# with a real stdin-shaped stream, or shells out to the real script as a
# subprocess, matching how `.claude/settings.json` actually invokes it.

_PROTECTED_PAYLOAD: Final[str] = json.dumps(
    {"tool_name": "Edit", "tool_input": {"file_path": "src/mangomas/errors.py"}}
)
_UNPROTECTED_PAYLOAD: Final[str] = json.dumps(
    {"tool_name": "Edit", "tool_input": {"file_path": "src/mangomas/agents/chat.py"}}
)
_NOTEBOOK_PAYLOAD: Final[str] = json.dumps(
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "src/mangomas/errors.py"}}
)


def test_pre_tool_use_protected_path_returns_ask_decision() -> None:
    result = linter._pre_tool_use_hook(StringIO(_PROTECTED_PAYLOAD))
    assert result == linter.EXIT_OK


def test_pre_tool_use_protected_path_emits_valid_hook_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    linter._pre_tool_use_hook(StringIO(_PROTECTED_PAYLOAD))
    out = capsys.readouterr().out
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "ask"
    assert "src/mangomas/errors.py" in decision["permissionDecisionReason"]
    assert linter.BREAKING_CHANGE_MARKER in decision["permissionDecisionReason"]


def test_pre_tool_use_never_returns_deny() -> None:
    """The hook is advisory only — it must never emit ``permissionDecision:
    "deny"``; the CI gate (scripts/check_protected_paths.py) is the
    authoritative enforcement point."""
    for payload in (_PROTECTED_PAYLOAD, _UNPROTECTED_PAYLOAD):
        assert linter._pre_tool_use_hook(StringIO(payload)) == linter.EXIT_OK


def test_pre_tool_use_unprotected_path_emits_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    result = linter._pre_tool_use_hook(StringIO(_UNPROTECTED_PAYLOAD))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out == ""


def test_pre_tool_use_notebook_edit_path_is_recognised(capsys: pytest.CaptureFixture[str]) -> None:
    """``notebook_path`` (NotebookEdit) is checked too, not just ``file_path``."""
    linter._pre_tool_use_hook(StringIO(_NOTEBOOK_PAYLOAD))
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"


def test_pre_tool_use_malformed_stdin_returns_ok_silently(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = linter._pre_tool_use_hook(StringIO("not json at all"))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out == ""


def test_pre_tool_use_empty_stdin_returns_ok_silently(capsys: pytest.CaptureFixture[str]) -> None:
    result = linter._pre_tool_use_hook(StringIO(""))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out == ""


def test_pre_tool_use_payload_missing_tool_input_returns_ok() -> None:
    result = linter._pre_tool_use_hook(StringIO(json.dumps({"tool_name": "Edit"})))
    assert result == linter.EXIT_OK


@pytest.mark.parametrize("stdin_json", ["[1, 2, 3]", '"just a string"', "42", "null"])
def test_read_hook_payload_rejects_non_dict_json(stdin_json: str) -> None:
    """``_read_hook_payload``'s ``isinstance(payload, dict)`` guard is
    exercised elsewhere only against non-JSON stdin; valid JSON that
    decodes to something other than an object (an array, a bare string, a
    number, ``null``) must degrade to ``{}`` the same way, not raise or
    propagate a non-dict value downstream."""
    assert linter._read_hook_payload(StringIO(stdin_json)) == {}


def test_post_tool_use_emit_path_prints_the_path(capsys: pytest.CaptureFixture[str]) -> None:
    result = linter._post_tool_use_emit_path(StringIO(_UNPROTECTED_PAYLOAD))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out.strip() == "src/mangomas/agents/chat.py"


def test_post_tool_use_emit_path_prints_nothing_for_empty_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = linter._post_tool_use_emit_path(StringIO("{}"))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out == ""


# ── Subprocess-level: the real invocation shape .claude/settings.json uses ───


def _run_hook_subprocess(args: list[str], stdin_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT_PATH), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )


def test_subprocess_pre_tool_use_protected_path_exits_ok_with_ask_json() -> None:
    result = _run_hook_subprocess(["--hook", "pre-tool-use"], _PROTECTED_PAYLOAD)
    assert result.returncode == linter.EXIT_OK
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"


def test_subprocess_post_tool_use_emit_path_pipes_the_path() -> None:
    result = _run_hook_subprocess(["--hook", "post-tool-use", "--emit-path"], _UNPROTECTED_PAYLOAD)
    assert result.returncode == linter.EXIT_OK
    assert result.stdout.strip() == "src/mangomas/agents/chat.py"


def test_subprocess_post_tool_use_without_emit_path_flag_prints_nothing() -> None:
    """Reserved for future PostToolUse modes; today it's a documented no-op."""
    result = _run_hook_subprocess(["--hook", "post-tool-use"], _UNPROTECTED_PAYLOAD)
    assert result.returncode == linter.EXIT_OK
    assert result.stdout == ""


def test_subprocess_hook_mode_runs_without_pydantic_installed(tmp_path: Path) -> None:
    """Regression guard for the exact defect this milestone fixes: the hook
    used to crash on `import pydantic` before reaching any logic (exit 1,
    which Claude Code treats as non-blocking — so the check silently never
    ran). A stub `pydantic` module earlier on `sys.path` than the real one
    reproduces "pydantic not installed" deterministically, without depending
    on any particular interpreter happening to lack it."""
    stub_dir = tmp_path / "stub_site_packages"
    stub_dir.mkdir()
    (stub_dir / "pydantic.py").write_text(
        "raise ImportError('stub: pydantic intentionally unavailable for this test')\n",
        encoding="utf-8",
    )
    (stub_dir / "yaml.py").write_text(
        "raise ImportError('stub: pyyaml intentionally unavailable for this test')\n",
        encoding="utf-8",
    )
    import os  # noqa: PLC0415 -- scoped to this one test's env construction

    env = dict(os.environ)
    env["PYTHONPATH"] = str(stub_dir) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT_PATH), "--hook", "pre-tool-use"],
        input=_PROTECTED_PAYLOAD,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == linter.EXIT_OK
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"


def test_subprocess_default_schema_lint_mode_still_requires_pydantic(tmp_path: Path) -> None:
    """The *default* (no-flag) schema-lint mode is unaffected by this
    milestone — it always needed pydantic/pyyaml and still does. This pins
    the friendly error path added alongside the hook-mode fix, rather than a
    bare NameError, when the dev extras genuinely are not installed."""
    stub_dir = tmp_path / "stub_site_packages"
    stub_dir.mkdir()
    (stub_dir / "pydantic.py").write_text(
        "raise ImportError('stub: pydantic intentionally unavailable for this test')\n",
        encoding="utf-8",
    )
    import os  # noqa: PLC0415 -- scoped to this one test's env construction

    env = dict(os.environ)
    env["PYTHONPATH"] = str(stub_dir) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == linter.EXIT_SCHEMA
    assert "dev' extra" in (result.stdout + result.stderr)
