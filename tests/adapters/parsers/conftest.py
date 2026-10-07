"""Shared test fixtures, mock helpers and factories for docling-serve parser tests."""

from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from collections.abc import Iterator
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mangomas.adapters.parsers import DoclingServeParser
from mangomas.config import ParserSettings
from tests.constants.docling import (
    SPEC_DOCLING_CONVERT_PATH,
    SPEC_DOCLING_SOURCE_PATH,
    TEST_DOCLING_BASE_URL,
    TEST_DOCLING_MAX_FILE_BYTES,
    TEST_DOCLING_MAX_RESPONSE_BYTES,
    TEST_DOCLING_MAX_ZIP_ENTRIES,
    TEST_DOCLING_MAX_ZIP_RATIO,
)

_CONVERT_URL = f"{TEST_DOCLING_BASE_URL}{SPEC_DOCLING_CONVERT_PATH}"
_SOURCE_URL = f"{TEST_DOCLING_BASE_URL}{SPEC_DOCLING_SOURCE_PATH}"
_PARSER_LOGGER = "mangomas.adapters.parsers.docling_serve"
_MAX_ENTRIES = TEST_DOCLING_MAX_ZIP_ENTRIES


def _settings(**overrides: Any) -> ParserSettings:
    values: dict[str, Any] = {
        "enabled": True,
        "base_url": TEST_DOCLING_BASE_URL,
        "max_file_bytes": TEST_DOCLING_MAX_FILE_BYTES,
        "max_response_bytes": TEST_DOCLING_MAX_RESPONSE_BYTES,
        "max_zip_entries": TEST_DOCLING_MAX_ZIP_ENTRIES,
        "max_zip_ratio": TEST_DOCLING_MAX_ZIP_RATIO,
    }
    values.update(overrides)
    return ParserSettings(**values)


def _parser(**overrides: Any) -> DoclingServeParser:
    return DoclingServeParser(_settings(**overrides))


def _ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


class _Clock:
    """A settable clock for the token cache."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _multipart(request: httpx.Request) -> EmailMessage:
    """Parse a captured multipart request body with the stdlib MIME parser."""
    body = request.read()
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + body)
    assert isinstance(message, EmailMessage)  # the HTTP policy builds EmailMessage
    return message


def _form_fields(request: httpx.Request) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for part in _multipart(request).iter_parts():
        if part.get_filename() is not None:
            continue
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        fields.setdefault(name, []).append(payload.decode())
    return fields


def _file_parts(request: httpx.Request) -> list[tuple[str, str, bytes]]:
    files: list[tuple[str, str, bytes]] = []
    for part in _multipart(request).iter_parts():
        filename = part.get_filename()
        if filename is None:
            continue
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        files.append((name, filename, payload))
    return files


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _jwt(claims: dict[str, Any]) -> str:
    def seg(obj: dict[str, Any]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.signature"


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Attach an in-memory exporter to the (set-once) global SDK provider."""
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


def _rendered_record(record: logging.LogRecord) -> str:
    return " ".join(f"{k}={v!r}" for k, v in vars(record).items()) + record.getMessage()


def _rendered_spans(exporter: InMemorySpanExporter) -> str:
    parts: list[str] = []
    for span in exporter.get_finished_spans():
        parts.append(span.name)
        parts.append(repr(dict(span.attributes or {})))
        parts.append(repr(span.status.description))
        for event in span.events:
            parts.append(event.name)
            parts.append(repr(dict(event.attributes or {})))
    return " ".join(parts)
