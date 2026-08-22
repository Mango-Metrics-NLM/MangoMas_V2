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
from typing import Any, cast

import pytest

from tests._script_loader import load_script_module
from tests.deploy import _workflows

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
    # Parsing lives in tests/deploy/_workflows.py so this suite and
    # test_workflow_hardening.py cannot drift in what they can see.
    return _workflows.jobs(_CI_WORKFLOW.name)


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
    # `gated-suites` runs tests/integration + tests/rag: env-gated so a plain
    # `pytest` stays fast, but needing no service, extra or network. They were
    # absent from CI purely because nothing set the gate.
    assert commands == ["make test-xml", "make coverage", "make gated-suites"]


def test_bridge_coverage_job_delegates_to_make() -> None:
    commands = _step_run_commands(_ci_jobs()["bridge-coverage"])
    assert commands == ["make bridge-coverage"]


def test_protected_paths_job_delegates_to_make() -> None:
    """The protected-path gate (ADR-0021) has its own job, like bridge-coverage,
    so a failure is attributable and the job needs no `pip install` step (the
    script is stdlib-only by design — see spec-0017 R2)."""
    job = _ci_jobs()["protected-paths"]
    run_steps = [step["run"] for step in job["steps"] if "run" in step]
    assert run_steps == [
        "git fetch origin ${{ env.BASE_BRANCH }}",
        "make protected-paths BASE_REF=origin/${{ env.BASE_BRANCH }}",
    ]
    assert not any(step.get("run", "").startswith("pip install") for step in job["steps"])
    # BASE_BRANCH is a job-level env var, not repeated per-step — the two
    # steps above template it rather than each hardcoding the trunk name.
    assert job["env"]["BASE_BRANCH"] == "feat/initial-release"


def test_pull_request_trigger_targets_the_real_trunk() -> None:
    """CI's PR trigger must name this repo's actual trunk.

    `feat/initial-release` is trunk here — the Makefile's `BASE_REF` default
    and the `protected-paths` job's `BASE_BRANCH` above both say so — not
    `main` (a real but permanently-diverged branch; see NEXT_STEPS.md) and not
    `develop` (which does not exist in this repository at all, checked against
    both local and remote branches). A PR opened against the actual trunk got
    no `pull_request`-triggered CI before this.

    PyYAML's default (YAML 1.1) resolver reads the unquoted ``on:`` key as the
    boolean ``True`` rather than the string ``"on"`` — the same reason
    `_ci_jobs()` above only ever indexes `doc["jobs"]`. Looking up either key
    keeps this guard correct even if a future PyYAML default ever stopped
    resolving the bare word to a boolean (YAML 1.2 keeps it a string).
    """
    triggers = _workflows.triggers(_CI_WORKFLOW.name)
    assert triggers["pull_request"]["branches"] == ["feat/initial-release"]


def test_scripts_coverage_job_delegates_to_make() -> None:
    """scripts/ sits outside `--cov=mangomas`'s reach (ADR-0021 / spec-0017
    A7), so it gets the same isolated-job treatment as bridge-coverage."""
    commands = _step_run_commands(_ci_jobs()["scripts-coverage"])
    assert commands == ["make scripts-coverage"]


def test_secret_scan_job_delegates_to_make() -> None:
    """gitleaks lives in exactly one place: the `secret-scan` Makefile target.

    Before this, `secret-scan` was the one CI job with no Makefile target —
    raw inline `curl`/`gitleaks` shell, which made README's "the Makefile
    wraps the exact commands CI runs" claim false for exactly this job.
    """
    commands = _step_run_commands(_ci_jobs()["secret-scan"])
    assert commands == ["make secret-scan"]


def test_secret_scan_runs_both_gitleaks_passes() -> None:
    """The scan must cover the working tree AND committed history (spec-0022 R1).

    The old recipe ran the deprecated ``gitleaks detect`` — an alias for the
    history-only ``git`` scan — so an uncommitted ``.env`` holding a real
    credential passed the gate. Since gitleaks v8.19 the supported commands
    are ``dir`` (working tree) and ``git`` (history); neither subsumes the
    other, so the recipe must run both and never regress to ``detect``.
    """
    body = _make_target_body("secret-scan")
    assert "gitleaks dir" in body
    assert "gitleaks git" in body
    assert "gitleaks detect" not in body


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


