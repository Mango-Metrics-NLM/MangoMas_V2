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
_CREDENTIAL_COMMANDS: Final[re.Pattern[str]] = re.compile(
    r"\b(?:print-access-token|print-identity-token|secrets\s+versions\s+access)\b"
)
_FENCE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ATX_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*|[ \t]*)$")
_BLOCKQUOTE_RE: Final[re.Pattern[str]] = re.compile(r"^ {0,3}>")
_THEMATIC_BREAK_RE: Final[re.Pattern[str]] = re.compile(
    r"^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$|^ {0,3}(?:\*[ \t]*){3,}$"
)
_LIST_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])(?:[ \t]+.*|[ \t]*)$"
)
_HTML_BLOCK_TAGS: Final[str] = (
    r"pre|script|style|textarea|address|article|aside|base|basefont|blockquote|body|"
    r"caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|"
    r"figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|"
    r"link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|option|p|param|search|section|"
    r"summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
_HTML_BLOCK_START_RE: Final[re.Pattern[str]] = re.compile(
    rf"^ {{0,3}}(?:"
    rf"<!--|<\?|<![A-Za-z]|<!\[CDATA\["
    rf"|</?(?:{_HTML_BLOCK_TAGS})(?:[ \t/>]|$)"
    rf"|(?:<[A-Za-z][A-Za-z0-9-]*(?:[ \t]+[^>\n]*?)?/?>|</[A-Za-z][A-Za-z0-9-]*[ \t]*>)[ \t]*$"
    rf")",
    re.IGNORECASE,
)
_GFM_DELIMITER_CELL_RE: Final[re.Pattern[str]] = re.compile(r"^\s*:?-+:?\s*$")
_BACKTICK_RUN_RE: Final[re.Pattern[str]] = re.compile(r"`+")
# `#` starts a shell comment in Bash/PowerShell only at a token boundary (after ASCII blank).
_SHELL_BLANK: Final[str] = r"[ \t]"
_CMD_END_LOOKAHEAD: Final[str] = rf"(?=;|(?<={_SHELL_BLANK})#|&&|\|\||\Z)"
_FLAG_PLACEHOLDER: Final[str] = (
    rf"(?<={_SHELL_BLANK})--[a-z0-9-]+=<[A-Z][A-Z0-9_]*>(?={_SHELL_BLANK})"
)
_QUOTED_SAFE_VALUE: Final[str] = r"(?:\"[A-Za-z0-9_./:-]+\"|'[A-Za-z0-9_./:-]+')"
_STDERR_DISCARD: Final[str] = (
    rf"(?<={_SHELL_BLANK})2(?:>&1|>{_SHELL_BLANK}*(?:/dev/null|(?i:\$null)))"
    rf"(?={_SHELL_BLANK}|;|&&|\|\||\Z)"
)
_PLAIN_PREFIX_CHAR: Final[str] = r"""[^;&|`#<>(){}\\"'\r\n]"""
_PREFIX_FRAGMENT: Final[str] = (
    rf"(?:{_FLAG_PLACEHOLDER}|{_STDERR_DISCARD}|{_QUOTED_SAFE_VALUE}|{_PLAIN_PREFIX_CHAR})"
)
_POST_DISCARD_TAIL: Final[str] = rf"(?:{_SHELL_BLANK}+{_STDERR_DISCARD})*{_SHELL_BLANK}*"
# Matched on pure shell snippets (fenced lines or extracted CommonMark code spans),
# so `` ` `` is always a shell metacharacter and never a Markdown delimiter.
_CMD_STDOUT_DISCARDED: Final[re.Pattern[str]] = re.compile(
    rf"{_PREFIX_FRAGMENT}*?(?:"
    rf"(?<![0-9&*])>{_SHELL_BLANK}*(?:/dev/null|(?i:\$null))(?={_SHELL_BLANK}|;|&&|\|\||\Z)"
    rf"{_POST_DISCARD_TAIL}{_CMD_END_LOOKAHEAD}"
    rf"|(?<!\|)\|(?!\|){_SHELL_BLANK}*(?i:Out-Null)\b{_SHELL_BLANK}*{_CMD_END_LOOKAHEAD}"
    rf")"
)
_TURN_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "ts", "agent", "request", "response", "schema_version", "status", "error_code", "error"}
)
_CLOUD_LOG_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"timestamp", "severity", "logger", "message", "trace_id", "span_id", "correlation_id"}
)


