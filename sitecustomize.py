"""Project runtime customization hooks.

Neutralizes ambient third-party pytest plugins (specifically pytest-randomly)
whose legacy 32-bit integer seeding crashes on NumPy 2.x.
"""

from __future__ import annotations

import os

# Ensure pytest never loads ambient pytest-randomly in any test runner or subprocess
if "-p no:randomly" not in os.environ.get("PYTEST_ADDOPTS", ""):
    existing = os.environ.get("PYTEST_ADDOPTS", "").strip()
    os.environ["PYTEST_ADDOPTS"] = f"-p no:randomly {existing}".strip()