def _makefile_variable(name: str) -> str:
    """Return a `NAME ?= value` (or `NAME = value`) assignment from the Makefile.

    Companion to `_make_target_body`: the parity assertions below compare a
    Makefile *variable* rather than a target body.
    """
    match = re.search(
        rf"^{re.escape(name)}\s*\??=\s*(.+)$", _MAKEFILE.read_text(encoding="utf-8"), re.M
    )
    assert match is not None, f"Makefile does not define {name}"
    return match.group(1).strip()


def _mypy_config() -> dict[str, Any]:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", pyproject["tool"]["mypy"])


def test_mypy_checked_surface_matches_the_makefile() -> None:
    """A bare `mypy` must check exactly what `make typecheck` does.

    Before this, `[tool.mypy]` declared `packages = ["mangomas"]`, so a bare
    `mypy` covered **155** files while `make typecheck` — and therefore CI —
    covered **324**. A contributor running the command CLAUDE.md documents got a
    weaker check than the gate, and only found out on push.

    Fixed in configuration rather than documented as a caveat, so the documented
    command is simply correct. This binds the two lists: `CODE_PATHS` is the
    source of truth and `files` must mirror it.
    """
    code_paths = _makefile_variable("CODE_PATHS").split()
    files = _mypy_config()["files"]
    assert files == code_paths, (
        f"[tool.mypy] files={files} but the Makefile's CODE_PATHS={code_paths}. "
        f"A bare `mypy` would check a different surface than `make typecheck`."
    )


def test_mypy_does_not_narrow_the_surface_with_packages() -> None:
    """`packages` alongside `files` would silently re-narrow the surface.

    Asserted separately from the list comparison because it is a different
    failure: `files` could mirror `CODE_PATHS` exactly while a leftover
    `packages` key still limited what actually got checked.
    """
    assert "packages" not in _mypy_config(), (
        "[tool.mypy] declares `packages`, which narrows a bare `mypy` back to "
        "that package regardless of `files` — the drift this test exists to stop"
    )


def test_isolated_coverage_floors_are_pinned() -> None:
    """`SCRIPTS_FLOOR` / `BRIDGE_FLOOR` are the only floors living in Makefile text.

    Every `src/mangomas` floor is a `Floor(...)` in `scripts/check_coverage.py`
    and is parametrised over by `tests/test_check_coverage.py`; the global one
    is asserted equal to pytest's addopt above. These two are `?=` Makefile
    variables that nothing checked — so a quiet edit lowering either would
    weaken an isolated gate with no review record. Bumping a floor is fine;
    doing it invisibly is not, and updating this line is the record.
    """
    assert int(_makefile_variable("SCRIPTS_FLOOR")) == 92
    assert int(_makefile_variable("BRIDGE_FLOOR")) == 100


def test_nightly_jobs_delegate_to_make() -> None:
    """The scheduled suites obey the same one-place rule as push CI.

    `nightly.yml` exists because seven opt-in suites ran nowhere and
    `secret-scan` only ever fired on push — but a scheduled job that inlines
    its commands would reintroduce exactly the drift `make`-delegation
    prevents, and nobody reads a nightly log until it matters.
    """
    jobs = _workflows.jobs("nightly.yml")
    assert _step_run_commands(jobs["postgres"]) == ["make postgres"]
    assert _step_run_commands(jobs["secret-scan"]) == ["make secret-scan"]


def test_nightly_is_scheduled_and_manually_dispatchable() -> None:
    """A schedule nobody can trigger by hand is untestable until it fires."""
    triggers = _workflows.triggers("nightly.yml")
    assert triggers["schedule"], "nightly.yml must carry a cron schedule"
    assert "workflow_dispatch" in triggers