_DEPTH_DELTAS: Final[dict[str, tuple[int, int]]] = {
    "(": (1, 0),
    ")": (-1, 0),
    "{": (0, 1),
    "}": (0, -1),
}


def _split_gfm_row(stripped_line: str) -> tuple[list[str], bool]:
    cells: list[str] = []
    buf: list[str] = []
    slash_run = 0
    saw_unescaped_pipe = False
    ended_on_pipe = False
    for ch in stripped_line:
        ended_on_pipe = False
        if ch == "\\":
            slash_run += 1
            buf.append(ch)
            continue
        if ch == "|":
            if slash_run % 2 == 1:
                buf[-1] = "|"
            else:
                saw_unescaped_pipe = True
                ended_on_pipe = True
                cells.append("".join(buf))
                buf = []
            slash_run = 0
            continue
        slash_run = 0
        buf.append(ch)
    cells.append("".join(buf))
    if stripped_line.startswith("|"):
        cells = cells[1:]
    if ended_on_pipe:
        cells = cells[:-1]
    return cells, saw_unescaped_pipe


def _gfm_row_cells(stripped_line: str) -> list[str]:
    cells, _ = _split_gfm_row(stripped_line)
    return cells


def _is_fence_open(raw_line: str) -> tuple[str, int] | None:
    match = _FENCE_OPEN_RE.match(raw_line)
    if match is None:
        return None
    fence_run, info = match.group(1), match.group(2)
    if fence_run[0] == "`" and "`" in info:
        return None
    return fence_run[0], len(fence_run)


def _is_fence_close(raw_line: str, active_fence: tuple[str, int]) -> bool:
    char, min_len = active_fence
    return re.match(rf"^ {{0,3}}{re.escape(char)}{{{min_len},}}[ \t]*$", raw_line) is not None


def _gfm_delimiter_cols(raw_line: str) -> int | None:
    stripped = raw_line.strip()
    if not stripped:
        return None
    cells, saw_pipe = _split_gfm_row(stripped)
    if not saw_pipe or not cells or not all(_GFM_DELIMITER_CELL_RE.match(c) for c in cells):
        return None
    return len(cells)


def _interrupts_gfm_table(raw_line: str) -> bool:
    if not raw_line.strip() or _is_fence_open(raw_line) is not None:
        return True
    return bool(
        _ATX_HEADING_RE.match(raw_line)
        or _BLOCKQUOTE_RE.match(raw_line)
        or _THEMATIC_BREAK_RE.match(raw_line)
        or _LIST_ITEM_RE.match(raw_line)
        or _HTML_BLOCK_START_RE.match(raw_line)
    )


def _iter_doc_lines(text: str) -> list[tuple[int, str, bool, int | None]]:
    lines = text.splitlines()
    out: list[tuple[int, str, bool, int | None]] = []
    active_fence: tuple[str, int] | None = None
    table_cols: int | None = None
    for idx, raw_line in enumerate(lines):
        if active_fence is not None:
            if _is_fence_close(raw_line, active_fence):
                active_fence = None
                out.append((idx + 1, raw_line, False, None))
            else:
                out.append((idx + 1, raw_line, True, None))
            continue
        opened = _is_fence_open(raw_line)
        if opened is not None:
            table_cols = None
            active_fence = opened
            out.append((idx + 1, raw_line, False, None))
            continue
        if table_cols is not None:
            if _interrupts_gfm_table(raw_line):
                table_cols = None
            else:
                out.append((idx + 1, raw_line, False, table_cols))
                continue
        if (
            raw_line.strip()
            and not _interrupts_gfm_table(raw_line)
            and idx + 1 < len(lines)
            and not _interrupts_gfm_table(lines[idx + 1])
        ):
            delim_cols = _gfm_delimiter_cols(lines[idx + 1])
            if delim_cols is not None:
                table_cols = delim_cols
                out.append((idx + 1, raw_line, False, table_cols))
                continue
        out.append((idx + 1, raw_line, False, None))
    return out


