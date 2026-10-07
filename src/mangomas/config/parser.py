"""Document-parser settings for RAG ingestion (``MANGOMAS_PARSER__*``; spec-0035).

Opt-in and default-off: with ``enabled=False`` no parser is constructed and a
``.txt``/``.md`` ingest is byte-identical to before this group existed. The
parser is consumed by the operator ``rag ingest`` path only, never by the API
service (ADR-0036 §4).

Every limit is strictly positive — there is deliberately no ``0 = off`` value,
because each one bounds untrusted input (file size, archive expansion, response
size) and an "off" spelling is exactly the misconfiguration that would remove
the bound silently. Invariants fail fast at settings load rather than deep in
an ingest run.

All defaults are provisional pending the spec-0035 verification spike and
bake-off; they live here as ``DEFAULT_*`` constants so nothing restates them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ParserAuthMode = Literal["none", "api_key", "google_id_token"]
ParserOnError = Literal["skip", "fail"]

DEFAULT_PARSER_ENABLED: bool = False


DEFAULT_PARSER_PROVIDER: str = "docling_serve"


DEFAULT_PARSER_BASE_URL: str = "http://localhost:5001"


# ``none`` sends no credential; ``api_key`` sends ``X-Api-Key`` (defence in
# depth only); ``google_id_token`` sends a Bearer identity token for a private
# Cloud Run service — the primary control (ADR-0036 §3).
DEFAULT_PARSER_AUTH_MODE: ParserAuthMode = "none"


DEFAULT_PARSER_API_KEY: str | None = None


DEFAULT_PARSER_SECRET_REF: str | None = None


# ``None`` means "use ``base_url``" — resolved by the provider, not here, so an
# operator overriding only ``BASE_URL`` keeps a matching audience.
DEFAULT_PARSER_ID_TOKEN_AUDIENCE: str | None = None


DEFAULT_PARSER_ID_TOKEN_REFRESH_MARGIN: float = 300.0


# The client timeout must exceed the server-side document budget, or the
# client abandons a conversion the server would still have finished.
DEFAULT_PARSER_TIMEOUT_SECONDS: float = 300.0


DEFAULT_PARSER_DOCUMENT_TIMEOUT_SECONDS: float = 240.0


# HTML is excluded on purpose: its backends carry the CVE history (ADR-0036 §7).
DEFAULT_PARSER_ALLOWED_SUFFIXES: tuple[str, ...] = (".pdf", ".docx", ".pptx", ".xlsx")


DEFAULT_PARSER_MAX_FILE_BYTES: int = 50 * 1024 * 1024


DEFAULT_PARSER_MAX_PAGES: int = 500


DEFAULT_PARSER_MAX_RESPONSE_BYTES: int = 20 * 1024 * 1024


DEFAULT_PARSER_MAX_ZIP_ENTRIES: int = 10_000


DEFAULT_PARSER_MAX_ZIP_RATIO: float = 100.0


# ``skip`` logs and counts a failed document and never purges its existing
# vectors; ``fail`` raises (files already processed stay replaced).
DEFAULT_PARSER_ON_ERROR: ParserOnError = "skip"


DEFAULT_PARSER_DO_OCR: bool = False


DEFAULT_PARSER_PARSED_CHUNK_WORDS: int = 300


DEFAULT_PARSER_EMBED_MAX_TOKENS: int | None = None


_SUFFIX_DOT = "."

# Strictly-positive fields, checked by one validator so every message has the
# same shape and names the field.
_POSITIVE_FIELDS: tuple[str, ...] = (
    "id_token_refresh_margin_seconds",
    "timeout_seconds",
    "document_timeout_seconds",
    "max_file_bytes",
    "max_pages",
    "max_response_bytes",
    "max_zip_entries",
    "max_zip_ratio",
    "parsed_chunk_words",
)


class ParserSettings(BaseModel):
    """Document-parser configuration (spec-0035 R2 / ADR-0036).

    Gated by ``enabled`` (default ``False``) like :class:`EmbeddingSettings`.
    ``provider`` selects the parser registry entry (``docling_serve``). The
    ``api_key`` is kept out of ``repr`` so a logged settings object cannot
    carry it; ``secret_ref``, when set, is resolved through the
    ``SecretsProvider`` seam and overrides ``api_key``.
    """

    enabled: bool = DEFAULT_PARSER_ENABLED
    provider: str = DEFAULT_PARSER_PROVIDER
    base_url: str = DEFAULT_PARSER_BASE_URL
    auth_mode: ParserAuthMode = DEFAULT_PARSER_AUTH_MODE
    api_key: str | None = Field(default=DEFAULT_PARSER_API_KEY, repr=False)
    secret_ref: str | None = DEFAULT_PARSER_SECRET_REF
    id_token_audience: str | None = DEFAULT_PARSER_ID_TOKEN_AUDIENCE
    id_token_refresh_margin_seconds: float = DEFAULT_PARSER_ID_TOKEN_REFRESH_MARGIN
    timeout_seconds: float = DEFAULT_PARSER_TIMEOUT_SECONDS
    document_timeout_seconds: float = DEFAULT_PARSER_DOCUMENT_TIMEOUT_SECONDS
    allowed_suffixes: tuple[str, ...] = DEFAULT_PARSER_ALLOWED_SUFFIXES
    max_file_bytes: int = DEFAULT_PARSER_MAX_FILE_BYTES
    max_pages: int = DEFAULT_PARSER_MAX_PAGES
    max_response_bytes: int = DEFAULT_PARSER_MAX_RESPONSE_BYTES
    max_zip_entries: int = DEFAULT_PARSER_MAX_ZIP_ENTRIES
    max_zip_ratio: float = DEFAULT_PARSER_MAX_ZIP_RATIO
    on_error: ParserOnError = DEFAULT_PARSER_ON_ERROR
    do_ocr: bool = DEFAULT_PARSER_DO_OCR
    parsed_chunk_words: int = DEFAULT_PARSER_PARSED_CHUNK_WORDS
    embed_max_tokens: int | None = DEFAULT_PARSER_EMBED_MAX_TOKENS

    @field_validator("allowed_suffixes")
    @classmethod
    def _normalise_suffixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Lower-case, force a leading dot, de-duplicate preserving order."""
        if not value:
            raise ValueError("allowed_suffixes must name at least one suffix")
        normalised: list[str] = []
        for raw in value:
            bare = raw.strip().lower().lstrip(_SUFFIX_DOT)
            if not bare:
                raise ValueError(f"allowed_suffixes entry {raw!r} is empty")
            suffix = f"{_SUFFIX_DOT}{bare}"
            if suffix not in normalised:
                normalised.append(suffix)
        return tuple(normalised)

    @model_validator(mode="after")
    def _check_limits(self) -> ParserSettings:
        for name in _POSITIVE_FIELDS:
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be > 0 (got {value})")
        if self.timeout_seconds <= self.document_timeout_seconds:
            raise ValueError(
                f"timeout_seconds ({self.timeout_seconds}) must be greater than "
                f"document_timeout_seconds ({self.document_timeout_seconds})"
            )
        if self.embed_max_tokens is not None and self.embed_max_tokens <= 0:
            raise ValueError(f"embed_max_tokens must be > 0 when set (got {self.embed_max_tokens})")
        if self.auth_mode == "api_key" and not (self.api_key or self.secret_ref):
            # Names the fields only — never echoes a credential value.
            raise ValueError("auth_mode='api_key' requires api_key or secret_ref to be set")
        return self
