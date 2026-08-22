"""Link-integrity contract for the repository's own documentation.

A relative link in a README is the one kind of documentation error that is
purely mechanical: either the target exists or it does not, and a reader
following a dead one learns nothing except that the docs are unmaintained.
Nothing checked them, and the docs cross-reference heavily — `CLAUDE.md`
alone names dozens of paths.

Scope is deliberately narrow, to the two things worth asserting mechanically:

* every relative Markdown link resolves to a file or directory that exists;
* a "further reading"-style list does not name the same target twice, which is
  what happens when someone appends to a list without reading it (it happened
  while writing this file, which is why the check exists).

External `http(s)://` links are **not** checked. Doing so would make the suite
depend on the network and on other people's uptime, turning a third party's
outage into a red build here.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Markdown files whose links are this repository's responsibility. Deliberately
# a list rather than a glob over `**/*.md`: `docs/adr/` and `docs/plans/` are
# dated records that may reference paths which have since been renamed or
# removed, and rewriting them to satisfy a linter would falsify the record.
_LINKED_DOCS: tuple[str, ...] = (
    "README.md",
    "CLAUDE.md",
    "NEXT_STEPS.md",
    "CONTRIBUTING.md",
    "specs/README.md",
    "docs/testing/regression.md",
    "docs/tooling/claude-code-ecosystem.md",
    "docs/architecture/c1-context.md",
    "docs/architecture/c2-container.md",
    "docs/architecture/c3-component.md",
)

# `[text](target)` — capturing the target only.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(?P<target>[^)\s]+)\s*\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")


def _existing_docs() -> list[str]:
    return [name for name in _LINKED_DOCS if (_REPO_ROOT / name).is_file()]


def _relative_targets(text: str) -> list[str]:
    targets = []
    for match in _MD_LINK_RE.finditer(text):
        target = match.group("target")
        if target.startswith(_EXTERNAL_PREFIXES):
            continue
        # Drop any in-page anchor; the file is what must exist.
        targets.append(unquote(target.split("#", 1)[0]))
    return [t for t in targets if t]


def test_linked_docs_exist() -> None:
    """Non-vacuity: the parametrised tests below need real files to read.

    A renamed doc would otherwise silently drop out of `_LINKED_DOCS` and take
    its link checking with it, which is the failure mode this whole file is
    about.
    """
    missing = sorted(name for name in _LINKED_DOCS if not (_REPO_ROOT / name).is_file())
    assert missing == [], (
        f"_LINKED_DOCS names file(s) that do not exist: {missing}. Update the list "
        "if a doc was renamed; remove the entry only if the doc is genuinely gone."
    )


@pytest.mark.parametrize("relpath", _existing_docs())
def test_every_relative_link_resolves(relpath: str) -> None:
    """A relative Markdown link must point at something that exists.

    Resolved against the linking document's own directory, the way a reader's
    Markdown renderer does — so `docs/testing/regression.md` linking
    `../adr/0001-cloud-targets.md` is checked as `docs/adr/0001-cloud-targets.md`,
    not as a repo-root path.
    """
    doc = _REPO_ROOT / relpath
    broken = [
        target
        for target in _relative_targets(doc.read_text(encoding="utf-8"))
        if not (doc.parent / target).exists()
    ]
    assert broken == [], f"{relpath} links to nonexistent target(s): {sorted(set(broken))}"


@pytest.mark.parametrize("relpath", _existing_docs())
def test_no_link_target_is_listed_twice_in_one_bullet_list(relpath: str) -> None:
    """A bullet list must not name the same target twice.

    Not a style preference: a duplicate is the signature of someone appending
    to a "further reading" list without reading it, and it means the list has
    stopped being maintained as a whole. Scoped to a single contiguous run of
    bullets, since the same doc may legitimately be linked from several
    different sections.
    """
    duplicates: list[str] = []
    run: list[str] = []

    def flush() -> None:
        seen: set[str] = set()
        for target in run:
            if target in seen:
                duplicates.append(f"{target} (in a bullet list)")
            seen.add(target)
        run.clear()

    for line in (_REPO_ROOT / relpath).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("- ", "* ")):
            run.extend(_relative_targets(line))
        else:
            flush()
    flush()

    assert duplicates == [], f"{relpath} lists the same target twice: {sorted(set(duplicates))}"