def _extract_markdown_shell_units(segment: str) -> list[str]:
    runs = list(_BACKTICK_RUN_RE.finditer(segment))
    if not runs:
        return [segment]
    code_spans: list[str] = []
    prose_parts: list[str] = []
    cursor = 0
    idx = 0
    while idx < len(runs):
        open_run = runs[idx]
        run_len = len(open_run.group(0))
        close_idx = next(
            (j for j in range(idx + 1, len(runs)) if len(runs[j].group(0)) == run_len),
            None,
        )
        if close_idx is None:
            idx += 1
            continue
        prose_parts.append(segment[cursor : open_run.start()])
        prose_parts.append(" <__CODE_SPAN__> ")
        inner = segment[open_run.end() : runs[close_idx].start()]
        if len(inner) >= 2 and inner.startswith(" ") and inner.endswith(" ") and inner.strip(" "):
            inner = inner[1:-1]
        code_spans.append(inner)
        cursor = runs[close_idx].end()
        idx = close_idx + 1
    prose_parts.append(segment[cursor:])
    return ["".join(prose_parts), *code_spans]


def _scan_prior_shell_state(prior: str) -> tuple[bool, str]:
    quote: str | None = None
    escaped = False
    paren_depth = 0
    brace_depth = 0
    last_boundary = 0
    for idx, ch in enumerate(prior):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if ch == "\\" and quote == '"':
                escaped = True
            elif ch == "`" and quote == '"':
                return False, ""
            elif ch == quote:
                quote = None
            continue
        if ch == "\\":
            escaped = True
        elif ch in {"'", '"'}:
            quote = ch
        elif ch in "#`":
            return False, ""
        elif ch in ";&|":
            last_boundary = idx + 1
        elif ch in _DEPTH_DELTAS:
            dp, db = _DEPTH_DELTAS[ch]
            paren_depth = max(0, paren_depth + dp)
            brace_depth = max(0, brace_depth + db)
    ok = quote is None and not escaped and paren_depth == 0 and brace_depth == 0
    return (ok, prior[last_boundary:] if ok else "")


def _match_discards_stdout(snippet: str, match: re.Match[str]) -> bool:
    ok, cmd_head = _scan_prior_shell_state(snippet[: match.start()])
    if not ok or any(ch in cmd_head for ch in "#<>(){}`\\\"'"):
        return False
    return _CMD_STDOUT_DISCARDED.match(snippet, match.end()) is not None


def _discards_stdout(line: str, *, in_fence: bool = False, in_table: bool = False) -> bool:
    stripped = line.strip()
    if in_fence:
        units = [line]
    else:
        cells = (
            _gfm_row_cells(stripped)
            if (in_table or (stripped.startswith("|") and stripped.endswith("|")))
            else [line]
        )
        units = [unit for cell in cells for unit in _extract_markdown_shell_units(cell)]
    found_any = False
    for unit in units:
        matches = list(_CREDENTIAL_COMMANDS.finditer(unit))
        if not matches:
            continue
        found_any = True
        if not all(_match_discards_stdout(unit, m) for m in matches):
            return False
    return found_any


