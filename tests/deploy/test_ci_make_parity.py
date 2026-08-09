"""Contract tests locking CI ↔ Makefile parity (spec 0014, milestone M14).

Before this suite, ``ci.yml`` hand-duplicated the lint/test/coverage commands
that the Makefile also defined, and two numeric thresholds (the global and
bridge coverage floors) were each written in two places with only a comment
asking a future editor to "keep them in sync" — nothing enforced it. These
tests make the coupling structural: ``ci.yml`` must invoke the ``make``
targets themselves (so there is only one place each command lives), and the
one remaining genuine duplication (the global floor, which necessarily
exists in both ``pyproject.toml`` addopts and ``scripts/check_coverage.py``
because pytest and the per-package gate are separate processes) is asserted
equal.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._script_loader import load_script_module

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_MAKEFILE = _REPO_ROOT / "Makefile"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Opt-in suite targets that must never trip the global coverage gate when run
# standalone — the concrete regression this milestone locks in (D5: `make
# rag` previously omitted `--no-cov` and always failed).
_OPT_IN_TARGETS = (
    "integration",
    "lmstudio",
    "vertex",
    "postgres",
    "rag",
    "gcp-secrets",
    "gcp-trace",
    "langfuse",
)


def _ci_jobs() -> dict[str, Any]:
    doc = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    return dict(doc["jobs"])


def _step_run_commands(job: dict[str, Any]) -> list[str]:
    """Return every step's ``run:`` body, excluding the shared setup steps
    (checkout / Python setup / ``pip install -e ".[dev]"``) common to every
    job — the assertions below care only about the gate commands themselves.
    """
    return [
        step["run"]
        for step in job["steps"]
        if "run" in step and not step["run"].startswith("pip install")
    ]


def _make_target_body(target: str) -> str:
    """Return the recipe lines for *target* (everything up to the next
    unindented/blank-separated target or EOF)."""
    text = _MAKEFILE.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(target)}:.*?(?=\n\S|\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    assert match is not None, f"Makefile target {target!r} not found"
    return match.group(0)


def test_lint_job_delegates_every_step_to_make() -> None:
    """Each lint-job step body is a bare `make <target>` invocation — proves
    ruff/mypy/frontmatter commands live in exactly one place (the Makefile).
    """
    commands = _step_run_commands(_ci_jobs()["lint"])
    assert commands == [
        "make validate-config",
        "make lint",
        "make format-check",
        "make frontmatter",
        "make typecheck",
    ]


def test_test_job_delegates_to_make() -> None:
    commands = _step_run_commands(_ci_jobs()["test"])
    assert commands == ["make test-xml", "make coverage"]


def test_bridge_coverage_job_delegates_to_make() -> None:
    commands = _step_run_commands(_ci_jobs()["bridge-coverage"])
    assert commands == ["make bridge-coverage"]


def test_protected_paths_job_delegates_to_make() -> None:
    """The protected-path gate (ADR-0021) has its own job, like bridge-coverage,
    so a failure is attributable and the job needs no `pip install` step (the
    script is stdlib-only by design — see spec-0017 R2)."""
    steps = _ci_jobs()["protected-paths"]["steps"]
    run_steps = [step["run"] for step in steps if "run" in step]
    assert run_steps == [
        "git fetch origin feat/initial-release",
        "make protected-paths BASE_REF=origin/feat/initial-release",
    ]
    assert not any(step.get("run", "").startswith("pip install") for step in steps)


def test_scripts_coverage_job_delegates_to_make() -> None:
    """scripts/ sits outside `--cov=mangomas`'s reach (ADR-0021 / spec-0017
    A7), so it gets the same isolated-job treatment as bridge-coverage."""
    commands = _step_run_commands(_ci_jobs()["scripts-coverage"])
    assert commands == ["make scripts-coverage"]


def test_global_coverage_floor_matches_pytest_addopts() -> None:
    """The one duplication that can't be structurally eliminated (pytest's
    own --cov-fail-under vs. scripts/check_coverage.py's authoritative
    per-package gate, run as two separate processes) is asserted equal
    instead of merely commented as "keep in sync".
    """
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
    match = re.search(r"--cov-fail-under=(\d+)", addopts)
    assert match is not None, "pyproject.toml addopts must set --cov-fail-under"
    pyproject_floor = int(match.group(1))

    check_coverage = load_script_module("check_coverage.py")
    assert check_coverage.GLOBAL_FLOOR.minimum == pyproject_floor


@pytest.mark.parametrize(
    "target",
    _OPT_IN_TARGETS,
    # Explicit ids that don't literally equal a registered pytest marker name
    # (e.g. bare "lmstudio"/"vertex"/"postgres"/"langfuse") — conftest.py's
    # marker-based skip gate does substring matching against each item's
    # keywords, which include the parametrize id, so an unprefixed id would
    # cause these parity checks to be silently skipped as if they were the
    # real gated live-service tests.
    ids=[f"make-{t}" for t in _OPT_IN_TARGETS],
)
def test_opt_in_target_never_fails_the_global_coverage_gate(target: str) -> None:
    """D5 regression, generalised to every opt-in suite target: running any
    one of them standalone must not inherit pytest's --cov-fail-under=95 and
    fail on an unrelated subset of the codebase.
    """
    body = _make_target_body(target)
    assert "--no-cov" in body, f"make {target} must pass --no-cov (see D5)"


def test_bridge_coverage_uses_an_isolated_coverage_file() -> None:
    """The bridge run must not overwrite the main suite's `.coverage` data —
    otherwise `make gate` (test → coverage → bridge-coverage) leaves a
    standalone `make coverage` afterward measuring the wrong run.
    """
    body = _make_target_body("bridge-coverage")
    assert "COVERAGE_FILE=.coverage.bridge" in body


def test_scripts_coverage_uses_an_isolated_coverage_file_and_addopts() -> None:
    """Same isolation idiom as bridge-coverage (ADR-0021 / spec-0017 A7):
    its own COVERAGE_FILE so `make gate`'s later `make coverage` still sees
    the main suite's data, and `-o addopts=""` so the inherited
    `--cov=mangomas` (which would measure the wrong package) doesn't apply.
    """
    body = _make_target_body("scripts-coverage")
    assert "COVERAGE_FILE=.coverage.scripts" in body
    assert '-o addopts=""' in body
