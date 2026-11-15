"""Shared reader for the repository ``Makefile`` — the sibling of ``_workflows.py``.

Contract suites compare workflow steps, the environment registry and docs
against Makefile variables and recipes. Before this module the two parsers
below lived privately in ``test_ci_make_parity.py``, so a second suite needing
them would have had to copy the regexes (and drift from them).
"""

from __future__ import annotations

import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def text() -> str:
    """The Makefile's full text."""
    return MAKEFILE.read_text(encoding="utf-8")


def target_body(target: str) -> str:
    """Return the recipe for *target*: its rule line up to the next unindented line or EOF."""
    pattern = re.compile(rf"^{re.escape(target)}:.*?(?=\n\S|\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text())
    assert match is not None, f"Makefile target {target!r} not found"
    return match.group(0)


def variable(name: str) -> str:
    """Return the value of a ``NAME ?= value`` (or ``NAME = value``) assignment."""
    match = re.search(rf"^{re.escape(name)}\s*\??=\s*(.+)$", text(), re.M)
    assert match is not None, f"Makefile does not define {name}"
    value = match.group(1).strip()
    # Only single-line values are supported; a `\`-continued one would be
    # silently truncated to its first line.
    assert not value.endswith("\\"), f"{name} is a multi-line value; read it another way"
    return value


def defined_targets() -> set[str]:
    """Return every explicit rule target declared in the repository Makefile."""
    targets: set[str] = set()
    for line in text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
        if match and match.group(1) != ".PHONY":
            targets.add(match.group(1))
    return targets


def phony_targets() -> set[str]:
    """Return every target listed in ``.PHONY:`` declarations in the Makefile."""
    lines = text().splitlines()
    phony: set[str] = set()
    idx = 0
    while idx < len(lines):
        if lines[idx].startswith(".PHONY:"):
            buf = lines[idx][len(".PHONY:") :]
            while buf.rstrip().endswith("\\") and idx + 1 < len(lines):
                buf = buf.rstrip()[:-1] + " " + lines[idx + 1]
                idx += 1
            phony.update(buf.split())
        idx += 1
    return phony
