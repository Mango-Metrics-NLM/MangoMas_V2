"""Unit tests for ``scripts/guard_probe.py`` (spec-0033 R1-R5).

The shipped manifest is checked for snippet uniqueness only. Mutations run
against a temporary git repo and a stub gate, never against this checkout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests._script_loader import load_script_module

probe = load_script_module("guard_probe.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path, *, newline: str = "\n") -> None:
    _git(root, "init")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "user.email", "probe@example.com")
    _git(root, "config", "user.name", "probe")
    (root / "target.txt").write_bytes(f"prefix FIXED suffix{newline}".encode())
    (root / "gate.py").write_text(
        "import pathlib, sys\n"
        "text = pathlib.Path('target.txt').read_text(encoding='utf-8')\n"
        "sys.exit(1 if 'DEFECT' in text else 0)\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")


def _manifest(root: Path, **overrides: object) -> Path:
    entry: dict[str, object] = {
        "id": "MUT-STUB",
        "path": "target.txt",
        "fixed": "FIXED",
        "defect": "DEFECT",
        "gate": ["{python}", "gate.py"],
    }
    entry.update(overrides)
    path = root.parent / "guard-probe-manifest.json"
    path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
    return path


def test_a_discriminating_mutation_restores_the_original_bytes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    original = (tmp_path / "target.txt").read_bytes()
    probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=False)
    assert (tmp_path / "target.txt").read_bytes() == original
    assert probe.content_sha256(original) == probe.content_sha256(
        (tmp_path / "target.txt").read_bytes()
    )


def test_a_disarmed_gate_fails_and_still_restores(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    original = (tmp_path / "target.txt").read_bytes()
    (tmp_path / "gate.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="stayed green"):
        probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=True)
    assert (tmp_path / "target.txt").read_bytes() == original


def test_gate_exception_restores_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    original = (tmp_path / "target.txt").read_bytes()

    def _boom(_gate: tuple[str, ...], _cwd: Path) -> int:
        raise RuntimeError("gate crashed")

    monkeypatch.setattr(probe, "run_gate", _boom)
    entry = probe.load_manifest(_manifest(tmp_path))[0]
    with pytest.raises(RuntimeError, match="gate crashed"):
        probe.probe_entry(tmp_path, entry)
    assert (tmp_path / "target.txt").read_bytes() == original


def test_crlf_files_round_trip(tmp_path: Path) -> None:
    _init_repo(tmp_path, newline="\r\n")
    original = (tmp_path / "target.txt").read_bytes()
    assert b"\r\n" in original
    probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=False)
    assert (tmp_path / "target.txt").read_bytes() == original


def test_a_dirty_tree_is_refused(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="dirty"):
        probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=False)
    assert b"DEFECT" not in (tmp_path / "target.txt").read_bytes()


def test_allow_dirty_runs_on_a_dirty_tree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=True)


def test_an_empty_manifest_fails(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text('{"entries": []}\n', encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="no entries"):
        probe.load_manifest(path)


def test_an_unreadable_manifest_fails(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(probe.ProbeError, match="unreadable"):
        probe.load_manifest(missing)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="unreadable"):
        probe.load_manifest(broken)


def test_a_malformed_entry_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"entries": [{"id": "x"}]}\n', encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="missing a field"):
        probe.load_manifest(path)
    path.write_text('{"entries": ["nope"]}\n', encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="not an object"):
        probe.load_manifest(path)


def test_a_snippet_that_is_not_unique_does_not_write(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    original = (tmp_path / "target.txt").read_bytes()
    with pytest.raises(probe.ProbeError, match="found 0 times"):
        probe.run_probe(tmp_path, _manifest(tmp_path, fixed="ABSENT"), allow_dirty=False)
    assert (tmp_path / "target.txt").read_bytes() == original


def test_apply_once_rejects_a_repeated_snippet() -> None:
    with pytest.raises(probe.ProbeError, match="found 2 times"):
        probe.apply_once("FIXED and FIXED\n", "FIXED", "DEFECT")


def test_resolve_command_rewrites_the_python_token() -> None:
    assert probe.resolve_command(("{python}", "-m"))[0] == probe.sys.executable
    assert probe.resolve_command(("python", "-m"))[0] == probe.sys.executable
    assert probe.resolve_command(("other",)) == ["other"]


def test_git_status_failure_is_a_probe_error(tmp_path: Path) -> None:
    with pytest.raises(probe.ProbeError, match="git status failed"):
        probe.git_status_porcelain(tmp_path)


def test_main_returns_probe_failure_for_a_missing_manifest(tmp_path: Path) -> None:
    code = probe.main(["--manifest", str(tmp_path / "nope.json"), "--root", str(tmp_path)])
    assert code == probe.EXIT_PROBE


def test_main_returns_ok_for_a_stub_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    code = probe.main(
        ["--manifest", str(_manifest(tmp_path)), "--root", str(tmp_path), "--allow-dirty"]
    )
    assert code == probe.EXIT_OK


def test_a_gate_that_stays_red_after_restore_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "gate.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    with pytest.raises(probe.ProbeError, match="stayed red"):
        probe.run_probe(tmp_path, _manifest(tmp_path), allow_dirty=True)
    assert b"DEFECT" not in (tmp_path / "target.txt").read_bytes()


def test_an_empty_gate_command_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty-gate.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "x",
                        "path": "target.txt",
                        "fixed": "FIXED",
                        "defect": "DEFECT",
                        "gate": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(probe.ProbeError, match="empty"):
        probe.load_manifest(path)


def test_shipped_manifest_snippets_each_occur_once() -> None:
    """The real manifest must still match the tree, or the probe cannot run."""
    entries = probe.load_manifest(probe._default_manifest())
    assert len(entries) == 10
    for entry in entries:
        text = (probe._repo_root() / entry.path).read_text(encoding="utf-8")
        assert probe._to_lf(text).count(probe._to_lf(entry.fixed)) == 1, entry.entry_id
    assert probe._repo_root() == _REPO_ROOT
