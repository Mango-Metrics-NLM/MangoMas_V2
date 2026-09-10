"""Regression for origin-defect fixes that have no other home.

POSIX loader sources, GCP exception binding, and sentence-transformers
progress-bar suppression already live in ``tests/rag/test_loader.py``,
``tests/test_secrets_gcp.py``, and
``tests/adapters/embeddings/test_sentence_transformers.py``. This module
keeps the one check those suites do not: a clean child process must still
load repo-root ``sitecustomize.py`` and inject ``-p no:randomly``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sitecustomize_protects_pytest_environment() -> None:
    """A clean child process auto-loads ``sitecustomize.py`` from the repo root."""
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PYTEST_ADDOPTS', ''))"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "-p no:randomly" in result.stdout.strip()
