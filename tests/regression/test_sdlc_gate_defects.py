"""Regression tests for defects found during the 2026-09-17 SDLC quality gate pass.

Three defects triaged:

1. **RAG pipeline POSIX path mismatch on Windows** (``test_pipeline.py:168``):
   ``loader.py`` canonicalises ``RawDoc.source`` via ``Path.as_posix()``, but the
   test compared against ``str(f)`` which uses OS-native backslashes on Windows.

2. **ADR-0031 numbering collision** (``docs/adr/``):
   Two independent branches both allocated number 0031 — ``0031-durable-turn-record``
   and ``0031-structured-acceptance-predicates``. The later was renumbered to 0034.

3. **Ruff toolchain pin desync** (``.pre-commit-config.yaml``):
   ``pyproject.toml`` pinned ``ruff==0.16.6`` (via Dependabot PR #57), but the
   pre-commit hook rev stayed at ``v0.16.0``. ``test_toolchain_pin_parity`` caught
   the drift; fix was a rev bump.

Defects 1 and 3 are structurally verified by the tests that caught them.
This module adds a **discrimination test** for defect 1 (the POSIX path pattern)
to ensure the cross-platform path handling is never accidentally reverted, and a
cross-reference integrity guard for defect 2.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from pathlib import Path

import pytest

from mangomas.rag.loader import load_documents

# ── Repo root (same derivation as test_origin_defects.py) ────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Defect 1: POSIX source paths are cross-platform ──────────────────────────


class TestPosixPathCanonicalization:
    """Guard against the path separator mismatch that broke on Windows.

    The RAG loader stores ``RawDoc.source`` as POSIX paths via
    ``Path.as_posix()``. Any test or consumer comparing against
    ``str(path)`` instead of ``path.as_posix()`` will fail on Windows where
    ``str()`` produces backslashes.
    """

    def test_loader_source_uses_forward_slashes(self, tmp_path: Path) -> None:
        """``load_documents`` must store source paths with forward slashes."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "test.txt").write_text("hello", encoding="utf-8")
        docs = asyncio.run(load_documents(str(tmp_path)))
        assert len(docs) == 1
        assert docs[0].source == "sub/test.txt", (
            f"Expected 'sub/test.txt', got {docs[0].source!r}. "
            "RawDoc.source must always be a forward-slash POSIX path."
        )


# ── Defect 2: ADR / spec numbering uniqueness ────────────────────────────────

_NUMBERED_STEM_RE = re.compile(r"^(?P<number>\d{4})-")
_ADR_DIR = _REPO_ROOT / "docs" / "adr"
_SPECS_DIR = _REPO_ROOT / "specs"


class TestDecisionRecordIntegrity:
    """Guard that the ADR-0031 numbering collision stays fixed.

    Two branches independently allocated ADR-0031. The structured-acceptance-
    predicates document was renumbered to 0034. These tests ensure the fix
    holds and no new collisions appear.
    """

    def test_adr_0034_exists_after_renumber(self) -> None:
        """The renumbered ADR file must exist at its new location."""
        target = _ADR_DIR / "0034-structured-acceptance-predicates.md"
        assert target.exists(), (
            f"{target.name} is missing — the ADR-0031 collision fix may have been reverted."
        )

    def test_no_duplicate_0031_adrs(self) -> None:
        """Only one ADR may claim number 0031."""
        files_0031 = [p.name for p in _ADR_DIR.glob("0031-*.md")]
        assert len(files_0031) == 1, (
            f"Expected exactly 1 ADR-0031 file, found {len(files_0031)}: "
            f"{files_0031}. The numbering collision has regressed."
        )
        assert files_0031[0] == "0031-durable-turn-record.md"

    @pytest.mark.parametrize("directory", [_ADR_DIR, _SPECS_DIR], ids=["adr", "specs"])
    def test_no_numbering_collisions(self, directory: Path) -> None:
        """No two files in a decision-record directory share a number prefix."""
        by_number: dict[str, list[str]] = defaultdict(list)
        for path in sorted(directory.glob("*.md")):
            match = _NUMBERED_STEM_RE.match(path.stem)
            if match is not None:
                by_number[match.group("number")].append(path.name)
        collisions = {n: names for n, names in by_number.items() if len(names) > 1}
        assert not collisions, f"{directory.name} contains numbering collisions: {collisions}"


# ── Defect 3: ruff pre-commit lockstep ───────────────────────────────────────


class TestToolchainLockstep:
    """Guard that the ruff pin desync stays fixed.

    The structural test ``test_toolchain_pin_parity`` already enforces parity
    between pyproject.toml and .pre-commit-config.yaml. This regression test
    ensures the specific v0.16.0 → v0.16.6 fix is not reverted to the old value.
    """

    def test_ruff_precommit_rev_is_not_stale(self) -> None:
        """The pre-commit ruff rev must not be the known-stale v0.16.0."""
        config = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        # A precise negative assertion: we know v0.16.0 was the broken value.
        assert "rev: v0.16.0" not in config, (
            ".pre-commit-config.yaml still has ruff rev v0.16.0 — "
            "the toolchain lockstep fix has regressed."
        )
