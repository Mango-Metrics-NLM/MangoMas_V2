"""Runs the eval self-check exactly as `make eval-selfcheck` does (spec-0045 B1).

Two oracles, one per direction, so the lane is never vacuously green:

* the committed dataset + baseline pass through the real CLI with the
  Makefile's own flags (read from the Makefile, never restated here);
* a corrupted copy of the dataset fails the threshold gate with exit code 3
  and names the regressed row — the direction `MUT-EVAL-01` replays against
  the committed file in `make guard-probe`.

The CLI builds an orchestrator from `Settings`, so the LM Studio client is
constructed but never contacted: `echo` dispatches no LLM call.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE
from tests.deploy import _makefile

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI = (sys.executable, "-m", "mangomas.cli.main", "eval")
_TIMEOUT_SECONDS = 120


def _run(*extra: str, dataset: Path | None = None) -> subprocess.CompletedProcess[str]:
    raw = _makefile.variable("EVAL_SELFCHECK_ARGS").replace(
        "$(EVAL_SELFCHECK_DATASET)", _makefile.variable("EVAL_SELFCHECK_DATASET")
    )
    args = shlex.split(raw)
    if dataset is not None:
        args[args.index("-d") + 1] = str(dataset)
    baseline = _REPO_ROOT / _makefile.variable("EVAL_SELFCHECK_BASELINE")
    return subprocess.run(  # noqa: S603 -- fixed argv: sys.executable + Makefile flags
        [*_CLI, *args, "--baseline", str(baseline), "--no-allow-new-failures", *extra],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )


def test_eval_selfcheck_passes_on_the_committed_dataset() -> None:
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate=PASS" in result.stdout


def test_eval_selfcheck_fails_with_exit_3_when_a_row_is_corrupted(tmp_path: Path) -> None:
    dataset = _REPO_ROOT / _makefile.variable("EVAL_SELFCHECK_DATASET")
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line]
    rows[0]["expected"] = rows[0]["expected"] + " (corrupted)"
    corrupted = tmp_path / dataset.name
    corrupted.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    result = _run(dataset=corrupted)

    assert result.returncode == EVAL_GATE_EXIT_CODE, result.stdout + result.stderr
    assert "gate=FAIL" in result.stdout
    assert rows[0]["id"] in result.stdout
