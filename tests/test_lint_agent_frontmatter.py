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


def _frontmatter_of(text: str) -> dict[str, object]:
    """Parse a fixture's frontmatter with the linter's own splitter.

    Reusing ``_split_frontmatter`` rather than a second YAML load keeps the
    field-level tests exercising the same parse the lint performs.
    """
    parsed: dict[str, object] = linter._split_frontmatter(text)
    return parsed


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


def _write_agent(directory: Path, slug: str, text: str | None = None) -> Path:
    """Write an agent fixture at the filename its ``name`` field requires.

    Claude Code resolves an agent by its ``name``, so the lint requires name and
    filename stem to agree; fixtures have to honour that or they exercise the
    mismatch branch instead of the one under test.
    """
    body = text if text is not None else constants.VALID_CLAUDE_AGENT_FRONTMATTER
    body = body.replace(f"name: {constants.VALID_CLAUDE_AGENT_SLUG}", f"name: {slug}")
    path = directory / f"{slug}{linter.AGENT_FILE_SUFFIX}"
    path.write_text(body, encoding="utf-8")
    return path


def test_valid_agent_frontmatter_returns_no_errors(tmp_path: Path) -> None:
    path = _write_agent(tmp_path, constants.VALID_CLAUDE_AGENT_SLUG)
    assert linter._validate_agent(str(path)) == []


