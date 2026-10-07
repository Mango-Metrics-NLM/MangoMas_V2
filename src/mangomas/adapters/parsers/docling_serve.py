"""docling-serve document parser — one multipart POST to ``/v1/convert/file``.

Implements :class:`~mangomas.adapters.parsers.base.DocumentParser` against an
out-of-process docling-serve (spec-0035 R3, R4, R5, R11 / ADR-0036). The input is an
untrusted operator-supplied binary, so every limit that can be checked locally
is checked **before** any I/O: the suffix (mapped and allow-listed), the size,
and — for OOXML archives — the entry count and compression ratio. The upload
carries a generated filename (``document<suffix>``), never the original, and
pins ``from_formats`` to that one format.

Failures leave as typed errors only: 401/403 → :class:`~mangomas.errors.ConfigError`;
every other HTTP error, timeout, transport failure, oversize body, malformed
body or non-success status → :class:`~mangomas.errors.DocumentParseError`. No
raw ``httpx`` exception escapes, and no error, log record or span attribute
carries a credential, the original filename stem, document text or a serve
response body.

``/v1/convert/source`` is never called: it fetches arbitrary URLs server-side
(an SSRF primitive, ADR-0036 §3).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from mangomas.adapters._http_errors import translate_httpx_parse_error, translate_parser_status
from mangomas.adapters.parsers._auth import GoogleIdTokenProvider, IdTokenProvider
from mangomas.adapters.parsers.base import ParsedDocument
from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE, ParserSettings
from mangomas.errors import ConfigError, DocumentParseError, MangomasError

logger = logging.getLogger(__name__)

# ── docling-serve wire contract ───────────────────────────────────────────────
# Pending live verification — runbook PR 1 task 1.1. These are transcribed from
# docling-serve's documented API (docs/usage.md) and the spec-0035 runbook, not
# from a captured exchange. They live here, and only here, so a correction from
# the spike is a one-place edit.
CONVERT_FILE_PATH: Final[str] = "/v1/convert/file"
FIELD_FILES: Final[str] = "files"
FIELD_FROM_FORMATS: Final[str] = "from_formats"
FIELD_TO_FORMATS: Final[str] = "to_formats"
FIELD_DO_OCR: Final[str] = "do_ocr"
FIELD_DOCUMENT_TIMEOUT: Final[str] = "document_timeout"
FIELD_MAX_NUM_PAGES: Final[str] = "max_num_pages"
TO_FORMAT_MARKDOWN: Final[str] = "md"
API_KEY_HEADER: Final[str] = "X-Api-Key"
AUTHORIZATION_HEADER: Final[str] = "Authorization"
BEARER_SCHEME: Final[str] = "Bearer"
# The upload's filename is generated so the original stem never leaves the host.
GENERATED_FILENAME_STEM: Final[str] = "document"
UPLOAD_CONTENT_TYPE: Final[str] = "application/octet-stream"
# Suffix (lower-case) → docling ``InputFormat`` value. ``.html`` is mapped so an
# operator *can* allow it, but it is outside the default ``allowed_suffixes``
# because of its backends' CVE history (ADR-0036 §7).
SUFFIX_TO_FORMAT: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".pdf": "pdf",
        ".docx": "docx",
        ".pptx": "pptx",
        ".xlsx": "xlsx",
        ".html": "html",
    }
)
# Zip-container formats that get the archive pre-check.
OOXML_SUFFIXES: Final[frozenset[str]] = frozenset({".docx", ".pptx", ".xlsx"})
STATUS_SUCCESS: Final[str] = "success"
STATUS_PARTIAL_SUCCESS: Final[str] = "partial_success"
STATUS_FAILURE: Final[str] = "failure"
STATUS_SKIPPED: Final[str] = "skipped"
RESPONSE_STATUS_FIELD: Final[str] = "status"
RESPONSE_DOCUMENT_FIELD: Final[str] = "document"
RESPONSE_MARKDOWN_FIELD: Final[str] = "md_content"
# Optional; read when present on ``document``, else ``ParsedDocument.pages`` is None.
RESPONSE_PAGES_FIELD: Final[str] = "num_pages"

# ── Observability vocabulary ──────────────────────────────────────────────────
SPAN_NAME: Final[str] = "parser.docling_serve.convert"
EVENT_REQUEST_COMPLETED: Final[str] = "parser_request_completed"
EVENT_REQUEST_FAILED: Final[str] = "parser_request_failed"
EVENT_PARTIAL_SUCCESS: Final[str] = "parser_partial_success"

_LABEL: Final[str] = "docling-serve"
_FORM_TRUE: Final[str] = "true"
_FORM_FALSE: Final[str] = "false"
_MS_PER_SECOND: Final[float] = 1000.0
# An upstream ``status`` is echoed into ``detail`` only when it looks like a
# protocol token; anything else could be content and is reported by type.
_STATUS_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z_]{1,32}")
# Archive errors ``zipfile`` raises for a corrupt or non-zip body.
_ZIP_ERRORS: Final[tuple[type[Exception], ...]] = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    ValueError,
    OSError,
    EOFError,
    NotImplementedError,
)


def check_ooxml_archive(content: bytes, *, max_entries: int, max_ratio: float) -> None:
    """Refuse an OOXML body that is not a zip or that would expand suspiciously.

    Pure (in-memory ``BytesIO``; no I/O). Rejects a body that is not a readable
    zip, one with more than ``max_entries`` members, and one whose total
    declared uncompressed size exceeds ``max_ratio`` times its total compressed
    size — the zip-bomb shape. Uses the sizes the central directory declares,
    which is what a downstream reader trusts too; it does not decompress.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except _ZIP_ERRORS as exc:
        raise DocumentParseError(
            "document is not a readable OOXML archive", detail=type(exc).__name__
        ) from None
    if len(entries) > max_entries:
        raise DocumentParseError(
            "OOXML archive has too many entries",
            detail=f"entries={len(entries)} max_entries={max_entries}",
        )
    uncompressed = sum(info.file_size for info in entries)
    compressed = sum(info.compress_size for info in entries)
    # Multiplication, not division: zero compressed bytes that expand to
    # anything is an unbounded ratio and must be refused, not a ZeroDivisionError.
    if uncompressed > max_ratio * compressed:
        raise DocumentParseError(
            "OOXML archive compression ratio exceeds the limit",
            detail=(f"uncompressed={uncompressed} compressed={compressed} max_ratio={max_ratio}"),
        )


