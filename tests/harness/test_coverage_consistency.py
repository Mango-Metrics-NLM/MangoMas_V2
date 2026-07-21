"""Coverage-floor drift guard (ADR-0011).

Asserts every coverage-floor value this repo directly controls agrees with
the single source of truth in ``pyproject.toml``'s
``[tool.pytest.ini_options].addopts``. ``ci.yml``'s explicit
``--cov-fail-under=90`` CLI override is a known, separate, not-yet-reconciled
value — raising it blind (without first checking actual coverage against a
95% floor) could break CI, so it is deliberately **not** asserted against
here. That reconciliation is tracked as an explicit fast-follow in ADR-0011,
not folded into this hard gate.
"""

from __future__ import annotations

from pathlib import Path

from mangomas.harness.coverage import read_coverage_floor
from tests._script_loader import load_script_module

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_pyproject_floor_matches_check_coverage_global_floor() -> None:
    """``pyproject.toml``'s real gate must agree with ``check_coverage.py``'s ``GLOBAL_FLOOR``.

    Both values are repo-controlled and were exactly this kind of
    independently-hardcoded pair before ADR-0011 (85/90/95 for one nominal
    gate) — this test is the guard against that drift recurring.
    """
    pyproject_floor = read_coverage_floor(_REPO_ROOT / "pyproject.toml")
    check_coverage = load_script_module("check_coverage.py")
    assert check_coverage.GLOBAL_FLOOR.minimum == pyproject_floor
