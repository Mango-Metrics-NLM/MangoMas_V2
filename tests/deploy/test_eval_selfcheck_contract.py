"""The eval self-check is a harness self-test whose teeth are pinned here (spec-0045 B1).

`make eval-selfcheck` runs the `echo` target over a committed dataset against a
committed baseline. The probe entry `MUT-EVAL-01` proves the *threshold* path
goes red; removing a flag makes the target quieter, never redder, so the probe
cannot see that. These tests pin the flags, the dataset/baseline pairing and
the CI wiring instead — the fail-open shape (a gate that passes because its
precondition quietly vanished) is the defect class every `tests/deploy`
contract exists to catch.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.deploy import _makefile, _workflows

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGET = "eval-selfcheck"
_BASELINE_TARGET = "eval-selfcheck-baseline"
_REQUIRED_FLAGS = (
    "-t echo",
    "-s exact_match",
    "--min-pass-rate 1.0",
    "--fail-on-error",
    "--baseline $(EVAL_SELFCHECK_BASELINE)",
    "--no-allow-new-failures",
)
_MIN_ROWS = 5


def _dataset_path() -> Path:
    return _REPO_ROOT / _makefile.variable("EVAL_SELFCHECK_DATASET")


def _baseline_path() -> Path:
    return _REPO_ROOT / _makefile.variable("EVAL_SELFCHECK_BASELINE")


def _expanded_target_body(target: str) -> str:
    """The target body with the shared `$(EVAL_SELFCHECK_ARGS)` variable inlined."""
    return _makefile.target_body(target).replace(
        "$(EVAL_SELFCHECK_ARGS)", _makefile.variable("EVAL_SELFCHECK_ARGS")
    )


def test_selfcheck_target_carries_every_gating_flag() -> None:
    body = _expanded_target_body(_TARGET)
    missing = [flag for flag in _REQUIRED_FLAGS if flag not in body]
    assert missing == [], f"{_TARGET} lost gating flag(s) {missing}: {body!r}"


def test_baseline_target_writes_where_the_selfcheck_reads() -> None:
    body = _expanded_target_body(_BASELINE_TARGET)
    assert "-o $(EVAL_SELFCHECK_BASELINE)" in body
    assert "--baseline" not in body, "regenerating a baseline must not diff against itself"


def test_dataset_and_baseline_are_committed_and_paired() -> None:
    dataset = _dataset_path()
    baseline = _baseline_path()
    assert dataset.is_file(), dataset
    assert baseline.is_file(), baseline
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) >= _MIN_ROWS, "too few rows for the regression diff to mean anything"
    report = json.loads(baseline.read_text(encoding="utf-8"))
    assert {row["id"] for row in rows} == {row["row_id"] for row in report["rows"]}, (
        "baseline row ids must match the dataset, or every row reads as dropped + new"
    )
    assert report["passed"] == report["dataset_size"] == len(rows), (
        "the self-check baseline must be an all-pass run, or --no-allow-new-failures is moot"
    )
    assert report["target_name"] == "echo"


def test_echo_rows_expect_their_own_last_user_message() -> None:
    """The dataset is only a self-test if `echo` can pass it by construction."""
    for line in _dataset_path().read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        last_user = [m for m in row["messages"] if m["role"] == "user"][-1]["content"]
        assert row["expected"] == last_user, row["id"]


_LANES_TARGET = "eval-lanes"
_LANES_BASELINE_TARGET = "eval-lanes-baseline"
_LANES_WRITE_ENV = "EVAL_LANES_WRITE_BASELINES=1"


def test_lanes_target_runs_the_lane_suite_and_its_baseline_twin_sets_the_write_flag() -> None:
    """spec-0045 B7: the lane and its baseline writer run the same tests; only the env differs."""
    lane = _makefile.target_body(_LANES_TARGET)
    rewrite = _makefile.target_body(_LANES_BASELINE_TARGET)
    tests = _makefile.variable("EVAL_LANES_TESTS")
    assert tests and (_REPO_ROOT / tests).is_file(), tests
    assert "$(EVAL_LANES_TESTS)" in lane and "$(EVAL_LANES_TESTS)" in rewrite
    assert _LANES_WRITE_ENV not in lane, "the gate lane must compare, never rewrite, baselines"
    assert _LANES_WRITE_ENV in rewrite
    gate_line = next(line for line in _makefile.text().splitlines() if line.startswith("gate:"))
    assert f" {_LANES_TARGET} " in gate_line or gate_line.rstrip().endswith(f" {_LANES_TARGET}")
    test_job = _workflows.jobs("ci.yml")["test"]
    assert any(step.get("run") == f"make {_LANES_TARGET}" for step in test_job["steps"])


def test_every_lane_dataset_has_a_committed_baseline() -> None:
    datasets = {p.stem for p in (_REPO_ROOT / "evals" / "datasets").glob("*_v1.jsonl")}
    baselines = {p.stem for p in (_REPO_ROOT / "evals" / "baselines").glob("*_v1.json")}
    assert datasets == baselines, sorted(datasets ^ baselines)


def test_selfcheck_runs_in_gate_and_in_the_ci_test_job() -> None:
    assert _TARGET in _makefile.target_body("gate").split() or _TARGET in _makefile.text(), (
        "eval-selfcheck must be a prerequisite of `make gate`"
    )
    gate_line = next(line for line in _makefile.text().splitlines() if line.startswith("gate:"))
    assert f" {_TARGET} " in gate_line or gate_line.rstrip().endswith(f" {_TARGET}")
    test_job = _workflows.jobs("ci.yml")["test"]
    assert any(step.get("run") == f"make {_TARGET}" for step in test_job["steps"])
