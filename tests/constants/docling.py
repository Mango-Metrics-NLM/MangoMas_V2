"""docling-serve adapter test constants and fixture loader (spec-0035 M4).

Literal ``SPEC_*`` pins restate the spec / runbook wire contract on purpose:
comparing the adapter's constant against itself would be a tautology, so these
name the drift if either side moves. Everything else is a test-scoped value
that never resolves to a real endpoint or credential.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

# ── Spec pins (deliberately *not* re-exported from the adapter) ──────────────
SPEC_DOCLING_CONVERT_PATH: Final[str] = "/v1/convert/file"
SPEC_DOCLING_SOURCE_PATH: Final[str] = "/v1/convert/source"
SPEC_DOCLING_API_KEY_HEADER: Final[str] = "X-Api-Key"
SPEC_DOCLING_FILES_FIELD: Final[str] = "files"
SPEC_DOCLING_FROM_FORMATS_FIELD: Final[str] = "from_formats"
SPEC_DOCLING_TO_FORMATS_FIELD: Final[str] = "to_formats"
SPEC_DOCLING_DO_OCR_FIELD: Final[str] = "do_ocr"
SPEC_DOCLING_DOCUMENT_TIMEOUT_FIELD: Final[str] = "document_timeout"
SPEC_DOCLING_MAX_NUM_PAGES_FIELD: Final[str] = "max_num_pages"
SPEC_DOCLING_FORBIDDEN_FIELD: Final[str] = "ocr_engine"
SPEC_DOCLING_TO_FORMAT_MD: Final[str] = "md"
SPEC_DOCLING_PROVIDER: Final[str] = "docling_serve"
SPEC_DOCLING_PARSER_CLOSE_LABEL: Final[str] = "parser"
SPEC_DOCLING_SPAN_NAME: Final[str] = "parser.docling_serve.convert"
SPEC_DOCLING_EVENT_PARTIAL: Final[str] = "parser_partial_success"
SPEC_DOCLING_EVENT_COMPLETED: Final[str] = "parser_request_completed"
SPEC_DOCLING_EVENT_FAILED: Final[str] = "parser_request_failed"
SPEC_DOCLING_CREDENTIALS_MESSAGE: Final[str] = "docling-serve rejected credentials"
# Suffix → docling ``from_formats`` value (lower-case), per spec R3.
SPEC_DOCLING_FORMATS: Final[dict[str, str]] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}

# ── Env-var names not already pinned in tests.constants.fixtures ────────────
HARNESS_ENABLED_ENV: Final[str] = "MANGOMAS_HARNESS__ENABLED"
PARSER_BASE_URL_ENV: Final[str] = "MANGOMAS_PARSER__BASE_URL"
DB_URL_ENV: Final[str] = "MANGOMAS_DB__URL"
TEST_IN_MEMORY_DB_URL: Final[str] = "sqlite:///:memory:"

# ── Endpoint / identity (never resolved; respx intercepts) ───────────────────
TEST_DOCLING_BASE_URL: Final[str] = "http://docling.test"
TEST_DOCLING_AUDIENCE: Final[str] = "https://docling-private.test"
TEST_DOCLING_BASE_HOST: Final[str] = "docling.test"

# ── Documents ────────────────────────────────────────────────────────────────
# A distinctive stem that must never reach the wire or a log record.
TEST_DOCLING_ORIGINAL_STEM: Final[str] = "q3-board-minutes-confidential"
TEST_DOCLING_PDF_NAME: Final[str] = f"{TEST_DOCLING_ORIGINAL_STEM}.pdf"
TEST_DOCLING_UPPER_PDF_NAME: Final[str] = f"{TEST_DOCLING_ORIGINAL_STEM}.PDF"
TEST_DOCLING_DOCX_NAME: Final[str] = f"{TEST_DOCLING_ORIGINAL_STEM}.docx"
TEST_DOCLING_HTML_NAME: Final[str] = f"{TEST_DOCLING_ORIGINAL_STEM}.html"
TEST_DOCLING_UNKNOWN_NAME: Final[str] = f"{TEST_DOCLING_ORIGINAL_STEM}.exe"
TEST_DOCLING_NO_SUFFIX_NAME: Final[str] = TEST_DOCLING_ORIGINAL_STEM
TEST_DOCLING_GENERATED_PDF_NAME: Final[str] = "document.pdf"
TEST_DOCLING_PDF_BYTES: Final[bytes] = b"%PDF-1.7 synthetic body"
TEST_DOCLING_PAGES: Final[int] = 7
TEST_DOCLING_PAGES_FIELD: Final[str] = "num_pages"

# ── Limits (small, so boundaries are cheap to build) ─────────────────────────
TEST_DOCLING_MAX_FILE_BYTES: Final[int] = 64
TEST_DOCLING_MAX_RESPONSE_BYTES: Final[int] = 4096
TEST_DOCLING_MAX_ZIP_ENTRIES: Final[int] = 4
TEST_DOCLING_MAX_ZIP_RATIO: Final[float] = 10.0
# Highly compressible payload: deflates far past TEST_DOCLING_MAX_ZIP_RATIO.
TEST_DOCLING_ZIP_BOMB_PAYLOAD_BYTES: Final[int] = 100_000
TEST_DOCLING_ZIP_MEMBER: Final[str] = "word/document.xml"
TEST_DOCLING_ZIP_SMALL_MEMBER: Final[bytes] = b"<w:document>ok</w:document>"

# ── Secrets / canaries ───────────────────────────────────────────────────────
TEST_DOCLING_SENTINEL_KEY: Final[str] = "sentinel-docling-key-9f2c41"
TEST_DOCLING_RESOLVED_KEY: Final[str] = "resolved-docling-key-77ab03"
TEST_DOCLING_SECRET_REF: Final[str] = "docling-api-key-ref"  # noqa: S105  a ref name
TEST_DOCLING_UNRESOLVED_REF: Final[str] = "docling-missing-ref"
TEST_DOCLING_DOC_CANARY: Final[str] = "CANARY-DOCTEXT-5d1e9a"
TEST_DOCLING_BODY_CANARY: Final[str] = "CANARY-SERVEBODY-0c77b2"

# ── Identity tokens (injected provider; never minted against Google) ─────────
TEST_ID_TOKEN_FIRST: Final[str] = "id-token-first"  # noqa: S105  a fake token
TEST_ID_TOKEN_SECOND: Final[str] = "id-token-second"  # noqa: S105  a fake token
TEST_ID_TOKEN_NOW: Final[float] = 1_000_000.0
TEST_ID_TOKEN_LIFETIME: Final[float] = 3600.0
TEST_ID_TOKEN_REFRESH_MARGIN: Final[float] = 120.0

# ── Fixtures ─────────────────────────────────────────────────────────────────
DOCLING_FIXTURE_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "fixtures" / "docling_serve"
)
DOCLING_FIXTURE_SUCCESS: Final[str] = "success"
DOCLING_FIXTURE_PARTIAL: Final[str] = "partial_success"
DOCLING_FIXTURE_FAILURE: Final[str] = "failure"
DOCLING_FIXTURE_SKIPPED: Final[str] = "skipped"


def load_docling_fixture(name: str) -> dict[str, Any]:
    """Return a fresh copy of ``tests/fixtures/docling_serve/<name>.json``."""
    data = json.loads((DOCLING_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"fixture {name!r} is not a JSON object"
    return data
