"""Direct unit tests for the shared adapter error-translation helpers.

These lock the reusable contracts in :mod:`mangomas.adapters._http_errors` and
:mod:`mangomas.adapters._vertex_errors` that the LM Studio / Vertex chat and
embedding adapters all delegate to.
"""

from __future__ import annotations

import httpx

from mangomas.adapters._http_errors import (
    translate_httpx_error,
    translate_httpx_parse_error,
    translate_parser_status,
)
from mangomas.adapters._vertex_errors import translate_vertex_error
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.errors import (
    ConfigError,
    DocumentParseError,
    LLMBadResponse,
    LLMTimeout,
    LLMUnavailable,
)

_BASE_URL = "http://localhost:1234/v1"
_LABEL = "Test upstream"
# A string that must never be echoed from an httpx exception into a parser error.
_PARSER_CANARY = "parser-canary-31c9"


class _CustomBadResponse(LLMBadResponse):
    """Distinguishable bad-response subtype supplied by a caller."""


def _make_vertex_exception(qualname: str, message: str = "boom") -> Exception:
    module, name = qualname.rsplit(".", 1)
    cls: type[Exception] = type(name, (Exception,), {"__module__": module})
    return cls(message)


def _httpx(exc: BaseException) -> Exception:
    return translate_httpx_error(
        exc, base_url=_BASE_URL, label=_LABEL, bad_response=_CustomBadResponse
    )


# ── translate_httpx_error ─────────────────────────────────────────────────────


def test_httpx_timeout_maps_to_llm_timeout() -> None:
    out = _httpx(httpx.ReadTimeout("slow"))
    assert isinstance(out, LLMTimeout)
    assert _LABEL in str(out)


def test_httpx_status_error_uses_supplied_bad_response() -> None:
    request = httpx.Request("POST", f"{_BASE_URL}/embeddings")
    response = httpx.Response(500, request=request)
    out = _httpx(httpx.HTTPStatusError("server error", request=request, response=response))
    assert isinstance(out, _CustomBadResponse)
    assert "500" in str(out)


def test_httpx_other_error_maps_to_unavailable() -> None:
    out = _httpx(httpx.ConnectError("connection refused"))
    assert isinstance(out, LLMUnavailable)


def test_httpx_detail_is_truncated() -> None:
    out = _httpx(httpx.ConnectError("x" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3)))
    assert len(getattr(out, "detail", "")) <= DEFAULT_ERROR_DETAIL_TRUNCATE


# ── translate_vertex_error ────────────────────────────────────────────────────


def test_vertex_timeout_maps_to_llm_timeout() -> None:
    exc = _make_vertex_exception("google.api_core.exceptions.DeadlineExceeded")
    out = translate_vertex_error(exc, project="p")
    assert isinstance(out, LLMTimeout)


def test_vertex_bad_request_defaults_to_llm_bad_response() -> None:
    exc = _make_vertex_exception("google.api_core.exceptions.InvalidArgument")
    out = translate_vertex_error(exc, project="p")
    assert isinstance(out, LLMBadResponse)
    assert not isinstance(out, _CustomBadResponse)


def test_vertex_bad_request_honors_custom_subtype() -> None:
    exc = _make_vertex_exception("google.api_core.exceptions.PermissionDenied")
    out = translate_vertex_error(exc, project="p", bad_request_error=_CustomBadResponse)
    assert isinstance(out, _CustomBadResponse)


def test_vertex_unavailable_and_fallback() -> None:
    unavailable = _make_vertex_exception("google.api_core.exceptions.ServiceUnavailable")
    assert isinstance(translate_vertex_error(unavailable, project="p"), LLMUnavailable)
    mystery = _make_vertex_exception("some.unknown.module.MysteryError")
    assert isinstance(translate_vertex_error(mystery, project="p"), LLMUnavailable)


def test_vertex_detail_is_truncated() -> None:
    long_msg = "x" * (DEFAULT_ERROR_DETAIL_TRUNCATE * 3)
    exc = _make_vertex_exception("google.api_core.exceptions.InvalidArgument", message=long_msg)
    out = translate_vertex_error(exc, project="p")
    assert len(getattr(out, "detail", "")) <= DEFAULT_ERROR_DETAIL_TRUNCATE


# ── parser translators (spec-0035) ────────────────────────────────────────────


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", f"{_BASE_URL}/{_PARSER_CANARY}")
    response = httpx.Response(status_code, request=request, text=_PARSER_CANARY)
    return httpx.HTTPStatusError(_PARSER_CANARY, request=request, response=response)


def test_parser_status_credentials_map_to_config_error() -> None:
    for status_code in (401, 403):
        err = translate_parser_status(status_code, label=_LABEL)
        assert type(err) is ConfigError
        assert str(err) == f"{_LABEL} rejected credentials"
        assert err.detail == f"status_code={status_code}"


def test_parser_status_other_codes_map_to_document_parse_error() -> None:
    err = translate_parser_status(502, label=_LABEL)
    assert isinstance(err, DocumentParseError)
    assert err.detail == "status_code=502"


def test_parse_error_status_exception_never_echoes_its_text() -> None:
    for status_code, expected in ((403, ConfigError), (500, DocumentParseError)):
        err = translate_httpx_parse_error(_status_error(status_code), label=_LABEL)
        assert type(err) is expected
        assert _PARSER_CANARY not in str(err)
        assert _PARSER_CANARY not in err.detail


def test_parse_error_timeout_and_transport_carry_class_name_only() -> None:
    timeout = translate_httpx_parse_error(httpx.ReadTimeout(_PARSER_CANARY), label=_LABEL)
    transport = translate_httpx_parse_error(httpx.ConnectError(_PARSER_CANARY), label=_LABEL)
    assert isinstance(timeout, DocumentParseError)
    assert isinstance(transport, DocumentParseError)
    assert "timed out" in str(timeout)
    assert timeout.detail == "ReadTimeout"
    assert transport.detail == "ConnectError"
    assert _PARSER_CANARY not in str(timeout) + str(transport)
