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

from mangomas.adapters.storage._schema import TURN_SCHEMA_VERSION, TurnStatus
from mangomas.agents.planner import ExecutionPlan
from mangomas.agents.reviewer import ReviewResult
from mangomas.composition.rag import _build_rag_tools
from mangomas.rag import RetrievalTool
from mangomas.workflow.graph import WorkflowGraph
from tests.fakes import FakeEmbeddingClient, FakeVectorStore

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_RUNBOOK: Final[Path] = _REPO_ROOT / "docs" / "runbooks" / "gcp-local-demo.md"
_SPEC: Final[Path] = _REPO_ROOT / "specs" / "0049-gcp-integration-demonstration.md"
_TOKEN_DOCS: Final[tuple[Path, ...]] = (
    _RUNBOOK,
    _REPO_ROOT / "docs" / "plans" / "20261109T010000Z-gcp-integration-demonstration-plan.md",
    _REPO_ROOT / ".claude" / "skills" / "mango-gcp-demo" / "SKILL.md",
    _SPEC,
)
_JSON_FENCE: Final[re.Pattern[str]] = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
_REPO_RELPATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:src|scripts|tests|docs|specs|deploy)(?:/[A-Za-z0-9_.-]+)*/?)"
)
_TOKEN_COMMAND: Final[str] = "print-access-token"  # noqa: S105 -- a gcloud verb, not a secret
# A token command is safe only when its *stdout* is discarded on the same line:
# `>/dev/null`, `> $null` or `| Out-Null`. A stderr-only redirect (`2>/dev/null`,
# `2> $null`) or a redirect to a real file still exposes the token, so the
# redirect must not be preceded by a file-descriptor digit or '&'.
_STDOUT_DISCARDED: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9&])>\s*(?:/dev/null|\$null)\b|\|\s*Out-Null\b", re.IGNORECASE
)
_TURN_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "ts", "agent", "request", "response", "schema_version", "status", "error_code", "error"}
)
_CLOUD_LOG_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"timestamp", "severity", "logger", "message", "trace_id", "span_id", "correlation_id"}
)


def _discards_stdout(line: str) -> bool:
    return _STDOUT_DISCARDED.search(line) is not None


def _json_fences(path: Path) -> list[str]:
    """Every fenced ``json`` block in *path*."""
    return _JSON_FENCE.findall(path.read_text(encoding="utf-8"))


def _json_objects(path: Path) -> list[dict[str, Any]]:
    """Every fenced ``json`` block in *path* that parses to a single JSON object."""
    objects: list[dict[str, Any]] = []
    for block in _json_fences(path):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _jsonl_blocks(path: Path) -> list[list[dict[str, Any]]]:
    """Every multi-line JSONL ``json`` block in *path* whose lines parse to JSON objects."""
    blocks: list[list[dict[str, Any]]] = []
    for block in _json_fences(path):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) <= 1:
            continue
        parsed_lines: list[dict[str, Any]] = []
        for ln in lines:
            try:
                item = json.loads(ln)
            except json.JSONDecodeError:
                parsed_lines = []
                break
            if not isinstance(item, dict):
                parsed_lines = []
                break
            parsed_lines.append(item)
        if parsed_lines:
            blocks.append(parsed_lines)
    return blocks


def _cli_tool_names() -> set[str]:
    registry = _build_rag_tools(FakeEmbeddingClient(), FakeVectorStore(), top_k=1)
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


def test_runbook_workflow_graph_examples_validate_against_the_schema() -> None:
    graphs = [o for o in _json_objects(_RUNBOOK) if {"schema_version", "name", "root"} <= o.keys()]
    assert graphs, "the runbook should carry at least one WorkflowGraph example"
    for graph in graphs:
        WorkflowGraph.model_validate(graph)


def test_runbook_persisted_turn_examples_match_storage_schema() -> None:
    turns = [o for o in _json_objects(_RUNBOOK) if o.keys() >= _TURN_REQUIRED_KEYS]
    assert turns, "the runbook should carry at least one persisted turn example"
    valid_statuses = {s.value for s in TurnStatus}
    for turn in turns:
        assert turn["schema_version"] == TURN_SCHEMA_VERSION
        assert turn["status"] in valid_statuses


def test_runbook_cloud_logging_jsonl_examples_match_json_formatter_schema() -> None:
    streams = _jsonl_blocks(_RUNBOOK)
    assert streams, "the runbook should carry at least one Cloud Logging JSONL stream example"
    for stream in streams:
        for entry in stream:
            assert entry.keys() >= _CLOUD_LOG_REQUIRED_KEYS


def test_runbook_tool_calls_name_only_tools_the_cli_registers() -> None:
    calls = [o for o in _json_objects(_RUNBOOK) if "tool" in o and "arguments" in o]
    assert calls, "the runbook should show at least one tool call"
    registered = _cli_tool_names()
    assert registered == {RetrievalTool.name}
    for call in calls:
        assert call["tool"] in registered, f"{call['tool']!r} is not registered by the CLI"


def test_runbook_leaves_zero_unclassified_json_fences() -> None:
    raw_fences = _json_fences(_RUNBOOK)
    single_objects = _json_objects(_RUNBOOK)
    jsonl_streams = _jsonl_blocks(_RUNBOOK)
    assert len(raw_fences) == len(single_objects) + len(jsonl_streams)

    classified = 0
    for obj in single_objects:
        if (
            ("steps" in obj and "kind" not in obj)
            or ({"score", "feedback"} <= obj.keys())
            or ({"schema_version", "name", "root"} <= obj.keys())
            or (obj.keys() >= _TURN_REQUIRED_KEYS)
            or ("tool" in obj and "arguments" in obj)
        ):
            classified += 1
    assert classified == len(single_objects), (
        "every JSON object fence in the runbook must match a known contract"
    )


@pytest.mark.parametrize("path", _TOKEN_DOCS, ids=lambda p: p.name)
def test_docs_reference_only_existing_repo_paths(path: Path) -> None:
    missing: list[tuple[int, str]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in _REPO_RELPATH_RE.finditer(line):
            rel = match.group(1)
            if not (_REPO_ROOT / rel).exists():
                missing.append((line_no, rel))
    assert missing == [], f"{path.name} references non-existent repo paths: {missing}"


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
