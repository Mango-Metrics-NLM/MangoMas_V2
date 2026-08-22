"""Tests for telemetry bootstrap."""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas import telemetry
from mangomas.errors import ConfigError
from mangomas.telemetry import JsonFormatter


def _reset() -> None:
    """Force the telemetry singleton back to unconfigured and clear scoped caches."""
    telemetry._state.configured = False
    telemetry._scoped_tracers.clear()


def test_configure_telemetry_idempotent() -> None:
    _reset()
    telemetry.configure_telemetry(service_name="test", log_level="DEBUG")
    first = telemetry._state.configured
    telemetry.configure_telemetry()
    assert first is True
    assert telemetry._state.configured is True


def test_configure_telemetry_json_format() -> None:
    """configure_telemetry with log_format='json' attaches a JsonFormatter."""
    _reset()
    telemetry.configure_telemetry(service_name="test", log_format="json")
    root_logger = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root_logger.handlers), (
        "Expected a JsonFormatter on the root handler"
    )


def test_get_tracer_returns_tracer() -> None:
    tracer = telemetry.get_tracer("unit")
    with tracer.start_as_current_span("span-x") as span:
        span.set_attribute("k", "v")
    assert tracer is not None


def test_get_tracer_cold_start() -> None:
    """get_tracer() auto-configures telemetry when not yet configured."""
    _reset()
    tracer = telemetry.get_tracer("cold-start-test")
    assert tracer is not None
    assert telemetry._state.configured is True


# ── Exporter selection ────────────────────────────────────────────────────────


def test_build_span_exporter_console_default() -> None:
    """The default token yields the built-in console exporter."""
    exporter = telemetry._build_span_exporter(telemetry.EXPORTER_CONSOLE)
    assert isinstance(exporter, ConsoleSpanExporter)


def test_build_span_exporter_gcp_uses_lazy_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gcp token delegates to the lazy Cloud Trace helper (no SDK needed)."""
    sentinel = object()
    monkeypatch.setattr(telemetry.exporters, "_lazy_cloud_trace_exporter", lambda: sentinel)
    assert telemetry._build_span_exporter(telemetry.EXPORTER_GCP) is sentinel


def test_build_span_exporter_unknown_token_raises() -> None:
    """An unknown exporter token fails loud rather than silently using console."""
    with pytest.raises(ConfigError):
        telemetry._build_span_exporter("bogus")


def test_configure_telemetry_gcp_exporter_builds_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_telemetry(exporter='gcp') constructs the Cloud Trace exporter."""
    called: list[bool] = []

    def _fake_lazy() -> InMemorySpanExporter:
        called.append(True)
        return InMemorySpanExporter()

    monkeypatch.setattr(telemetry.exporters, "_lazy_cloud_trace_exporter", _fake_lazy)
    _reset()
    telemetry.configure_telemetry(service_name="t", exporter=telemetry.EXPORTER_GCP)
    assert called == [True]
    assert telemetry._state.configured is True


def test_build_scoped_tracer_inherit_returns_global() -> None:
    """The default 'inherit' path reuses the global provider (no behaviour change)."""
    _reset()
    tracer = telemetry.build_scoped_tracer("harness.ns", exporter=telemetry.EXPORTER_INHERIT)
    assert tracer is not None
    assert telemetry._state.configured is True


