"""Live docs must not teach module files that ADR-0019 turned into packages.

``composition.py``, ``api/middleware.py`` and ``config.py`` were each split into
a package of the same name. A doc that still names one as a file sends the next
session to something that is not there.

Two citation shapes, and the original ledger only caught one. It substring-
matched the **full path** (``src/mangomas/composition.py``), which live docs do
not actually use — they write the **bare basename** (``composition.py``). Both
recorded decompositions were therefore uncaught, and two real defects survived
every gate: ``.github/copilot-instructions.md`` named ``config.py`` as the home
of ``Settings``, and ``docs/workflow/graphs.md`` listed ``composition.py``
among unchanged current files.

Basename *existence* is not the test. ``tests/constants/config.py`` exists, so
"does a file with this name exist anywhere?" passes on the very citation that is
wrong. Vanished-ness is the signal.

Historical ledgers (CHANGELOG, ADRs, plans, analyses, specs, NEXT_STEPS) are
correct as of their date and are excluded — see ``_corpus.is_dated_record``.
"""

from __future__ import annotations

import pytest

from tests.constants.corpus import (
    LIVE_DOC_ROOT_RELPATHS,
    VANISHED_MODULES,
    VANISHED_PATHS,
    VanishedModule,
)
from tests.tooling._corpus import (
    REPO_ROOT,
    cites_basename,
    find_basename_citations,
    iter_backticked_tokens,
    live_doc_paths,
)

_MODULE_IDS = [module.basename for module in VANISHED_MODULES]


def test_live_path_ledger_is_non_empty() -> None:
    """A ledger that walks nothing passes vacuously.

    The denominator is derived from globs now, so an empty match set is a real
    possibility (a moved directory, a typo'd glob) and would turn every check
    below into a green no-op.
    """
    assert len(live_doc_paths()) >= len(VANISHED_MODULES), (
        f"live-doc denominator collapsed to {len(live_doc_paths())} files; "
        "check LIVE_DOC_GLOBS / LIVE_DOC_ROOT_RELPATHS in tests.constants.corpus"
    )


def test_live_doc_denominator_resolves() -> None:
    """Every individually-named live doc must exist.

    The glob-derived members cannot be missing by construction; the hand-named
    ones can, and a stale entry there silently shrinks what is policed.
    """
    missing = [relpath for relpath in LIVE_DOC_ROOT_RELPATHS if not (REPO_ROOT / relpath).is_file()]
    assert missing == [], f"live-doc ledger names missing file(s): {missing}"


@pytest.mark.parametrize("vanished", VANISHED_PATHS, ids=VANISHED_PATHS)
def test_live_docs_do_not_cite_vanished_module_files(vanished: str) -> None:
    """A current-path citation of a split-away module file is a ledger defect."""
    hits: list[str] = []
    for path in live_doc_paths():
        if vanished in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(REPO_ROOT).as_posix())
    assert hits == [], (
        f"{vanished!r} is cited as a current path in {hits}. Point those docs at "
        "the package instead. Dated records are excluded on purpose."
    )


@pytest.mark.parametrize("module", VANISHED_MODULES, ids=_MODULE_IDS)
def test_live_docs_do_not_cite_a_vanished_basename(module: VanishedModule) -> None:
    """The citation shape docs actually use, which the full-path check misses.

    Narrative exemptions are scoped to a (doc, module) pair: a skill that
    explains the ``composition.py`` decomposition may name it, and that licenses
    nothing about ``config.py``.
    """
    offenders = [
        str(citation)
        for citation in find_basename_citations(module.basename)
        if citation.relpath not in module.narrative_exemptions
    ]
    assert offenders == [], (
        f"{module.basename!r} no longer exists as a module file — it is now "
        f"{module.replacement}. Cited as current in:\n  "
        + "\n  ".join(offenders)
        + "\n\nFix the citation. If the doc genuinely narrates the "
        "decomposition itself, add its path to that module's "
        "narrative_exemptions in tests.constants.corpus."
    )


@pytest.mark.parametrize("module", VANISHED_MODULES, ids=_MODULE_IDS)
def test_narrative_exemptions_are_still_earned(module: VanishedModule) -> None:
    """A stale exemption is a hole that nobody notices opening.

    An exemption list only stays honest if entries cost something. Requiring
    each one to still be exercised means a doc that stopped narrating the
    decomposition loses its licence automatically, instead of keeping a
    standing permission to reintroduce the defect.
    """
    unearned = sorted(
        relpath for relpath in module.narrative_exemptions if not _cites(relpath, module.basename)
    )
    assert unearned == [], (
        f"these docs are exempted for {module.basename!r} but no longer cite "
        f"it: {unearned}. Drop them from narrative_exemptions — a licence "
        "nothing uses is a hole waiting for the next edit."
    )


def _cites(relpath: str, basename: str) -> bool:
    path = REPO_ROOT / relpath
    if not path.is_file():
        return False
    return any(
        cites_basename(token, basename)
        for _, token in iter_backticked_tokens(path.read_text(encoding="utf-8"))
    )
