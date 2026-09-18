"""Subprocess proof that the collection gate and zero-skip guard fire (spec-0022 R7).

The conftest gate and the zero-skip session guard are themselves untested
plumbing unless something watches them work — and this repo has already been
bitten by exactly that rot vector: the gate's keyword matching once silently
skipped the CI-parity tests themselves, leaving the ``ids=[f"make-{t}"]``
workaround documented in ``tests/deploy/test_ci_make_parity.py`` as the scar.
A guard that cannot be seen firing is indistinguishable from a guard that
fails open (the ``mango-mutation-proof`` discipline, mechanised).

Each test builds a tmp mini-suite whose ``conftest.py`` imports the *real*
hooks from ``tests.conftest`` — not a copy, so these assertions cannot drift
from the shipped gate — and runs pytest on it in a subprocess with a scrubbed
environment. That also proves the one mechanism the guard quietly relies on:
mutating ``session.exitstatus`` in ``pytest_sessionfinish`` really does change
the process exit code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.constants import ENV_GATE_SKIP_REASONS

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Imports the shipped hooks so the mini-suite runs the real gate + guard.
_MINI_CONFTEST = (
    "from tests.conftest import (\n"
    "    pytest_collection_modifyitems,\n"
    "    pytest_collectreport,\n"
    "    pytest_runtest_logreport,\n"
    "    pytest_sessionfinish,\n"
    ")\n"
)

_PASSING_TEST = "def test_probe() -> None:\n    assert True\n"


def _run_pytest(
    tree: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    (tree / "conftest.py").write_text(_MINI_CONFTEST, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("RUN_")}
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            str(tree),
            "-q",
            "--no-cov",
            "-rs",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
        ],
        env=env,
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tree: Path, relpath: str, body: str) -> None:
    target = tree / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def test_integration_dir_is_skipped_without_the_env_gate(tmp_path: Path) -> None:
    """Directory-based gating fires, with the exact sanctioned reason, exit 0.

    Doubles as the guard's allowlist test: a sanctioned env-gate skip must
    NOT trip the zero-skip escalation.
    """
    _write(tmp_path, "integration/test_gate_probe.py", _PASSING_TEST)
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert ENV_GATE_SKIP_REASONS["RUN_INTEGRATION"] in result.stdout


def test_integration_dir_runs_with_the_env_gate(tmp_path: Path) -> None:
    _write(tmp_path, "integration/test_gate_probe.py", _PASSING_TEST)
    result = _run_pytest(tmp_path, {"RUN_INTEGRATION": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_unmarked_directory_is_not_skipped(tmp_path: Path) -> None:
    """Pins the over-matching hazard: only gated names may gate.

    The rag gate once nearly skipped pure-domain unit tests because a
    directory name leaked into ``item.keywords`` — this asserts a plain
    directory runs untouched with no ``RUN_*`` env set.
    """
    _write(tmp_path, "plain/test_gate_probe.py", _PASSING_TEST)
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_unsanctioned_skip_fails_the_session(tmp_path: Path) -> None:
    """An ad-hoc skip turns an otherwise-green run red, naming the offender."""
    _write(
        tmp_path,
        "test_adhoc.py",
        'import pytest\n\ndef test_flaky() -> None:\n    pytest.skip("flaky, revisit later")\n',
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "zero-skip guard" in result.stdout
    assert "flaky, revisit later" in result.stdout


def test_xfail_fails_the_session(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_expected_failure.py",
        "import pytest\n\n@pytest.mark.xfail\ndef test_broken() -> None:\n"
        "    raise AssertionError\n",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "XFAIL/XPASS" in result.stdout


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param("set VERTEX_PROJECT_ID to run Vertex AI tests", id="prefix-vertex"),
        pytest.param("set GCP_SECRETS_PROJECT to run GCP secrets tests", id="prefix-gcp"),
        # spec-0029 R9: the live MODEL_OVERRIDE scenario needs a *second*
        # loaded model, which the suite's own RUN_LMSTUDIO gate cannot express
        # — so it skips at runtime inside an already-enabled suite, exactly
        # like the vertex/gcp fixtures above.
        pytest.param(
            "set LMSTUDIO_OVERRIDE_MODEL to run LM Studio override tests",
            id="prefix-lmstudio",
        ),
    ],
    # Ids are deliberately prefixed: conftest's collection gate matches
    # substrings against `item.keywords`, which include the parametrize id, so
    # a bare "vertex"/"lmstudio" id makes these cases skip as though they were
    # the real gated live-service tests — silently, on a green run. The same
    # scar `tests/deploy/test_ci_make_parity.py` records with `ids=[f"make-{t}"]`.
    #
    # This file is where that hazard bites hardest: it is the proof that the
    # gate works, so a case the gate silently swallows proves nothing.
)
def test_gated_runtime_skip_prefix_is_tolerated(tmp_path: Path, reason: str) -> None:
    """`set VERTEX_*/GCP_*/LMSTUDIO_*` runtime skips stay sanctioned."""
    _write(
        tmp_path,
        "test_runtime_gate.py",
        f"import pytest\n\ndef test_needs_config() -> None:\n    pytest.skip({reason!r})\n",
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


def test_an_unprefixed_runtime_skip_is_still_rejected(tmp_path: Path) -> None:
    """The other direction: widening the prefix list must not sanction everything.

    Adding ``LMSTUDIO_`` to the allowlist is only safe if the allowlist is
    still an allowlist. A reason naming an env var outside the sanctioned
    prefixes must keep failing the session.
    """
    _write(
        tmp_path,
        "test_runtime_gate.py",
        "import pytest\n\ndef test_needs_config() -> None:\n"
        '    pytest.skip("set SOME_OTHER_VAR to run something")\n',
    )
    result = _run_pytest(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "zero-skip guard" in result.stdout


def test_collection_level_importorskip_fails_the_session(tmp_path: Path) -> None:
    """A module-level ``importorskip`` is a *collect* skip, invisible to
    ``pytest_runtest_logreport`` — this proves the ``pytest_collectreport``
    path catches it. Hypothesis and asyncpg are dev-extra requirements, so in
    a correctly-installed environment such a skip means a broken install
    silently shedding fuzz coverage, not an optional feature.
    """
    _write(
        tmp_path,
        "test_missing_dep.py",
        'import pytest\n\npytest.importorskip("nonexistent_pkg_spec0022")\n\n' + _PASSING_TEST,
    )
    # A passing sibling keeps the session otherwise green (exit 0 without the
    # guard) — an all-skipped run would exit NO_TESTS_COLLECTED and mask the
    # escalation path under test.
    _write(tmp_path, "test_green_sibling.py", _PASSING_TEST)
    result = _run_pytest(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "SKIP (collection)" in result.stdout