def _shape(value: object) -> str:
    """Describe a payload by type and (for an object) its keys — never its values."""
    if isinstance(value, dict):
        keys = ",".join(sorted(str(k) for k in value))
        return f"type=dict keys=[{keys}]"[:DEFAULT_ERROR_DETAIL_TRUNCATE]
    return f"type={type(value).__name__}"[:DEFAULT_ERROR_DETAIL_TRUNCATE]


def _status_token(status: object) -> str:
    if isinstance(status, str) and _STATUS_TOKEN_RE.fullmatch(status):
        return status
    return f"<{type(status).__name__}>"


def _malformed(reason: str, detail: str) -> DocumentParseError:
    return DocumentParseError(
        f"{_LABEL} returned a malformed response: {reason}",
        detail=detail[:DEFAULT_ERROR_DETAIL_TRUNCATE],
    )


class DoclingServeParser:
    """``DocumentParser`` backed by a docling-serve HTTP service.

    ``api_key`` is the **resolved** key (composition resolves ``secret_ref``
    through the ``SecretsProvider`` seam); it is required when
    ``settings.auth_mode == "api_key"`` and ignored otherwise.
    ``token_provider`` mints identity tokens for ``google_id_token`` mode
    (default :class:`~mangomas.adapters.parsers._auth.GoogleIdTokenProvider`);
    ``clock`` drives the token cache. An injected ``client`` is used as-is and
    never closed here; a client this parser creates is closed by :meth:`aclose`.
    """

    def __init__(
        self,
        settings: ParserSettings,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        token_provider: IdTokenProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._base_url = settings.base_url.rstrip("/")
        self._auth_mode = settings.auth_mode
        if self._auth_mode == "api_key" and not api_key:
            # Names the setting only; there is no value to leak.
            raise ConfigError(
                "MANGOMAS_PARSER__AUTH_MODE='api_key' requires an API key "
                "(set MANGOMAS_PARSER__API_KEY or a resolvable MANGOMAS_PARSER__SECRET_REF)"
            )
        self._api_key = api_key if self._auth_mode == "api_key" else None
        self._token_provider: IdTokenProvider | None = None
        if self._auth_mode == "google_id_token":
            self._token_provider = token_provider or GoogleIdTokenProvider()
        self._audience = settings.id_token_audience or self._base_url
        self._clock = clock
        self._token: str | None = None
        self._token_refresh_at = 0.0
        self._token_lock = asyncio.Lock()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.timeout_seconds)
        self._closed = False

    def __repr__(self) -> str:
        host = urlsplit(self._base_url).hostname or ""
        return f"DoclingServeParser(host={host!r}, auth_mode={self._auth_mode!r})"

    @property
    def token_provider(self) -> IdTokenProvider | None:
        """The identity-token provider in ``google_id_token`` mode, else ``None``."""
        return self._token_provider

    async def parse(self, *, filename: str, content: bytes) -> ParsedDocument:
        """Convert *content* to Markdown; *filename* contributes its suffix only."""
        suffix = PurePath(filename).suffix.lower()
        size = len(content)
        started = time.perf_counter()
        # Exceptions are not auto-recorded: the status/attributes below carry the
        # typed error code, and a recorded traceback is text this module does
        # not control.
        # Acquired per call through the raw OTel API, never at import: an
        # import-time ``get_tracer`` latches telemetry before the CLI configures it.
        with trace.get_tracer(__name__).start_as_current_span(
            SPAN_NAME, record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("parser.suffix", suffix)
            span.set_attribute("parser.bytes", size)
            try:
                docling_format = self._preflight(suffix, content)
                payload = await self._convert(suffix, docling_format, content)
                parsed, status = self._interpret(payload)
            except MangomasError as exc:
                span.set_attribute("error.code", exc.code)
                span.set_status(Status(StatusCode.ERROR, exc.code))
                logger.warning(
                    "docling-serve conversion failed",
                    extra={
                        "event": EVENT_REQUEST_FAILED,
                        "suffix": suffix,
                        "bytes": size,
                        "error_code": exc.code,
                        "error_class": type(exc).__name__,
                    },
                )
                raise
            span.set_attribute("parser.status", status)
        elapsed_ms = (time.perf_counter() - started) * _MS_PER_SECOND
        if parsed.partial:
            logger.warning(
                "docling-serve converted the document only partially",
                extra={"event": EVENT_PARTIAL_SUCCESS, "suffix": suffix, "bytes": size},
            )
        logger.debug(
            "docling-serve conversion completed",
            extra={
                "event": EVENT_REQUEST_COMPLETED,
                "suffix": suffix,
                "bytes": size,
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )
        return parsed

    def _preflight(self, suffix: str, content: bytes) -> str:
        """Every check that needs no network; returns the docling format to pin."""
        docling_format = SUFFIX_TO_FORMAT.get(suffix)
        if docling_format is None or suffix not in self._settings.allowed_suffixes:
            raise DocumentParseError(
                "document format is not accepted by the parser",
                detail=f"suffix={suffix or '<none>'}",
            )
        limit = self._settings.max_file_bytes
        if len(content) > limit:
            raise DocumentParseError(
                "document exceeds the parser size limit",
                detail=f"bytes={len(content)} max_file_bytes={limit}",
            )
        if suffix in OOXML_SUFFIXES:
            check_ooxml_archive(
                content,
                max_entries=self._settings.max_zip_entries,
                max_ratio=self._settings.max_zip_ratio,
            )
        return docling_format

    async def _convert(self, suffix: str, docling_format: str, content: bytes) -> object:
        headers = await self._auth_headers()
        settings = self._settings
        files = {FIELD_FILES: (f"{GENERATED_FILENAME_STEM}{suffix}", content, UPLOAD_CONTENT_TYPE)}
        data: dict[str, Any] = {
            FIELD_FROM_FORMATS: [docling_format],
            FIELD_TO_FORMATS: [TO_FORMAT_MARKDOWN],
            FIELD_DO_OCR: _FORM_TRUE if settings.do_ocr else _FORM_FALSE,
            FIELD_DOCUMENT_TIMEOUT: str(settings.document_timeout_seconds),
            FIELD_MAX_NUM_PAGES: str(settings.max_pages),
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}{CONVERT_FILE_PATH}",
                files=files,
                data=data,
                headers=headers,
            ) as response:
                if not response.is_success:
                    raise translate_parser_status(response.status_code, label=_LABEL)
                body = await self._read_capped(response)
        except httpx.HTTPError as exc:
            raise translate_httpx_parse_error(exc, label=_LABEL) from None
        try:
            return json.loads(body)
        except ValueError:  # JSONDecodeError and UnicodeDecodeError
            raise _malformed("body is not JSON", f"bytes={len(body)}") from None

    async def _read_capped(self, response: httpx.Response) -> bytes:
        """Read the body, refusing it once it passes ``max_response_bytes``."""
        limit = self._settings.max_response_bytes
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise DocumentParseError(
                "parser response exceeds the size limit",
                detail=f"max_response_bytes={limit}",
            )
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise DocumentParseError(
                    "parser response exceeds the size limit",
                    detail=f"max_response_bytes={limit}",
                )
        return bytes(body)

    @staticmethod
    def _interpret(payload: object) -> tuple[ParsedDocument, str]:
        if not isinstance(payload, dict):
            raise _malformed("not a JSON object", _shape(payload))
        status = payload.get(RESPONSE_STATUS_FIELD)
        if status in (STATUS_FAILURE, STATUS_SKIPPED):
            raise DocumentParseError(
                f"{_LABEL} could not convert the document", detail=f"status={status}"
            )
        if status not in (STATUS_SUCCESS, STATUS_PARTIAL_SUCCESS):
            raise DocumentParseError(
                f"{_LABEL} returned an unknown status",
                detail=f"status={_status_token(status)}",
            )
        document = payload.get(RESPONSE_DOCUMENT_FIELD)
        if not isinstance(document, dict):
            raise _malformed("missing document", _shape(payload))
        text = document.get(RESPONSE_MARKDOWN_FIELD)
        if not isinstance(text, str):
            raise _malformed("missing markdown", _shape(document))
        pages = document.get(RESPONSE_PAGES_FIELD)
        page_count = pages if isinstance(pages, int) and not isinstance(pages, bool) else None
        partial = status == STATUS_PARTIAL_SUCCESS
        return ParsedDocument(text=text, pages=page_count, partial=partial), str(status)

    async def _auth_headers(self) -> dict[str, str]:
        if self._api_key is not None:
            return {API_KEY_HEADER: self._api_key}
        if self._token_provider is not None:
            token = await self._identity_token(self._token_provider)
            return {AUTHORIZATION_HEADER: f"{BEARER_SCHEME} {token}"}
        return {}

    async def _identity_token(self, provider: IdTokenProvider) -> str:
        """Return a cached identity token, minting a new one inside the refresh margin."""
        async with self._token_lock:
            if self._token is None or self._clock() >= self._token_refresh_at:
                try:
                    token, expiry = await provider.fetch(self._audience)
                except MangomasError:
                    raise
                except Exception as exc:
                    raise ConfigError(
                        "could not mint an identity token for docling-serve",
                        detail=type(exc).__name__,
                    ) from None
                if not token:
                    raise ConfigError("identity-token provider returned an empty token")
                self._token = token
                self._token_refresh_at = expiry - self._settings.id_token_refresh_margin_seconds
            return self._token

    async def aclose(self) -> None:
        """Close the HTTP client if this parser created it; idempotent."""
        if self._closed:
            return
        self._closed = True
        logger.debug(
            "Closing docling-serve parser",
            extra={"event": "parser_aclose", "owns_client": self._owns_client},
        )
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "API_KEY_HEADER",
    "AUTHORIZATION_HEADER",
    "BEARER_SCHEME",
    "CONVERT_FILE_PATH",
    "EVENT_PARTIAL_SUCCESS",
    "EVENT_REQUEST_COMPLETED",
    "EVENT_REQUEST_FAILED",
    "FIELD_DOCUMENT_TIMEOUT",
    "FIELD_DO_OCR",
    "FIELD_FILES",
    "FIELD_FROM_FORMATS",
    "FIELD_MAX_NUM_PAGES",
    "FIELD_TO_FORMATS",
    "GENERATED_FILENAME_STEM",
    "OOXML_SUFFIXES",
    "RESPONSE_DOCUMENT_FIELD",
    "RESPONSE_MARKDOWN_FIELD",
    "RESPONSE_PAGES_FIELD",
    "RESPONSE_STATUS_FIELD",
    "SPAN_NAME",
    "STATUS_FAILURE",
    "STATUS_PARTIAL_SUCCESS",
    "STATUS_SKIPPED",
    "STATUS_SUCCESS",
    "SUFFIX_TO_FORMAT",
    "TO_FORMAT_MARKDOWN",
    "UPLOAD_CONTENT_TYPE",
    "DoclingServeParser",
    "check_ooxml_archive",
]
