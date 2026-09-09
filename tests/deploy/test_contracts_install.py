"""Prove mango_contracts is a real runtime install, not just pytest pythonpath.

``pyproject.toml`` keeps the sibling package out of ``dependencies`` so
``requirements.lock`` stays an ``==`` pin of PyPI names. ``make install``,
CI, and the Docker dual-wheel build are what put ``mango_contracts`` on
``sys.path`` for flag-on emission.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mango_contracts.cognitive_signal import SCHEMA_VERSION

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mango_contracts_imports_without_pytest_pythonpath() -> None:
    """A clean interpreter must import the envelope after ``make install``."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from mango_contracts.cognitive_signal import SCHEMA_VERSION; print(SCHEMA_VERSION)",
        ],
        env=env,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "import mango_contracts failed without pytest pythonpath. "
        "Run `make install` (pip install -e ./mango-integration-contracts).\n"
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )
    assert result.stdout.strip() == SCHEMA_VERSION
