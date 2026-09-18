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


# Every directory-scoped floor uses ``**/*.py``, never a flat ``*.py``.
#
# ``coverage report --include`` treats ``**`` as "zero or more directories", so
# the recursive form matches the same top-level files a flat glob does *plus*
# anything in a subpackage — verified against the live data file, where
# ``api/*.py`` matches 8 files and ``api/**/*.py`` matches 12.
#
# This matters because the failure mode is **fail-open, not fail-closed**: a
# flat glob that stops matching still reports a percentage and still passes.
# When ``api/`` grew a ``routes/`` subpackage the flat glob silently stopped
# measuring it, and ``cli/`` is next — spec-0015 decomposes ``cli/main.py``
# into a command package. ``tests/test_check_coverage.py`` pins the rule so a
# flat glob cannot be reintroduced by hand.
FLOORS: list[Floor] = [
    Floor("src/mangomas/errors.py", 100, "errors"),
    Floor("src/mangomas/registry.py", 100, "registry"),
    Floor("src/mangomas/core/**/*.py", 100, "core"),
    Floor("src/mangomas/composition/**/*.py", 95, "composition"),
    Floor("src/mangomas/agents/**/*.py", 95, "agents"),
    Floor("src/mangomas/api/**/*.py", 95, "api"),
    Floor("src/mangomas/cli/**/*.py", 95, "cli"),
    Floor("src/mangomas/adapters/**/*.py", 85, "adapters"),
    Floor("src/mangomas/secrets/**/*.py", 100, "secrets"),
    Floor("src/mangomas/correlation.py", 100, "correlation"),
    Floor("src/mangomas/tenancy.py", 100, "tenancy"),
    Floor("src/mangomas/_headers.py", 100, "headers"),
    Floor("src/mangomas/config/**/*.py", 95, "config"),
    Floor("src/mangomas/telemetry/**/*.py", 95, "telemetry"),
    Floor("src/mangomas/metrics.py", 95, "metrics"),
    Floor("src/mangomas/eval/**/*.py", 95, "eval"),
    Floor("src/mangomas/rag/**/*.py", 95, "rag"),
    Floor("src/mangomas/workflow/**/*.py", 95, "workflow"),
    Floor("src/mangomas/cognitive/**/*.py", 95, "cognitive"),
    # ADR-0021 / spec-0017: PROTECTED_PATHS/marker-alias governance +
    # ConfigChange decision logic. New package; 95% matches its siblings.
    Floor("src/mangomas/harness/**/*.py", 95, "harness"),
    Floor("src/mangomas/utils/**/*.py", 100, "utils"),
    # spec-0020 R1: the one top-level module that had no floor. Shared
    # entry-point iteration for the two discovery modules; already at 100%,
    # so the floor is set where the code actually is rather than below it.
    Floor("src/mangomas/_entry_points.py", 100, "entry_points"),
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
