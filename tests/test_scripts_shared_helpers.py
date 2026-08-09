"""Direct unit tests for ``scripts/_governance.py`` and ``scripts/_stdin_json.py``.

Both are shared by two consumers each (``check_protected_paths.py`` +
``lint_agent_frontmatter.py`` for governance;  ``lint_agent_frontmatter.py``
+ ``harness_config_audit.py`` for stdin-JSON reading), but each consumer's
own test suite only exercises the branches its own error-handling wraps
around them — this file tests the shared modules on their own terms so
every branch has direct coverage, not just incidental coverage through one
caller's happy/unhappy path.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

import pytest

from tests._script_loader import load_script_module

governance = load_script_module("_governance.py")
stdin_json = load_script_module("_stdin_json.py")

_VALID_TOML = """
[tool.mangomas.governance]
protected_paths = ["src/mangomas/core/agent.py"]
breaking_change_marker_aliases = ["BREAKING-CHANGE"]
"""


# ── _governance.load_governance ────────────────────────────────────────────


def test_load_governance_reads_valid_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_VALID_TOML, encoding="utf-8")
    protected, aliases = governance.load_governance(pyproject)
    assert protected == frozenset({"src/mangomas/core/agent.py"})
    assert aliases == frozenset({"BREAKING-CHANGE"})


def test_load_governance_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(governance.GovernanceLoadError, match="cannot read"):
        governance.load_governance(tmp_path / "does-not-exist.toml")


def test_load_governance_raises_on_malformed_toml_syntax(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("not [ valid toml", encoding="utf-8")
    with pytest.raises(governance.GovernanceLoadError, match="malformed TOML"):
        governance.load_governance(pyproject)


def test_load_governance_raises_on_missing_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.mangomas]\n# no governance table\n", encoding="utf-8")
    with pytest.raises(governance.GovernanceLoadError, match="missing or malformed key"):
        governance.load_governance(pyproject)


def test_load_governance_raises_on_empty_protected_paths(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.mangomas.governance]\nprotected_paths = []\n"
        'breaking_change_marker_aliases = ["BREAKING-CHANGE"]\n',
        encoding="utf-8",
    )
    with pytest.raises(governance.GovernanceLoadError, match="non-empty"):
        governance.load_governance(pyproject)


# ── _governance.find_breaking_change_marker ────────────────────────────────

_ALIASES = frozenset({"BREAKING-CHANGE", "# approved-breaking-change"})


@pytest.mark.parametrize(
    ("text", "expect_match"),
    [
        ("BREAKING-CHANGE: widened protocol", True),
        ("BREAKING-CHANGE", True),
        ("# approved-breaking-change", True),
        ("+BREAKING-CHANGE: widened protocol", True),  # added diff line
        ("-BREAKING-CHANGE: widened protocol", False),  # deleted diff line
        ("This is NOT a BREAKING-CHANGE, just a cleanup.", False),
        ("just an ordinary message", False),
        ("BREAKING-CHANGEFOO: not the marker", False),
    ],
)
def test_find_breaking_change_marker(text: str, expect_match: bool) -> None:
    result = governance.find_breaking_change_marker(text, _ALIASES)
    assert (result is not None) is expect_match


# ── _stdin_json.read_json_payload ──────────────────────────────────────────


def test_read_json_payload_parses_a_valid_object() -> None:
    assert stdin_json.read_json_payload(StringIO('{"a": 1}')) == {"a": 1}


def test_read_json_payload_returns_empty_dict_on_empty_stream() -> None:
    assert stdin_json.read_json_payload(StringIO("")) == {}


def test_read_json_payload_returns_empty_dict_on_malformed_json_no_logger() -> None:
    assert stdin_json.read_json_payload(StringIO("not json")) == {}


def test_read_json_payload_returns_empty_dict_on_non_dict_json_no_logger() -> None:
    assert stdin_json.read_json_payload(StringIO("[1, 2, 3]")) == {}


def test_read_json_payload_logs_a_warning_on_malformed_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_scripts_shared_helpers.malformed")
    caplog.set_level(logging.WARNING, logger=logger.name)
    assert stdin_json.read_json_payload(StringIO("not json"), logger=logger) == {}
    assert any("malformed JSON" in r.message for r in caplog.records)


def test_read_json_payload_logs_a_warning_on_non_dict_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_scripts_shared_helpers.non_dict")
    caplog.set_level(logging.WARNING, logger=logger.name)
    assert stdin_json.read_json_payload(StringIO("[1, 2, 3]"), logger=logger) == {}
    records = [r for r in caplog.records if "not an object" in r.message]
    assert len(records) == 1
    assert getattr(records[0], "payload_type", None) == "list"
