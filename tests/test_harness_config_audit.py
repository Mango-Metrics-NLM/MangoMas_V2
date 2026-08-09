"""Tests for ``scripts/harness_config_audit.py`` (ADR-0021 / spec-0017).

In-process tests exercise the module's functions directly (fast, easy to
assert against); a subprocess test proves the whole hook degrades gracefully
even in an interpreter without ``mangomas`` installed, matching the same
rigor applied to the ``lint_agent_frontmatter.py --hook`` fixes.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Final

import pytest

from mangomas import config as config_module
from mangomas.config import DEFAULT_HARNESS_CONFIG_AUDIT_MODE
from tests._script_loader import load_script_module

hook = load_script_module("harness_config_audit.py")

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCRIPT_PATH: Final[Path] = _REPO_ROOT / "scripts" / "harness_config_audit.py"

# tests/conftest.py's autouse `_clear_settings_cache` already clears
# `get_settings.cache_clear()` before every test; tests below that change an
# env var mid-test still call it again inline so the new value is observed.
#
# Accessed as `config_module.get_settings`, never imported by name: this
# module is collected (imported once) well before any test runs, but
# `tests/test_config.py::test_module_reimport_safe` calls
# `importlib.reload(mangomas.config)` during the *run* phase, which rebinds
# `mangomas.config.get_settings` to a brand-new function with its own
# separate `lru_cache`. A name-imported `get_settings` here would go stale
# at that point — `.cache_clear()` on it would no longer affect what
# `harness_config_audit.py`'s own `from mangomas.config import get_settings`
# (evaluated fresh, at call time) actually resolves to. Module-attribute
# access always reads whatever is currently in `sys.modules`.


def test_extract_source_tries_each_candidate_key() -> None:
    assert hook._extract_source({"source": "project_settings"}) == "project_settings"
    assert hook._extract_source({"config_source": "local_settings"}) == "local_settings"
    assert hook._extract_source({"hook_event_source": "skills"}) == "skills"


def test_extract_source_returns_none_when_no_candidate_key_matches() -> None:
    assert hook._extract_source({"unrelated": "value"}) is None


def test_extract_source_debug_log_omits_raw_payload_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression guard: a ConfigChange payload can plausibly carry config
    content or env values in fields other than the recognized source keys.
    The DEBUG log on a miss must carry only the payload's key names, never
    the values, so enabling DEBUG logging can't leak secrets."""
    caplog.set_level(logging.DEBUG, logger=hook.__name__)
    hook._extract_source({"unrelated": "super-secret-value", "another": "also-secret"})

    records = [r for r in caplog.records if "no recognized source key" in r.message]
    assert len(records) == 1
    assert not hasattr(records[0], "payload")
    assert getattr(records[0], "payload_keys", None) == ["another", "unrelated"]


def test_read_stdin_payload_parses_valid_json() -> None:
    sys_stdin_backup = sys.stdin
    try:
        sys.stdin = StringIO(json.dumps({"source": "project_settings"}))
        assert hook._read_stdin_payload() == {"source": "project_settings"}
    finally:
        sys.stdin = sys_stdin_backup


def test_read_stdin_payload_returns_empty_dict_on_malformed_json() -> None:
    sys_stdin_backup = sys.stdin
    try:
        sys.stdin = StringIO("not json")
        assert hook._read_stdin_payload() == {}
    finally:
        sys.stdin = sys_stdin_backup


def test_read_stdin_payload_returns_empty_dict_on_empty_stdin() -> None:
    sys_stdin_backup = sys.stdin
    try:
        sys.stdin = StringIO("")
        assert hook._read_stdin_payload() == {}
    finally:
        sys.stdin = sys_stdin_backup


def test_resolve_mode_reads_the_real_settings_default() -> None:
    assert hook._resolve_mode() == DEFAULT_HARNESS_CONFIG_AUDIT_MODE


def test_resolve_mode_reflects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "audit")
    config_module.get_settings.cache_clear()
    assert hook._resolve_mode() == "audit"


def test_main_off_mode_allows_and_exits_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"source": "project_settings"})))
    assert hook.main() == hook.EXIT_OK


def test_main_block_mode_blocks_a_governed_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "block")
    config_module.get_settings.cache_clear()
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"source": "local_settings"})))
    assert hook.main() == hook.EXIT_BLOCK


def test_main_block_mode_never_blocks_policy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_HARNESS__CONFIG_AUDIT_MODE", "block")
    config_module.get_settings.cache_clear()
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"source": "policy_settings"})))
    assert hook.main() == hook.EXIT_OK


# ── Subprocess-level: real invocation shape, real degrade-gracefully proof ──


def test_subprocess_default_mode_off_exits_ok() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT_PATH)],
        input=json.dumps({"source": "project_settings"}),
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == hook.EXIT_OK


def test_subprocess_runs_without_mangomas_installed(tmp_path: Path) -> None:
    """Regression guard mirroring the PreToolUse/PostToolUse hook fixes: this
    hook must never crash a session even when `mangomas` (and therefore
    pydantic) cannot be imported at all — it must degrade to the documented
    default mode ("off") rather than raising."""
    stub_dir = tmp_path / "stub_site_packages"
    stub_dir.mkdir()
    (stub_dir / "pydantic.py").write_text(
        "raise ImportError('stub: pydantic intentionally unavailable for this test')\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(stub_dir) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT_PATH)],
        input=json.dumps({"source": "project_settings"}),
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == hook.EXIT_OK
