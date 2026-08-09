"""Per-package coverage gate.

Run *after* ``pytest --cov`` to verify that each module cluster meets its
minimum coverage floor.  Exits with code 1 if any floor is not met.

Usage::

    python scripts/check_coverage.py

The script shells out to ``coverage report --include=<glob> --fail-under=<N>``
once per floor, reading the ``.coverage`` data file produced by the preceding
pytest run (pytest's ``--cov=mangomas`` addopt writes it). It never reads
``coverage.xml`` — that report exists only for the codecov upload step in CI
and is not consulted here.
"""

from __future__ import annotations

import subprocess
import sys
from typing import NamedTuple


class Floor(NamedTuple):
    include: str
    minimum: int
    label: str


FLOORS: list[Floor] = [
    Floor("src/mangomas/errors.py", 100, "errors"),
    Floor("src/mangomas/registry.py", 100, "registry"),
    Floor("src/mangomas/core/*.py", 100, "core"),
    Floor("src/mangomas/composition.py", 95, "composition"),
    Floor("src/mangomas/agents/*.py", 95, "agents"),
    # ``**/*.py`` (not ``*.py``) so the ``api/routes/`` subpackage is measured
    # too — a flat ``*.py`` glob is non-recursive and would silently exclude
    # any file added under a new subdirectory.
    Floor("src/mangomas/api/**/*.py", 95, "api"),
    Floor("src/mangomas/cli/*.py", 95, "cli"),
    Floor("src/mangomas/adapters/**/*.py", 85, "adapters"),
    Floor("src/mangomas/secrets/*.py", 100, "secrets"),
    Floor("src/mangomas/correlation.py", 100, "correlation"),
    Floor("src/mangomas/tenancy.py", 100, "tenancy"),
    Floor("src/mangomas/_headers.py", 100, "headers"),
    Floor("src/mangomas/config.py", 95, "config"),
    Floor("src/mangomas/telemetry.py", 95, "telemetry"),
    Floor("src/mangomas/metrics.py", 95, "metrics"),
    Floor("src/mangomas/eval/**/*.py", 95, "eval"),
    Floor("src/mangomas/rag/**/*.py", 95, "rag"),
    Floor("src/mangomas/workflow/**/*.py", 95, "workflow"),
    # ADR-0021 / spec-0017: PROTECTED_PATHS/marker-alias governance +
    # ConfigChange decision logic. New package; 95% matches its siblings.
    Floor("src/mangomas/harness/**/*.py", 95, "harness"),
]

GLOBAL_FLOOR = Floor("src/mangomas/**/*.py", 95, "global")


def _check(floor: Floor) -> bool:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            f"--include={floor.include}",
            f"--fail-under={floor.minimum}",
            "--no-skip-covered",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  FAIL [{floor.label}]: {floor.include} < {floor.minimum}%")
        # Print the last few lines of coverage output for context.
        lines = (result.stdout + result.stderr).strip().splitlines()
        for line in lines[-6:]:
            print(f"    {line}")
        return False
    return True


def main() -> None:
    print("Checking per-package coverage floors…")
    failures: list[str] = []

    for floor in FLOORS:
        ok = _check(floor)
        status = "OK  " if ok else "FAIL"
        print(f"  {status} [{floor.label}] >= {floor.minimum}%")
        if not ok:
            failures.append(floor.label)

    print()
    ok_global = _check(GLOBAL_FLOOR)
    status = "OK  " if ok_global else "FAIL"
    print(f"  {status} [global] >= {GLOBAL_FLOOR.minimum}%")
    if not ok_global:
        failures.append(GLOBAL_FLOOR.label)

    if failures:
        print(f"\nCoverage gate FAILED for: {failures}")
        sys.exit(1)
    else:
        print("\nAll coverage floors met.")


if __name__ == "__main__":
    main()
