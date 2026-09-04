"""Every gated suite has an executing home, or a recorded reason it cannot.

Roadmap item 2.1. This repository has ten env-gated suites; before this guard,
nothing said which of them actually ran anywhere. Two of them (``integration``,
``rag``) run on every push, one (``postgres``) runs nightly, and the rest ran
**nowhere, ever** — a state indistinguishable, from the outside, from "these
suites are fine".

The gap is not that some suites cannot run in CI. LM Studio needs a local
process; the cloud suites need credentials nobody has provisioned. The gap is
that the *reason* lived in nobody's head. This guard makes the claim explicit
and, more importantly, makes a **stale** claim fail: a suite listed as
infeasible that some workflow does invoke is the fail-open shape — an
infeasibility nobody revisits is how a suite that became runnable stays unrun.

Parsing is delegated to the readers this suite already has —
``tests/deploy/_workflows.py`` for the workflow YAML and ``_make_target_body``
for Makefile recipes — rather than adding a third. A guard with its own parser
is a guard that can silently disagree with the gate it is checking.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.constants import ENV_GATE_SUITES, HOSTED_RUNNER_INFEASIBLE
from tests.deploy import _workflows

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _REPO_ROOT / "Makefile"

# A Makefile recipe line that runs pytest behind an env gate, e.g.
#   RUN_POSTGRES=1 $(PYTHON) -m pytest tests/postgres --no-cov $(PYTEST_FLAGS)
# Captures every RUN_* var on the line, because `embeddings-local` sets two.
_GATE_VAR_RE = re.compile(r"\b(RUN_[A-Z_]+)=1")
# A target header: a lowercase name followed by `:`. The `(?!=)` rejects a
# `:=` variable assignment; uppercase variable names (`PYTHON ?= python`) are
# already excluded by the leading `[a-z]`.
#
# Deliberately does NOT exclude `=` from the rest of the line: several target
# headers carry a `##` help comment naming their own env gate
# (`integration: ## Integration suite (RUN_INTEGRATION=1)`), and an
# `=`-excluding pattern silently skipped exactly those targets — making this
# guard report the best-covered suite in the repo as homeless.
_TARGET_RE = re.compile(r"^([a-z][a-z0-9-]*):(?!=)", re.MULTILINE)


def _makefile_text() -> str:
    return _MAKEFILE.read_text(encoding="utf-8")


def _target_bodies() -> dict[str, str]:
    """Every Makefile target mapped to its recipe body."""
    text = _makefile_text()
    names = [m.group(1) for m in _TARGET_RE.finditer(text)]
    bodies: dict[str, str] = {}
    for name in names:
        pattern = re.compile(rf"^{re.escape(name)}:.*?(?=\n\S|\Z)", re.MULTILINE | re.DOTALL)
        match = pattern.search(text)
        if match is not None:
            bodies[name] = match.group(0)
    return bodies


def _targets_by_gate() -> dict[str, set[str]]:
    """``RUN_*`` env var → the Makefile targets that set it."""
    mapping: dict[str, set[str]] = {}
    for name, body in _target_bodies().items():
        for gate in _GATE_VAR_RE.findall(body):
            mapping.setdefault(gate, set()).add(name)
    return mapping


def _targets_invoked_by_workflows() -> set[str]:
    """Every ``make <target>`` any workflow runs, directly or via a composite.

    A composite target counts for what it delegates to: CI runs
    ``make gated-suites``, whose recipe is ``integration rag``, so both of
    those suites genuinely execute on every push even though no workflow names
    them.
    """
    invoked: set[str] = set()
    for _where, body in _workflows.run_bodies():
        invoked.update(re.findall(r"\bmake\s+([a-z][a-z0-9-]*)", body))

    bodies = _target_bodies()
    # Expand prerequisites transitively: `gated-suites: integration rag`.
    frontier = list(invoked)
    while frontier:
        target = frontier.pop()
        header = bodies.get(target, "").split("\n", 1)[0]
        _, _, prereqs = header.partition(":")
        for prereq in prereqs.split("##")[0].split():
            if prereq in bodies and prereq not in invoked:
                invoked.add(prereq)
                frontier.append(prereq)
    return invoked


def _gates_with_an_executing_home() -> set[str]:
    invoked = _targets_invoked_by_workflows()
    return {gate for gate, targets in _targets_by_gate().items() if targets & invoked}


# ── The contract ──────────────────────────────────────────────────────────────


def test_the_parser_sees_the_suites_it_is_meant_to_check() -> None:
    """Non-vacuity: a regex that matched nothing would make every test below pass."""
    by_gate = _targets_by_gate()
    assert by_gate, "parsed zero gated Makefile targets — did the recipe shape change?"
    missing = sorted(set(ENV_GATE_SUITES) - set(by_gate))
    assert missing == [], (
        f"gated suites with no Makefile target at all: {missing}. Every RUN_* gate "
        "needs a `make` target, or it can never be run reproducibly by anyone."
    )


@pytest.mark.parametrize("gate", sorted(ENV_GATE_SUITES), ids=lambda g: f"suite-{g.lower()}")
def test_every_gated_suite_runs_somewhere_or_says_why_not(gate: str) -> None:
    """Each suite either executes in a workflow, or is recorded as infeasible."""
    has_home = gate in _gates_with_an_executing_home()
    recorded = gate in HOSTED_RUNNER_INFEASIBLE
    assert has_home or recorded, (
        f"{gate} ({ENV_GATE_SUITES[gate]}) runs in no workflow and is not listed in "
        "HOSTED_RUNNER_INFEASIBLE. Give it an executing home, or record why a "
        "hosted runner cannot provide one (spec-0029 R7.2)."
    )


@pytest.mark.parametrize(
    "gate", sorted(HOSTED_RUNNER_INFEASIBLE), ids=lambda g: f"claim-{g.lower()}"
)
def test_no_infeasibility_claim_is_stale(gate: str) -> None:
    """A suite cannot be both "impossible in CI" and running in CI.

    The direction that catches rot. A suite that gains a home while its excuse
    stays on the books would otherwise keep the excuse forever, and the next
    person to read the table would believe it.
    """
    assert gate not in _gates_with_an_executing_home(), (
        f"{gate} is listed in HOSTED_RUNNER_INFEASIBLE but a workflow does run it. "
        "Delete the entry — the claim is stale."
    )


def test_infeasibility_reasons_are_substantive() -> None:
    """A reason must say something; an empty string is not a record.

    Cheap, and it closes the obvious way to satisfy the guard without doing
    the thinking: adding a key with an empty value.
    """
    thin = sorted(
        gate for gate, reason in HOSTED_RUNNER_INFEASIBLE.items() if len(reason.split()) < 4
    )
    assert thin == [], f"infeasibility reasons too thin to be useful: {thin}"


def test_every_infeasible_suite_is_actually_a_gated_suite() -> None:
    """The table cannot name a suite that does not exist.

    Guards against the other desync: a renamed or deleted gate leaving an
    orphan excuse behind, which would make the coverage above look complete
    while silently covering nothing.
    """
    unknown = sorted(set(HOSTED_RUNNER_INFEASIBLE) - set(ENV_GATE_SUITES))
    assert unknown == [], f"HOSTED_RUNNER_INFEASIBLE names non-existent gates: {unknown}"
