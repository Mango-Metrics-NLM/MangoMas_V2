"""Tests for the default-off GenAI invoke_agent span helper."""

from __future__ import annotations

import ast
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.constants import DEFAULT_AGENT_NAME

from mangomas.cognitive.constants import (
    GENAI_ATTR_AGENT_NAME,
    GENAI_ATTR_OPERATION_NAME,
    GENAI_ATTR_SEMCONV_STATUS,
    GENAI_ATTR_SYSTEM,
    GENAI_INVOKE_AGENT_SPAN,
    GENAI_OPERATION_INVOKE_AGENT,
    GENAI_SEMCONV_STATUS,
    GENAI_SYSTEM_NAME,
)
from mangomas.cognitive.genai import genai_invoke_agent_span

_EXPORTER: InMemorySpanExporter = InMemorySpanExporter()
_GENAI_PATH = Path(__file__).resolve().parents[2] / "src" / "mangomas" / "cognitive" / "genai.py"


def _init_exporter() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        return
    new_provider = TracerProvider()
    new_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(new_provider)


_init_exporter()


def test_helper_is_noop_when_disabled() -> None:
    _EXPORTER.clear()
    with genai_invoke_agent_span(enabled=False, agent_name=DEFAULT_AGENT_NAME) as span:
        assert span is None
    names = [item.name for item in _EXPORTER.get_finished_spans()]
    assert GENAI_INVOKE_AGENT_SPAN not in names


def test_helper_opens_development_alias_when_enabled() -> None:
    _EXPORTER.clear()
    with genai_invoke_agent_span(enabled=True, agent_name=DEFAULT_AGENT_NAME) as span:
        assert span is not None
    finished = [
        item for item in _EXPORTER.get_finished_spans() if item.name == GENAI_INVOKE_AGENT_SPAN
    ]
    assert len(finished) == 1
    attributes = dict(finished[0].attributes or {})
    assert attributes[GENAI_ATTR_OPERATION_NAME] == GENAI_OPERATION_INVOKE_AGENT
    assert attributes[GENAI_ATTR_AGENT_NAME] == DEFAULT_AGENT_NAME
    assert attributes[GENAI_ATTR_SYSTEM] == GENAI_SYSTEM_NAME
    assert attributes[GENAI_ATTR_SEMCONV_STATUS] == GENAI_SEMCONV_STATUS


def test_otel_import_is_function_local() -> None:
    tree = ast.parse(_GENAI_PATH.read_text(encoding="utf-8"))
    top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.append(node.module)
    assert not any(
        name == "opentelemetry" or name.startswith("opentelemetry.") for name in top_level
    )
