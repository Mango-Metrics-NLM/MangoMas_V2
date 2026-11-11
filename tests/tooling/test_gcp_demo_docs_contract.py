"""Contract between the GCP demo docs (spec-0049) and the code they describe.

Copilot's review of PR #130 found three runbook examples that could not pass
the validation the runbook itself had just enabled (an ``ExecutionPlan`` with
``step_id`` and no ``goal``, a ``ReviewResult`` with ``verdict`` instead of
``passed``, a ``list_agents`` tool call the CLI never registers), and a plan
command that printed a live OAuth bearer token. Each is a mechanical drift
between prose and code, so each is asserted here instead of re-reviewed.

Offline and deterministic: the JSON examples are parsed from the Markdown and
validated against the real Pydantic schemas; the tool roster comes from the
real composition helper.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

import pytest

from mangomas.agents.planner import ExecutionPlan
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition.rag import _build_rag_tools
from mangomas.rag import RetrievalTool

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_RUNBOOK: Final[Path] = _REPO_ROOT / "docs" / "runbooks" / "gcp-local-demo.md"
_TOKEN_DOCS: Final[tuple[Path, ...]] = (
    _RUNBOOK,
    _REPO_ROOT / "docs" / "plans" / "20261109T010000Z-gcp-integration-demonstration-plan.md",
    _REPO_ROOT / ".claude" / "skills" / "mango-gcp-demo" / "SKILL.md",
)
_JSON_FENCE: Final[re.Pattern[str]] = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
_TOKEN_COMMAND: Final[str] = "print-access-token"  # noqa: S105 -- a gcloud verb, not a secret
# A token command is safe only when its *stdout* is discarded on the same line:
# `>/dev/null`, `> $null` or `| Out-Null`. A stderr-only redirect (`2>/dev/null`,
# `2> $null`) or a redirect to a real file still exposes the token, so the
# redirect must not be preceded by a file-descriptor digit or '&'.
_STDOUT_DISCARDED: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9&])>\s*(?:/dev/null|\$null)\b|\|\s*Out-Null\b", re.IGNORECASE
)


def _discards_stdout(line: str) -> bool:
    return _STDOUT_DISCARDED.search(line) is not None


def _json_objects(path: Path) -> list[dict[str, Any]]:
    """Every fenced ``json`` block in *path* that parses to a JSON object."""
    objects: list[dict[str, Any]] = []
    for block in _JSON_FENCE.findall(path.read_text(encoding="utf-8")):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue  # illustrative fragments with ellipses are not contracts
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


class _Stub:
    """Non-None stand-in: ``_build_rag_tools`` only checks presence at build time."""


def _cli_tool_names() -> set[str]:
    registry = _build_rag_tools(_Stub(), _Stub(), top_k=1)
    assert registry is not None
    return set(registry.available())


def test_runbook_execution_plan_examples_validate_against_the_schema() -> None:
    plans = [o for o in _json_objects(_RUNBOOK) if "steps" in o and "kind" not in o]
    assert plans, "the runbook should carry at least one ExecutionPlan example"
    for plan in plans:
        ExecutionPlan.model_validate(plan)


def test_runbook_review_result_examples_validate_against_the_schema() -> None:
    reviews = [o for o in _json_objects(_RUNBOOK) if {"score", "feedback"} <= o.keys()]
    assert reviews, "the runbook should carry at least one ReviewResult example"
    for review in reviews:
        ReviewResult.model_validate(review)
        assert "verdict" not in review


def test_runbook_tool_calls_name_only_tools_the_cli_registers() -> None:
    calls = [o for o in _json_objects(_RUNBOOK) if "tool" in o and "arguments" in o]
    assert calls, "the runbook should show at least one tool call"
    registered = _cli_tool_names()
    assert registered == {RetrievalTool.name}
    for call in calls:
        assert call["tool"] in registered, f"{call['tool']!r} is not registered by the CLI"


@pytest.mark.parametrize(
    ("line", "safe"),
    [
        ("gcloud auth application-default print-access-token >/dev/null && echo ok", True),
        ("gcloud auth application-default print-access-token > $null", True),
        ("gcloud auth application-default print-access-token | Out-Null; if ($?) { 'ok' }", True),
        ("gcloud auth application-default print-access-token", False),
        ("gcloud auth application-default print-access-token 2>/dev/null", False),
        ("gcloud auth application-default print-access-token 2> $null", False),
        ("gcloud auth application-default print-access-token > token.txt", False),
    ],
)
def test_stdout_discard_detection_is_not_fooled_by_stderr_redirects(line: str, safe: bool) -> None:
    assert _discards_stdout(line) is safe


@pytest.mark.parametrize("path", _TOKEN_DOCS, ids=lambda p: p.name)
def test_docs_never_print_a_bearer_token(path: Path) -> None:
    offending = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _TOKEN_COMMAND in line and not _discards_stdout(line)
    ]
    assert offending == [], f"{path.name} prints an OAuth token: {offending}"