def test_missing_tools_agent_returns_error(tmp_path: Path) -> None:
    """``tools`` is required: omitting it makes an agent inherit *every* tool,
    so silence here would hand out full privilege."""
    path = tmp_path / f"{constants.MISSING_TOOLS_AGENT_SLUG}{linter.AGENT_FILE_SUFFIX}"
    path.write_text(constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8")
    errors = linter._validate_agent(str(path))
    assert len(errors) == 1
    assert "schema violation" in errors[0]


def test_agent_unknown_tool_returns_error(tmp_path: Path) -> None:
    invalid = constants.VALID_CLAUDE_AGENT_FRONTMATTER.replace(
        "tools: Read, Grep, Glob, Skill", "tools: Read, Delete"
    )
    path = _write_agent(tmp_path, constants.VALID_CLAUDE_AGENT_SLUG, invalid)
    errors = linter._validate_agent(str(path))
    assert len(errors) == 1
    assert "unrecognised tool token" in errors[0]


def test_name_must_match_the_filename_stem(tmp_path: Path) -> None:
    """A file whose ``name`` disagrees with its stem loads under a name that
    does not match where it lives — findable only by reading the frontmatter."""
    path = tmp_path / f"somewhere-else{linter.AGENT_FILE_SUFFIX}"
    path.write_text(constants.VALID_CLAUDE_AGENT_FRONTMATTER, encoding="utf-8")
    errors = linter._validate_agent(str(path))
    assert len(errors) == 1
    assert "filename stem" in errors[0]


def test_legacy_copilot_agent_is_rejected_with_specific_messages(tmp_path: Path) -> None:
    """A ``.agent.md`` resurrected from a rebase must not pass, and must say
    *what* is wrong. ``extra="forbid"`` alone would emit one generic "Extra
    inputs are not permitted" for ``argument-hint`` and stop there."""
    path = tmp_path / f"parent{linter.AGENT_FILE_SUFFIX}"
    path.write_text(constants.VALID_AGENT_FRONTMATTER, encoding="utf-8")
    joined = " ".join(linter._validate_agent(str(path)))
    assert "argument-hint" in joined
    assert "unrecognised tool token" in joined
    assert "Extra inputs are not permitted" not in joined


def test_invalid_yaml_frontmatter_is_reported_not_raised(tmp_path: Path) -> None:
    """An unquoted description containing a colon raises ``yaml.ScannerError``,
    which is not a ``ValueError`` — it used to escape the caller's handler and
    surface as a traceback instead of a message naming the file."""
    path = tmp_path / f"broken{linter.AGENT_FILE_SUFFIX}"
    path.write_text(
        "---\nname: broken\ndescription: Routing for X: routers, DTOs and more text here\n---\n",
        encoding="utf-8",
    )
    errors = linter._validate_agent(str(path))
    assert len(errors) == 1
    assert "invalid YAML" in errors[0]


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
        _write_agent(agents_dir, f"agent-{index}")

    monkeypatch.chdir(tmp_path)
    result = linter.run_schema_lint(min_agents=1, min_skills=1)
    assert result.exit_code == linter.EXIT_OK
    assert (result.skill_count, result.agent_count) == (3, 3)


def test_floors_are_overridable_via_cli_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tooling knobs get a script-level ``Final`` plus a flag, never a bare literal.

    Overridability is demonstrated with a floor the corpus *fails* — the previous
    version of this test passed ``0`` against an empty directory and asserted
    ``EXIT_OK``, which proved only that a disabled guard stays quiet.
    """
    skills_dir = tmp_path / _glob_root(linter.SKILLS_GLOB)
    agents_dir = tmp_path / _glob_root(linter.AGENTS_GLOB)
    skill = skills_dir / "skill-0"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(constants.VALID_SKILL_FRONTMATTER, encoding="utf-8")
    agents_dir.mkdir(parents=True)
    _write_agent(agents_dir, "agent-0")

    monkeypatch.chdir(tmp_path)
    assert linter.main(["--min-agents", "1", "--min-skills", "1"]) == linter.EXIT_OK
    # The same corpus, one over the floor: the raised value is what changes the verdict.
    assert linter.main(["--min-agents", "2", "--min-skills", "1"]) == linter.EXIT_SCHEMA


@pytest.mark.parametrize("flag", ["--min-agents", "--min-skills"])
@pytest.mark.parametrize("value", ["-1", "0"])
def test_floor_below_lower_bound_is_rejected(
    flag: str, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A floor of 0 or less can never fail, so accepting one silently restores the
    gate that passes without validating anything. ``tmp_path`` is empty, so the
    run would otherwise report EXIT_OK — proving the guard, not the corpus."""
    monkeypatch.chdir(tmp_path)
    assert linter.main([flag, value]) == linter.EXIT_SCHEMA


def test_floor_rejection_names_the_flag_and_the_bound(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error has to say which flag and what bound, or it can't be acted on."""
    caplog.set_level(logging.ERROR, logger=linter.__name__)
    monkeypatch.chdir(tmp_path)
    assert linter.main(["--min-agents", "-1"]) == linter.EXIT_SCHEMA
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "--min-agents" in logged
    assert str(linter.MIN_FLOOR_LOWER_BOUND) in logged


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
    # Named for its `name` field so it fails on the missing `tools` — the
    # schema violation under test — rather than on the name/stem mismatch.
    broken = agents_dir / f"{constants.MISSING_TOOLS_AGENT_SLUG}{linter.AGENT_FILE_SUFFIX}"
    broken.write_text(constants.MALFORMED_AGENT_FRONTMATTER_MISSING_TOOLS, encoding="utf-8")

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


_BASH_PROTECTED_PAYLOAD: Final[str] = json.dumps(
    {
        "tool_name": "Bash",
        "tool_input": {"command": "sed -i 's/x/y/' src/mangomas/errors.py"},
    }
)
_BASH_BACKSLASH_PAYLOAD: Final[str] = json.dumps(
    {
        "tool_name": "Bash",
        "tool_input": {"command": "type src\\mangomas\\core\\agent.py"},
    }
)
_BASH_UNPROTECTED_PAYLOAD: Final[str] = json.dumps(
    {"tool_name": "Bash", "tool_input": {"command": "python -m pytest -q"}}
)


def test_pre_tool_use_bash_protected_mention_emits_ask(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Bash command mentioning a protected path gets the advisory `ask`.

    Shell writes bypass the Edit|Write|NotebookEdit matcher entirely — the
    gap ADR-0021 concedes. The Bash registration narrows it at mention level
    (spec-0022 R11) without reversing ADR-0021's rejected hard block.
    """
    result = linter._pre_tool_use_hook(StringIO(_BASH_PROTECTED_PAYLOAD))
    assert result == linter.EXIT_OK
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "src/mangomas/errors.py" in decision["permissionDecisionReason"]
    assert linter.BREAKING_CHANGE_MARKER in decision["permissionDecisionReason"]


def test_pre_tool_use_bash_backslash_paths_are_recognised(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Windows-style separators normalise before the protected-path scan."""
    linter._pre_tool_use_hook(StringIO(_BASH_BACKSLASH_PAYLOAD))
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "src/mangomas/core/agent.py" in decision["permissionDecisionReason"]


def test_pre_tool_use_bash_unprotected_command_emits_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = linter._pre_tool_use_hook(StringIO(_BASH_UNPROTECTED_PAYLOAD))
    assert result == linter.EXIT_OK
    assert capsys.readouterr().out == ""


def test_pre_tool_use_bash_never_returns_deny(capsys: pytest.CaptureFixture[str]) -> None:
    """The Bash branch is advisory only, exactly like the file-path branch."""
    for payload in (_BASH_PROTECTED_PAYLOAD, _BASH_UNPROTECTED_PAYLOAD):
        assert linter._pre_tool_use_hook(StringIO(payload)) == linter.EXIT_OK
    assert '"deny"' not in capsys.readouterr().out


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


def test_subprocess_pre_tool_use_bash_mention_exits_ok_with_ask_json() -> None:
    """The Bash-matcher registration pipes through the same mode (spec-0022 R11)."""
    result = _run_hook_subprocess(["--hook", "pre-tool-use"], _BASH_PROTECTED_PAYLOAD)
    assert result.returncode == linter.EXIT_OK
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "src/mangomas/errors.py" in decision["permissionDecisionReason"]


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


# ── Claude Code agent-format validators (spec-0018 / ADR-0024) ────────────────
#
# These helpers are unwired in this commit — defined and tested, called by
# nothing. The schema swap that calls them is a separate, near-mechanical diff.


def test_valid_claude_agent_frontmatter_has_no_field_errors() -> None:
    """The positive case, so every rejection test below is a real signal rather
    than a fixture that could never pass."""
    fm = _frontmatter_of(constants.VALID_CLAUDE_AGENT_FRONTMATTER)
    assert linter._invalid_agent_fields(fm) == []


@pytest.mark.parametrize("model", constants.INVALID_AGENT_MODEL_VALUES)
def test_uppercase_or_spaced_model_is_rejected(model: str) -> None:
    """One rule retires the whole ``Claude Sonnet 4.5 (copilot)`` class that all
    19 agents carried, without this script tracking a live model list."""
    assert not linter._model_value_is_valid(model)


@pytest.mark.parametrize("model", ["inherit", "sonnet", "opus", "claude-opus-5"])
def test_alias_and_model_id_are_accepted(model: str) -> None:
    assert linter._model_value_is_valid(model)


def test_copilot_tool_aliases_are_rejected() -> None:
    """`read`/`edit`/`search`/`execute` are valid Copilot aliases and meaningless
    to Claude Code. Since omitting ``tools`` inherits *every* tool, an
    unrecognised list is the dangerous kind of wrong, not a harmless one."""
    invalid = linter._invalid_tool_tokens(list(constants.INVALID_AGENT_TOOL_TOKENS))
    assert invalid == list(constants.INVALID_AGENT_TOOL_TOKENS)


def test_real_tool_names_and_mcp_tools_are_accepted() -> None:
    tokens = [*constants.VALID_AGENT_TOOL_TOKENS, constants.VALID_MCP_TOOL_NAME]
    assert linter._invalid_tool_tokens(tokens) == []


def test_scoped_delegation_form_is_rejected() -> None:
    """``Agent(a, b)`` is ignored inside a subagent definition — the agent gets
    unrestricted delegation rather than the named subset. A rule that looks like
    a restriction and isn't is worse than no rule, so it fails the lint."""
    tokens = [constants.SCOPED_DELEGATION_TOOL_SPEC]
    assert linter._scoped_delegation_tokens(tokens) == tokens


@pytest.mark.parametrize(
    "raw",
    ["Read, Grep, Glob", ["Read", "Grep", "Glob"]],
    ids=["comma-string", "yaml-list"],
)
def test_tools_accepts_both_documented_shapes(raw: object) -> None:
    assert linter._normalize_tools(raw) == ["Read", "Grep", "Glob"]


@pytest.mark.parametrize("raw", [42, None, {"Read": True}, ["Read", 7]])
def test_tools_rejects_shapes_that_are_not_token_lists(raw: object) -> None:
    assert linter._normalize_tools(raw) is None


@pytest.mark.parametrize("field", constants.POLICY_REJECTED_AGENT_FIELDS)
def test_policy_rejected_field_explains_the_project_policy(field: str) -> None:
    """These are valid Claude Code fields, so ``extra="forbid"`` would say
    "Extra inputs are not permitted" — sending the reader to hunt a typo that
    isn't there. The message has to say *this project declines it*."""
    errors = linter._policy_rejected_fields({field: "whatever"})
    assert len(errors) == 1
    assert field in errors[0]
    assert ".claude/settings.json" in errors[0]


@pytest.mark.parametrize("field", constants.LEGACY_AGENT_FIELDS)
def test_legacy_copilot_field_is_named_as_such(field: str) -> None:
    """Every file in the migrating corpus carries at least one of these, so this
    is among the most-read messages of the migration."""
    errors = linter._legacy_format_fields({field: "whatever"})
    assert len(errors) == 1
    assert field in errors[0]


def test_policy_and_legacy_messages_are_distinguishable() -> None:
    """A rejected-by-policy field and a wrong-format field must not read the
    same — they call for different fixes."""
    policy = linter._policy_rejected_fields({"permissionMode": "acceptEdits"})[0]
    legacy = linter._legacy_format_fields({"argument-hint": "x"})[0]
    assert policy != legacy
    assert "not a Claude Code agent field" in legacy
    assert "not a Claude Code agent field" not in policy


@pytest.mark.parametrize("name", ["Example", "mango_agent", "Mango-Agent", "agent!"])
def test_non_kebab_name_is_rejected(name: str) -> None:
    fm = {"name": name}
    assert any("kebab-case" in error for error in linter._invalid_agent_fields(fm))


@pytest.mark.parametrize("name", ["backend", "mango-orchestrator-dev", "sse-streamer"])
def test_kebab_name_is_accepted(name: str) -> None:
    fm = {"name": name}
    assert linter._invalid_agent_fields(fm) == []


def test_legacy_corpus_fixture_fails_on_every_axis() -> None:
    """The fixture the 19 agents actually used, run through the new validator:
    Copilot tool aliases, a spaced/uppercase model, a Title-Case name, and
    ``argument-hint``. Proves the migration's error output is useful rather
    than one generic complaint."""
    fm = _frontmatter_of(constants.VALID_AGENT_FRONTMATTER)
    errors = linter._invalid_agent_fields(fm)
    joined = " ".join(errors)
    assert "argument-hint" in joined
    assert "kebab-case" in joined
    assert "model" in joined
    assert "unrecognised tool token" in joined
