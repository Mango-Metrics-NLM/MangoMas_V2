"""Live docs must not teach vanished composition/middleware module files.

``src/mangomas/composition.py`` and ``src/mangomas/api/middleware.py`` were
split into packages (ADR-0019). Agents and reader docs that still name those
paths as files send the next session to a missing file. Historical ledgers
(CHANGELOG, ADRs, plans, specs, NEXT_STEPS) may keep the old path as a
dated record and are excluded here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_VANISHED_PATHS: tuple[str, ...] = (
    "src/mangomas/composition.py",
    "src/mangomas/api/middleware.py",
)

_LIVE_FILES: tuple[str, ...] = (
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "docs/architecture/c1-context.md",
    "docs/architecture/c2-container.md",
    "docs/architecture/c3-component.md",
    "docs/architecture/c4-code.md",
    "docs/architecture/cloud-providers.md",
)


def _live_paths() -> list[Path]:
    paths = [_REPO_ROOT / rel for rel in _LIVE_FILES]
    paths.extend(sorted((_REPO_ROOT / "docs" / "adapters").glob("*.md")))
    paths.extend(sorted((_REPO_ROOT / ".claude" / "agents").glob("mango-*.md")))
    paths.extend(sorted((_REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md")))
    return paths


def test_live_path_ledger_files_exist() -> None:
    """The denylist is only meaningful if every named live file is present."""
    missing = [rel for rel in _LIVE_FILES if not (_REPO_ROOT / rel).is_file()]
    assert missing == [], f"live-path ledger names missing file(s): {missing}"


@pytest.mark.parametrize("vanished", _VANISHED_PATHS, ids=_VANISHED_PATHS)
def test_live_docs_do_not_cite_vanished_module_files(vanished: str) -> None:
    """A current-path citation of a split-away module file is a ledger defect."""
    hits: list[str] = []
    for path in _live_paths():
        text = path.read_text(encoding="utf-8")
        if vanished in text:
            rel = path.relative_to(_REPO_ROOT).as_posix()
            hits.append(rel)
    assert hits == [], (
        f"{vanished!r} is cited as a current path in {hits}. Point those "
        "docs at the package (composition/ or api/middleware/) instead. "
        "Dated records in CHANGELOG, docs/adr, docs/plans, specs, and "
        "NEXT_STEPS are excluded on purpose."
    )
