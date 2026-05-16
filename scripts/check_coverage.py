"""Per-package coverage gate.

Run *after* ``pytest --cov`` to verify that each module cluster meets its
minimum coverage floor.  Exits with code 1 if any floor is not met.

Usage::

    python scripts/check_coverage.py

The script reads ``coverage.xml`` (or the ``.coverage`` data file) produced by
the preceding pytest run.  Add ``--cov-report=xml`` to your pytest invocation
if you want the XML report used here; otherwise it uses ``coverage report``
against the existing data file.
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
    Floor("src/mangomas/composition.py", 90, "composition"),
    Floor("src/mangomas/agents/*.py", 95, "agents"),
    Floor("src/mangomas/api/*.py", 90, "api"),
    Floor("src/mangomas/cli/*.py", 90, "cli"),
    Floor("src/mangomas/adapters/**/*.py", 85, "adapters"),
    Floor("src/mangomas/secrets/*.py", 100, "secrets"),
]

GLOBAL_FLOOR = Floor("src/mangomas/**/*.py", 90, "global")


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
