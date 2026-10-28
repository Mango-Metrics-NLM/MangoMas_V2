"""Shared reader for ``pyproject.toml``'s extras table — sibling of ``_makefile.py``.

``cloud = ["mangomas[vertex,postgres,gcp]"]`` is a meta-extra: the *names* it
expands to are what the manifest provider contract compares against each
selected provider's extra, while ``test_lockfile_freshness.py`` compares the
*distributions*. Both read the same table; this keeps the self-reference
expansion in one place for the name-level question.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

#: ``mangomas[vertex,postgres]`` inside an extra — a self-reference.
_SELF_EXTRA_RE = re.compile(r"^\s*mangomas\s*\[(?P<extras>[^\]]+)\]\s*$")


def extras_table(*, text: str | None = None) -> dict[str, list[str]]:
    raw = PYPROJECT.read_text(encoding="utf-8") if text is None else text
    table: dict[str, list[str]] = tomllib.loads(raw)["project"]["optional-dependencies"]
    return table


def extra_closure(extra: str, *, text: str | None = None) -> set[str]:
    """Return *extra* plus every extra it reaches through ``mangomas[...]`` self-references.

    ``extra_closure("cloud")`` is ``{"cloud", "vertex", "postgres", "gcp"}``.
    An unknown extra raises rather than resolving to the empty set, so a typo
    in a Dockerfile build arg cannot read as "installs nothing, needs nothing".
    """
    table = extras_table(text=text)
    seen: set[str] = set()
    pending = [extra]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        assert name in table, f"pyproject declares no extra named {name!r}"
        seen.add(name)
        for requirement in table[name]:
            match = _SELF_EXTRA_RE.match(requirement)
            if match is not None:
                pending.extend(part.strip() for part in match.group("extras").split(","))
    return seen