def _find_table_column_mismatches(text: str) -> list[tuple[int, int, int]]:
    mismatches: list[tuple[int, int, int]] = []
    for line_no, raw_line, in_fence, expected_cols in _iter_doc_lines(text):
        if in_fence or expected_cols is None:
            continue
        actual_cols = len(_gfm_row_cells(raw_line.strip()))
        if actual_cols != expected_cols:
            mismatches.append((line_no, expected_cols, actual_cols))
    return mismatches


def _find_unmasked_credential_lines(text: str) -> list[str]:
    offending: list[str] = []
    for _line_no, raw_line, in_fence, expected_cols in _iter_doc_lines(text):
        if _CREDENTIAL_COMMANDS.search(raw_line) and not _discards_stdout(
            raw_line, in_fence=in_fence, in_table=(expected_cols is not None)
        ):
            offending.append(raw_line.strip())
    return offending


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
        ("gcloud auth application-default print-access-token >/dev/null 2>&1 && echo ok", True),
        ("gcloud auth application-default print-access-token > $null", True),
        ("gcloud auth application-default print-access-token | Out-Null; if ($?) { 'ok' }", True),
        ("gcloud secrets versions access latest --secret=<NAME> >/dev/null && echo ok", True),
        ("gcloud secrets versions access latest --secret=<NAME> | Out-Null", True),
        ("gcloud auth print-identity-token >/dev/null", True),
        (
            "`gcloud secrets versions access latest --secret=<NAME> >/dev/null` or "
            "`gcloud secrets versions access latest --secret=<NAME> | Out-Null`",
            True,
        ),
        (
            "| err | cause | "
            "`gcloud secrets versions access latest --secret=<NAME> \\| Out-Null` | fix |",
            True,
        ),
        ("gcloud auth application-default print-access-token", False),
        ("gcloud secrets versions access latest --secret=<NAME>", False),
        ("gcloud auth application-default print-access-token 2>/dev/null", False),
        ("gcloud auth application-default print-access-token 2> $null", False),
        ("gcloud auth application-default print-access-token > token.txt", False),
        ("gcloud auth application-default print-access-token >/dev/null.txt", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null >token.txt", False),
        ("gcloud secrets versions access latest >/dev/null <input>token.txt", False),
        ("gcloud secrets versions access latest <input>token.txt >/dev/null", False),
        ("gcloud secrets versions access latest --secret=<NAME>token.txt >/dev/null", False),
        ("gcloud secrets versions access latest --secret=<input> >/dev/null", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null --extra", False),
        (">token.txt gcloud auth application-default print-access-token >/dev/null", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null >>token.txt", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null 1>token.txt", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null 1>&2", False),
        ("gcloud secrets versions access latest --secret=example >/dev/null &>token.txt", False),
        ("gcloud secrets versions access latest --secret=example > $null > token.txt", False),
        ("gcloud secrets versions access latest --secret=example | Out-Null > token.txt", False),
        ("echo $(gcloud auth application-default print-access-token) >/dev/null", False),
        ('gcloud secrets versions access latest --filter="x >/dev/null ;"', False),
        ('echo "gcloud auth application-default print-access-token >/dev/null ;"', False),
        (
            "``gcloud auth application-default print-access-token >/dev/null`printf ''` >t.txt``",
            False,
        ),
        ("gcloud secrets versions access latest --secret=<NAME> \\| Out-Null", False),
        (
            "| err | cause | "
            "`gcloud secrets versions access latest --secret=<NAME> | Out-Null` | fix |",
            False,
        ),
        ("gcloud auth application-default print-access-token && echo ok >/dev/null", False),
        ("gcloud auth application-default print-access-token; echo ok > $null", False),
        ("gcloud auth application-default print-access-token || Out-Null", False),
        ("gcloud auth application-default print-access-token | tee token.txt | Out-Null", False),
        ("gcloud auth application-default print-access-token # >/dev/null", False),
        ("gcloud auth application-default print-access-token # | Out-Null", False),
        (
            "`gcloud auth print-access-token >/dev/null` or `gcloud auth print-access-token`",
            False,
        ),
    ],
)
def test_stdout_discard_detection_is_not_fooled_by_stderr_or_chained_redirects(
    line: str, safe: bool
) -> None:
    assert _discards_stdout(line) is safe


def test_fenced_shell_backticks_and_hash_suffixes_do_not_bypass_discard_check() -> None:
    backtick_leak = (
        "gcloud auth application-default print-access-token >/dev/null`printf ''` >token.txt"
    )
    hash_suffix_leak = (
        "gcloud auth application-default print-access-token >/dev/null#suffix >token.txt"
    )
    out_null_hash_leak = (
        "gcloud auth application-default print-access-token | Out-Null#suffix >token.txt"
    )
    stderr_hash_leak = (
        "gcloud auth application-default print-access-token >/dev/null 2>&1#suffix >token.txt"
    )
    nbsp_hash_leak = (
        "gcloud auth application-default print-access-token >/dev/null\u00a0#suffix >token.txt"
    )
    for leaking in (
        backtick_leak,
        hash_suffix_leak,
        out_null_hash_leak,
        stderr_hash_leak,
        nbsp_hash_leak,
    ):
        assert _discards_stdout(leaking, in_fence=True) is False
        assert _discards_stdout(leaking, in_fence=False) is False
        doc = f"```bash\n{leaking}\n```\n"
        assert _find_unmasked_credential_lines(doc) == [leaking]

    spaced_comment = "gcloud auth application-default print-access-token >/dev/null # >token.txt"
    assert _discards_stdout(spaced_comment, in_fence=True) is True


def test_gfm_table_rows_without_outer_pipes_and_block_interruptions() -> None:
    unescaped_table = (
        "| Symptom | Cause | Diagnostic | Remediation |\n"
        "|---|---|---|---|\n"
        "`SecretsResolutionError` | Role | "
        "`gcloud secrets versions access latest --secret=<NAME> | Out-Null` | Grant role\n"
    )
    assert _find_table_column_mismatches(unescaped_table) == [(3, 4, 5)]
    assert len(_find_unmasked_credential_lines(unescaped_table)) == 1

    escaped_table = (
        "| Symptom | Cause | Diagnostic | Remediation |\n"
        "|---|---|---|---|\n"
        "`SecretsResolutionError` | Role | "
        "`gcloud secrets versions access latest --secret=<NAME> \\| Out-Null` | Grant role\n"
    )
    assert _find_table_column_mismatches(escaped_table) == []
    assert _find_unmasked_credential_lines(escaped_table) == []

    for interruption in (
        "- Follow-up instruction",
        "-",
        "1. Numbered follow-up",
        "<div>Note block</div>",
        "<pre>raw block</pre>",
        "<details><summary>More</summary></details>",
        "<!-- trailing comment -->",
    ):
        table_then_block = f"| Col A | Col B |\n|---|---|\n| 1 | 2 |\n{interruption}\n"
        assert _find_table_column_mismatches(table_then_block) == []

    inline_html_row_mismatch = "| Col A | Col B |\n|---|---|\n<code>err</code> | cause | extra\n"
    assert _find_table_column_mismatches(inline_html_row_mismatch) == [(3, 2, 3)]


@pytest.mark.parametrize("path", _TOKEN_DOCS, ids=lambda p: p.name)
def test_docs_markdown_tables_preserve_column_counts(path: Path) -> None:
    mismatches = _find_table_column_mismatches(path.read_text(encoding="utf-8"))
    assert mismatches == [], f"{path.name} has unescaped '|' splitting table cells: {mismatches}"


@pytest.mark.parametrize("path", _TOKEN_DOCS, ids=lambda p: p.name)
def test_docs_never_print_a_bearer_token_or_secret_payload(path: Path) -> None:
    offending = _find_unmasked_credential_lines(path.read_text(encoding="utf-8"))
    assert offending == [], f"{path.name} prints an unmasked token or secret payload: {offending}"
