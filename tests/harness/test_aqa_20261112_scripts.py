"""Regression + AQA guards for ``scripts`` harness hooks (spec-0050 D-11)."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from tests._script_loader import load_script_module

linter = load_script_module("lint_agent_frontmatter.py")
guard = load_script_module("_destructive_bash.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "variant_path",
    [
        str((_REPO_ROOT / "src" / "mangomas" / "errors.py").resolve()),
        "./src/mangomas/agents/../errors.py",
        "SRC\\MANGOMAS\\ERRORS.PY",
    ],
    ids=["absolute-repo-path", "dot-slash-dotdot-traversal", "uppercase-windows-backslash"],
)
def test_d11_pre_tool_use_recognises_canonical_and_case_varied_protected_paths(
    variant_path: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``PreToolUse`` must match protected paths across absolute, traversal, and case variants."""
    monkeypatch.chdir(_REPO_ROOT)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": variant_path}})
    result = linter._pre_tool_use_hook(StringIO(payload))
    assert result == linter.EXIT_OK
    out = capsys.readouterr().out
    assert out, f"Expected ask advisory for {variant_path!r}, got empty stdout"
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "src/mangomas/errors.py" in decision["permissionDecisionReason"]


def test_d11_post_tool_use_emit_path_normalises_windows_backslashes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Native Windows backslash paths must normalise to ``/`` and emit cleanly."""
    payload = json.dumps(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": r"src\mangomas\agents\chat.py"},
        }
    )
    result = linter._post_tool_use_emit_path(StringIO(payload))
    assert result == linter.EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.strip() == "src/mangomas/agents/chat.py"
    assert captured.err == ""


@pytest.mark.parametrize(
    "command",
    [
        "python -m guard_probe --manifest evil.json",
        "python -m scripts.guard_probe --manifest=evil.json",
        "python3 -u -m scripts.guard_probe --manifest evil.json",
        "py -3 -mguard_probe --manifest evil.json",
    ],
)
def test_d11_destructive_bash_blocks_python_module_guard_probe_manifest(
    command: str,
) -> None:
    """``python -m [scripts.]guard_probe --manifest ...`` must trigger the ``deny`` guard."""
    assert guard.destructive_bash_reason(command) == guard.GUARD_PROBE_REASON


@pytest.mark.parametrize(
    "command",
    [
        "python -Bm guard_probe --manifest evil.json",
        "python -um scripts.guard_probe --man evil.json",
        "python -Bum scripts.guard_probe --manifest=evil.json",
        "python3.12 -m guard_probe --manifest evil.json",
        "py3 -mguard_probe --manif evil.json",
        "python -W ignore -m guard_probe --manifest evil.json",
        "python -BW ignore -m guard_probe --manifest evil.json",
        "python -Xdev -m guard_probe --manifest evil.json",
        "python -Ximporttime -m scripts.guard_probe --manifest evil.json",
        "python -Xc -m guard_probe --manifest evil.json",
        "python -BXc -m guard_probe --manifest evil.json",
        "python -Wc -m scripts.guard_probe --manifest evil.json",
        "python -Wdefault:c -m guard_probe --manifest evil.json",
    ],
)
def test_d11_critic_destructive_bash_blocks_python_clustered_abbreviated_and_versioned_flags(
    command: str,
) -> None:
    """Clustered short flags, versioned python binaries, and --man prefixes remain blocked."""
    assert guard.destructive_bash_reason(command) == guard.GUARD_PROBE_REASON


@pytest.mark.parametrize(
    "command",
    [
        'python -c "print(1)"',
        'python -Bc "print(1)"',
        "python script.py -m guard_probe --manifest evil.json",
        "python --help -m not_guard_probe --manifest evil.json",
        "python -W",
        "python -m",
        "python -",
        'python -Ximporttime -c "guard_probe --manifest evil.json"',
        "python -Xm guard_probe --manifest evil.json",
        "python -Wm scripts.guard_probe --manifest evil.json",
    ],
)
def test_d11_critic_destructive_bash_allows_safe_python_invocations(command: str) -> None:
    """Safe python invocations (`-c`, positional scripts, other modules) are not blocked."""
    assert guard.destructive_bash_reason(command) is None


def test_d11_critic_frontmatter_handles_relative_traversal_subcwd_and_case_bash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PreToolUse catches relative traversal into repo and case-varied Bash protected mentions."""
    monkeypatch.chdir(_REPO_ROOT)
    repo_dir = _REPO_ROOT.name
    traversal = f"../{repo_dir}/pyproject.toml"
    assert linter._canonical_protected_path(traversal) == "pyproject.toml"
    assert linter._canonical_protected_path("/completely/outside/pyproject.toml") is None

    monkeypatch.chdir(_REPO_ROOT / "scripts")
    assert linter._canonical_protected_path("../pyproject.toml") == "pyproject.toml"

    # Subdirectory and parent-directory cwd must still resolve repo-root canonical paths.
    monkeypatch.chdir(_REPO_ROOT / "src" / "mangomas")
    assert linter._canonical_protected_path("errors.py") == "src/mangomas/errors.py"
    assert linter._canonical_protected_path("agents/chat.py") is None
    monkeypatch.chdir(_REPO_ROOT.parent)
    assert (
        linter._canonical_protected_path(f"{repo_dir}/src/mangomas/errors.py")
        == "src/mangomas/errors.py"
    )
    # Isolated tmp_path fallback still works via cwd_resolved when outside repo_root.
    (tmp_path / "src" / "mangomas").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert linter._canonical_protected_path("src/mangomas/errors.py") == "src/mangomas/errors.py"

    assert linter._protected_paths_in_command("Get-Content PYPROJECT.TOML") == ["pyproject.toml"]
    assert linter._protected_paths_in_command("cat src\\MANGOMAS\\ERRORS.PY") == [
        "src/mangomas/errors.py"
    ]


def test_d11_guard_probe_runs_via_module_mode() -> None:
    """``python -m scripts.guard_probe --help`` must work without ``PYTHONPATH=scripts``."""
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.guard_probe", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
