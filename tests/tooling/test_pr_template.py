"""The PR template must teach the trailer contract, not undermine it.

Two commits in this repository's history carry ``BREAKING-CHANGE: none``.
``scripts/_governance.py::find_marker_scopes`` reads any marker whose first
token is not a protected path as *unscoped* — a blanket approval of every
protected path in the range — so a placeholder meant to say "nothing to
approve" approved everything. The template is where a contributor learns the
trailer, so it must say this, and must never itself suggest the placeholder.
"""

from __future__ import annotations

import re
from pathlib import Path

_TEMPLATE = Path(__file__).resolve().parents[2] / ".github" / "PULL_REQUEST_TEMPLATE.md"
# A marker line as the gate would see it: line start, optional indent, the
# marker, a colon, then a first token that is not a path.
# `(?=\s|$)` rather than `\b`: a word boundary never follows a bare `-`, so the
# first version let `BREAKING-CHANGE: -` through (Copilot review, PR #123).
_PLACEHOLDER_MARKER_RE = re.compile(
    r"^[ \t]*BREAKING-CHANGE:[ \t]*(?:none|n/a|-)(?=\s|$)", re.MULTILINE
)


def _text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def test_template_has_a_protected_paths_section() -> None:
    text = _text()
    assert "## Protected paths" in text
    assert "BREAKING-CHANGE: <path>" in text, "the scoped trailer form must be shown"
    assert "unscoped" in text, "the template must say what a non-path first token does"
    assert re.search(r"merge\s+commit", text), (
        "squash-merge drops the trailer out of the gate's range"
    )


def test_template_never_suggests_a_placeholder_marker() -> None:
    assert _PLACEHOLDER_MARKER_RE.search(_text()) is None


def test_the_placeholder_pattern_catches_the_historical_form() -> None:
    """Mutation proof for the guard: the exact text seen in commits 7cbc010 / ffc3072."""
    assert _PLACEHOLDER_MARKER_RE.search("BREAKING-CHANGE: none\n") is not None
    assert _PLACEHOLDER_MARKER_RE.search("  BREAKING-CHANGE: n/a") is not None
    assert _PLACEHOLDER_MARKER_RE.search("BREAKING-CHANGE: -\n") is not None
    assert _PLACEHOLDER_MARKER_RE.search("BREAKING-CHANGE: -") is not None
    # A path that merely *starts* like a placeholder is not one.
    assert _PLACEHOLDER_MARKER_RE.search("BREAKING-CHANGE: none-of-these/file.py — why") is None
    assert _PLACEHOLDER_MARKER_RE.search("BREAKING-CHANGE: src/mangomas/errors.py — why") is None
