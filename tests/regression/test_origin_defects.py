"""Regression for origin-defect fixes that have no other home.

POSIX loader sources, GCP exception binding, and sentence-transformers
progress-bar suppression already live in ``tests/rag/test_loader.py``,
``tests/test_secrets_gcp.py``, and
``tests/adapters/embeddings/test_sentence_transformers.py``. This module
keeps the one check those suites do not: a clean child process must still
load repo-root ``sitecustomize.py``, and it must inject **exactly**
``-p no:randomly`` — nothing more.

Why exactness matters, and why asserting mere presence did not (ADR-0030):
``sitecustomize.py`` is imported automatically by any interpreter that can see
the repo root, and it writes ``PYTEST_ADDOPTS``, which pytest applies to every
run. That variable carries ``--cov-fail-under`` and ``-k``. A guard that
asserts only ``"-p no:randomly" in ...`` therefore stays green while an added
``--cov-fail-under=0`` disarms the coverage gate for the whole repository,
CI included — the same fail-open shape ``scripts/check_coverage.py`` names in
its own comments and specs 0020/0021 were spent hunting.
``test_a_stray_injected_token_is_detected`` is the mechanised proof that the
exact-match form actually discriminates (``mango-mutation-proof``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.constants import SITECUSTOMIZE_INJECTED_ADDOPTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SITECUSTOMIZE = "sitecustomize.py"

# Token a tampered sitecustomize would add to silently switch the coverage gate
# off. Any PYTEST_ADDOPTS-settable flag would do; this one is the realistic
# worst case, because the run still reports success.
_GATE_DISABLING_TOKEN = "--cov-fail-under=0"  # noqa: S105 — a pytest flag, not a credential


def _injected_addopts(pythonpath_root: Path) -> str:
    """Return PYTEST_ADDOPTS as a clean child sees it with *pythonpath_root* importable.

    Shared by both directions of the proof so the honest tree and the tampered
    one are measured by identical machinery — a copy would let them drift and
    the mutation test would stop meaning anything.
    """
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = str(pythonpath_root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PYTEST_ADDOPTS', ''))"],
        cwd=pythonpath_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_sitecustomize_protects_pytest_environment() -> None:
    """A clean child auto-loads ``sitecustomize.py`` and injects exactly the allowed tokens."""
    injected = _injected_addopts(_REPO_ROOT)

    assert set(injected.split()) == set(SITECUSTOMIZE_INJECTED_ADDOPTS), (
        f"sitecustomize.py injected {injected!r} into PYTEST_ADDOPTS; only "
        f"{sorted(SITECUSTOMIZE_INJECTED_ADDOPTS)} is allowed. An extra token here applies "
        "to every pytest run in the repo, CI included."
    )


def test_a_stray_injected_token_is_detected(tmp_path: Path) -> None:
    """The exact-match guard must go red when sitecustomize is tampered with.

    Mechanised mutation proof: copy the real ``sitecustomize.py`` into a
    throwaway tree, add a gate-disabling token to what it injects, and assert
    the *same* comparison the test above makes now rejects it. Run against a
    copy rather than the repo file so an interrupted run can never leave the
    tree tampered — the restore step that a manual mutation loop depends on is
    the step that gets skipped.
    """
    shutil.copy(_REPO_ROOT / _SITECUSTOMIZE, tmp_path / _SITECUSTOMIZE)
    tampered = tmp_path / _SITECUSTOMIZE
    source = tampered.read_text(encoding="utf-8")
    mutated = source.replace(
        'f"-p no:randomly {existing}"',
        f'f"-p no:randomly {_GATE_DISABLING_TOKEN} {{existing}}"',
    )
    assert mutated != source, "mutation did not apply; sitecustomize.py's injection changed shape"
    tampered.write_text(mutated, encoding="utf-8")

    injected = _injected_addopts(tmp_path)

    assert _GATE_DISABLING_TOKEN in injected.split(), "the tampered copy did not take effect"
    assert set(injected.split()) != set(SITECUSTOMIZE_INJECTED_ADDOPTS), (
        "the exact-match guard failed to detect a gate-disabling token — it is "
        "back to proving nothing"
    )