def test_build_scoped_tracer_routes_to_dedicated_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-inherit exporter builds a dedicated provider that receives the spans."""
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry.exporters, "_build_span_exporter", lambda _token: exporter)
    _reset()
    tracer = telemetry.build_scoped_tracer("harness.test", exporter=telemetry.EXPORTER_CONSOLE)
    with tracer.start_as_current_span("harness.span"):
        pass
    assert [span.name for span in exporter.get_finished_spans()] == ["harness.span"]


def test_build_scoped_tracer_caches_by_namespace_and_exporter() -> None:
    """Repeated calls reuse one provider/tracer instead of leaking a new one."""
    _reset()
    first = telemetry.build_scoped_tracer("harness.cache", exporter=telemetry.EXPORTER_CONSOLE)
    second = telemetry.build_scoped_tracer("harness.cache", exporter=telemetry.EXPORTER_CONSOLE)
    assert first is second


@pytest.mark.gcp_trace
def test_gcp_trace_exporter_constructs_with_real_sdk() -> None:
    """Gated: build the real Cloud Trace exporter (requires the gcp extra + ADC)."""
    _reset()
    telemetry.configure_telemetry(service_name="t", exporter=telemetry.EXPORTER_GCP)
    assert telemetry._state.configured is True


# ── JsonFormatter ─────────────────────────────────────────────────────────────


def test_json_formatter_produces_valid_json() -> None:
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["severity"] == "INFO"
    assert parsed["logger"] == "test"


def test_json_formatter_includes_exc_info() -> None:
    """Exception info is serialised into the JSON envelope."""
    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="an error",
            args=(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    output = fmt.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]


def test_json_formatter_forwards_extra_fields() -> None:
    """Extra fields passed via ``extra={}`` appear at the top level."""
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="with extras",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc123"
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed.get("request_id") == "abc123"


@pytest.mark.parametrize(
    ("level", "expected_severity"),
    [("DEBUG", "DEBUG"), ("WARNING", "WARNING"), ("ERROR", "ERROR")],
)
def test_json_formatter_severity_levels(level: str, expected_severity: str) -> None:
    fmt = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=getattr(logging, level),
        pathname=__file__,
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )
    parsed = json.loads(fmt.format(record))
    assert parsed["severity"] == expected_severity


# ── D8: no module-level auto-configuring tracer ───────────────────────────────

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "mangomas"

# `mangomas.telemetry.get_tracer` self-bootstraps: on first use it calls
# `configure_telemetry()` with *hard-coded* defaults (INFO / text / console)
# and `logging.basicConfig(force=True)`. Because `configure_telemetry` is
# idempotent, whoever calls it first wins — so a module-level binding latches
# the whole process at those defaults the moment its module is imported, and
# every later caller with real settings (the FastAPI lifespan,
# `cli._runtime.configure_cli_logging`) silently becomes a no-op.
#
# `tracing.py` itself is exempt: it defines `get_tracer`.
_TRACER_SCAN_EXEMPT = {_SRC_ROOT / "telemetry" / "tracing.py"}

# Non-vacuity floor. The package has far more modules than this; the guard
# exists so a moved `src/` layout fails loudly instead of scanning zero files
# and reporting a green "no violations".
_MIN_SCANNED_MODULES = 50


def _import_time_get_tracer_calls(tree: ast.Module) -> list[int]:
    """Line numbers of import-time `get_tracer(...)` calls in ``tree``.

    "Import time" means anything not inside a function body — module scope,
    class bodies, and `if`/`try`/`with` blocks all run on import. Calls inside
    a `def`/`async def` are exactly the lazy pattern this test wants, so they
    are not reported.

    `trace.get_tracer(...)` (raw OpenTelemetry) is deliberately *not* flagged:
    it returns a `ProxyTracer` that resolves the global provider on each span,
    so it configures nothing and works correctly whenever telemetry is set up.
    """
    hits: list[int] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue  # a call in here is the lazy pattern — that is the fix
            if isinstance(child, ast.Call):
                func = child.func
                if (isinstance(func, ast.Name) and func.id == "get_tracer") or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get_tracer"
                    and not (isinstance(func.value, ast.Name) and func.value.id == "trace")
                ):
                    hits.append(child.lineno)
            visit(child)

    visit(tree)
    return hits


def test_no_module_configures_telemetry_at_import() -> None:
    """D8: no module under ``src/mangomas`` may call ``get_tracer`` at import time.

    This is the mechanical form of a rule the codebase already stated in prose
    and enforced only per-import-chain (``mangomas.api.app``). Four modules had
    drifted past that narrower check — `eval/gate.py`, `eval/baseline.py`,
    `eval/sinks/langfuse.py` and `rag/pipeline.py` — none of which the app
    imports, but all of which the **CLI** does. The result was that every
    `mangomas ...` invocation had telemetry latched at defaults before
    `configure_cli_logging()` ran, so `MANGOMAS_LOG__FORMAT` and
    `MANGOMAS_TELEMETRY__EXPORTER` could never take effect.

    The fix in every case is the house idiom: acquire the tracer *inside* the
    function, via the raw ``opentelemetry.trace.get_tracer``.
    """
    scanned = 0
    violations: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if path in _TRACER_SCAN_EXEMPT:
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.relative_to(_SRC_ROOT.parent.parent)}:{lineno}"
            for lineno in _import_time_get_tracer_calls(tree)
        )

    assert scanned >= _MIN_SCANNED_MODULES, (
        f"only scanned {scanned} modules under {_SRC_ROOT} — the layout moved and "
        "this guard is measuring nothing"
    )
    assert not violations, (
        "import-time get_tracer() call(s) — these latch telemetry at hard-coded "
        "defaults and make every later configure_telemetry() a no-op. Move the "
        "call inside the function and use `opentelemetry.trace.get_tracer`:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.parametrize(
    "module",
    ["mangomas.api.app", "mangomas.cli.main", "mangomas.eval", "mangomas.rag"],
)
def test_importing_entry_point_does_not_configure_telemetry(module: str) -> None:
    """Runtime counterpart to the AST scan, over each real entry-point chain.

    The AST scan cannot see indirect routes (a third-party import that
    configures telemetry, a dynamic `getattr` call). This runs each chain in a
    fresh interpreter, so an already-configured in-process singleton — pytest
    itself configures telemetry — cannot mask the regression.
    """
    code = (
        "import mangomas.telemetry as t;"
        f"import {module};"
        "assert t._state.configured is False, 'telemetry configured at import time'"
    )
    result = subprocess.run(  # noqa: S603 -- trusted: fixed code string + sys.executable
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
