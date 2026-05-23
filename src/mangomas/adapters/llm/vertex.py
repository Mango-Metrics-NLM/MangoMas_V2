"""Vertex AI adapter — Gemini chat completions via ``vertexai.generative_models``.

The actual ``vertexai`` / ``google.cloud.aiplatform`` SDK imports are deferred to
:meth:`VertexClient.__init__` so the module is always importable, even when the
``vertex`` extra has not been installed. The class then raises a clear
``ImportError`` pointing at ``pip install mangomas[vertex]`` when the SDK is
genuinely missing and the caller did not inject a test double.

Error translation is qualname-based (``type(exc).__module__ + __qualname__``)
so unit tests can exercise the matrix without installing the Google SDK.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any, Final

from mangomas.config import DEFAULT_LLM_TEMPERATURE, DEFAULT_LLM_TIMEOUT_SECONDS
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

__all__ = ["VertexClient", "VertexError"]

# Minimal prompt used by ``VertexClient.ping`` to exercise the Vertex endpoint.
# Vertex AI does not expose a free-form health endpoint; the cheapest reachable
# probe is a one-token completion. Module-level so the literal is named.
_PING_PROMPT: Final[str] = "ping"


class VertexError(LLMBadResponse):
    """Raised when Vertex AI returns an unexpected or malformed response."""


# ── Error translation (qualname-based; works without SDK installed) ───────────


def _qualname(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


_VERTEX_TIMEOUT_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.DeadlineExceeded",
        "google.api_core.exceptions.RetryError",
    }
)
_VERTEX_UNAVAILABLE_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.ServiceUnavailable",
        "google.api_core.exceptions.InternalServerError",
        "google.api_core.exceptions.GatewayTimeout",
        "google.api_core.exceptions.Aborted",
        "google.auth.exceptions.RefreshError",
        "google.auth.exceptions.DefaultCredentialsError",
    }
)
_VERTEX_BAD_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        "google.api_core.exceptions.InvalidArgument",
        "google.api_core.exceptions.PermissionDenied",
        "google.api_core.exceptions.Unauthenticated",
        "google.api_core.exceptions.NotFound",
        "google.api_core.exceptions.FailedPrecondition",
    }
)


def _translate_vertex_error(exc: BaseException, *, project: str | None) -> Exception:
    """Map a Vertex SDK exception to the typed Mango-Mas error vocabulary."""
    qualname = _qualname(exc)
    detail = f"{type(exc).__name__}: {exc}"[:200]
    if qualname in _VERTEX_TIMEOUT_TYPES:
        return LLMTimeout(f"Vertex AI request timed out (project={project!r})", detail=detail)
    if qualname in _VERTEX_BAD_REQUEST_TYPES:
        return VertexError(f"Vertex AI rejected the request (project={project!r})", detail=detail)
    if qualname in _VERTEX_UNAVAILABLE_TYPES:
        return LLMUnavailable(f"Vertex AI unavailable (project={project!r})", detail=detail)
    return LLMUnavailable(f"Vertex AI request failed (project={project!r})", detail=detail)


# ── Lazy SDK loader ──────────────────────────────────────────────────────────


_SDK_INSTALL_HINT = (
    "Vertex AI SDK is not installed. Install the optional extra: pip install 'mangomas[vertex]'"
)


def _lazy_import_vertex() -> tuple[Any, Any, Any]:  # pragma: no cover - requires vertex extra
    """Import ``vertexai``, ``Content``, ``Part`` lazily.

    Returns a tuple of ``(vertexai_module, Content_cls, Part_cls)``. Raises
    :class:`ImportError` with a clear install hint if the SDK is absent.

    Excluded from coverage because the success path requires the optional
    ``vertex`` extra to be installed; the failure path is exercised in
    ``tests/test_vertex_unit.py::test_constructor_without_sdk_raises_import_error``
    by monkeypatching this helper.
    """
    try:
        # Lazy: SDK is an optional extra; importing at module load would force
        # the dependency on every user, defeating the optional-extra contract.
        import vertexai  # noqa: PLC0415
        from vertexai.generative_models import Content, Part  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    return vertexai, Content, Part


def _lazy_import_credentials() -> Any:  # pragma: no cover - requires vertex extra
    """Import ``google.oauth2.service_account.Credentials`` lazily.

    Used only when explicit credentials are supplied. With Application Default
    Credentials the SDK resolves auth on its own and this helper is not called.
    Coverage exclusion mirrors :func:`_lazy_import_vertex`.
    """
    try:
        # Lazy: same optional-extra contract as ``_lazy_import_vertex``.
        from google.oauth2 import service_account  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    return service_account.Credentials


# ── Adapter ──────────────────────────────────────────────────────────────────


class VertexClient:
    """Vertex AI ``GenerativeModel`` client satisfying ``LLMClient`` protocols.

    Constructor parameters mirror :class:`~mangomas.adapters.llm.lmstudio.LMStudioClient`
    where they overlap. A ``client`` argument is accepted for test injection;
    when supplied, no SDK import is performed.
    """

    def __init__(
        self,
        *,
        project_id: str | None,
        location: str,
        model: str,
        credentials_path: str | None = None,
        credentials_json: str | None = None,
        timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        default_temperature: float = DEFAULT_LLM_TEMPERATURE,
        client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._location = location
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._default_temperature = default_temperature
        # Cached lazy SDK handles (only populated when no client is injected).
        self._vertexai: Any = None
        self._Content: Any = None
        self._Part: Any = None
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = self._init_real_client(
                credentials_path=credentials_path,
                credentials_json=credentials_json,
            )
            self._owns_client = True

    # ── SDK initialisation ────────────────────────────────────────────────

    def _init_real_client(  # pragma: no cover - requires vertex extra
        self,
        *,
        credentials_path: str | None,
        credentials_json: str | None,
    ) -> Any:
        """Initialise the real SDK and return a ``GenerativeModel`` instance.

        Excluded from coverage because every call site requires the optional
        ``vertex`` extra to be installed; the early-failure path (lazy import
        raises ``ImportError``) is exercised in
        ``tests/test_vertex_unit.py::test_constructor_without_sdk_raises_import_error``.
        """
        vertexai, Content, Part = _lazy_import_vertex()
        self._vertexai = vertexai
        self._Content = Content
        self._Part = Part
        credentials = self._resolve_credentials(
            credentials_path=credentials_path,
            credentials_json=credentials_json,
        )
        vertexai.init(
            project=self._project_id,
            location=self._location,
            credentials=credentials,
        )
        # Lazy: ``GenerativeModel`` is part of the optional ``vertexai`` SDK
        # imported above; pulling it out of the eagerly-imported tuple would
        # widen ``_lazy_import_vertex`` for one caller only.
        from vertexai.generative_models import GenerativeModel  # noqa: PLC0415

        return GenerativeModel(self._model)

    @staticmethod
    def _resolve_credentials(
        *,
        credentials_path: str | None,
        credentials_json: str | None,
    ) -> Any | None:
        """Return a ``Credentials`` instance or ``None`` (ADC fallback)."""
        if credentials_json:
            cred_cls = _lazy_import_credentials()
            try:
                info = json.loads(credentials_json)
            except json.JSONDecodeError as exc:
                raise VertexError(
                    "credentials_json is not valid JSON",
                    detail=str(exc)[:200],
                ) from exc
            return cred_cls.from_service_account_info(info)
        if credentials_path:
            cred_cls = _lazy_import_credentials()
            return cred_cls.from_service_account_file(credentials_path)
        return None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _build_contents(self, messages: list[Message]) -> list[Any]:
        """Convert ``Message`` records into Vertex ``Content`` objects.

        Vertex Gemini accepts ``user`` and ``model`` roles. ``system`` and
        ``tool`` messages are folded into the ``user`` role with a textual
        prefix so context is preserved without breaking the role contract.
        """
        if self._Content is None or self._Part is None:
            # When a test client is injected and no SDK is present, pass raw
            # dicts that mirror the SDK shape; the fake just reflects them.
            return [{"role": _vertex_role_for(m.role), "content": _annotate(m)} for m in messages]
        contents: list[Any] = []
        for m in messages:
            role = _vertex_role_for(m.role)
            contents.append(self._Content(role=role, parts=[self._Part.from_text(_annotate(m))]))
        return contents

    def _generation_config(self, temperature: float | None) -> dict[str, Any]:
        return {
            "temperature": (self._default_temperature if temperature is None else temperature),
        }

    # ── Protocol surface ──────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> str:
        """Return the assistant content for ``messages`` (single non-streaming response)."""
        contents = self._build_contents(messages)
        config = self._generation_config(temperature)
        logger.info(
            "Vertex request",
            extra={
                "event": "vertex_request",
                "model": self._model,
                "project": self._project_id,
                "location": self._location,
                "temperature": config["temperature"],
            },
        )
        started = time.monotonic()
        try:
            response = await self._client.generate_content_async(
                contents,
                generation_config=config,
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            logger.error(
                "Vertex request failed",
                extra={
                    "event": "vertex_error",
                    "model": self._model,
                    "project": self._project_id,
                    "error_type": type(exc).__name__,
                    "duration_ms": duration_ms,
                },
            )
            raise _translate_vertex_error(exc, project=self._project_id) from exc
        text = _extract_text(response)
        duration_ms = (time.monotonic() - started) * 1000
        logger.debug(
            "Vertex response",
            extra={
                "event": "vertex_response",
                "model": self._model,
                "duration_ms": duration_ms,
                "content_length": len(text),
            },
        )
        if not text:
            raise VertexError(
                f"Malformed Vertex response: no text content (model={self._model!r})",
            )
        return text

    async def ping(self) -> None:
        """Issue a minimal completion to verify the Vertex endpoint is reachable.

        Vertex AI does not expose a free-form health endpoint; the cheapest
        validation is a one-token completion request. Errors are translated
        through the same exception map as ``complete``.
        """
        try:
            await self._client.generate_content_async(
                [{"role": "user", "content": _PING_PROMPT}]
                if self._Content is None
                else [self._Content(role="user", parts=[self._Part.from_text(_PING_PROMPT)])],
                generation_config={"temperature": 0.0, "max_output_tokens": 1},
            )
        except Exception as exc:
            logger.error(
                "Vertex ping failed",
                extra={
                    "event": "vertex_error",
                    "model": self._model,
                    "project": self._project_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise _translate_vertex_error(exc, project=self._project_id) from exc
        logger.debug(
            "Vertex ping OK",
            extra={"event": "vertex_ping_ok", "model": self._model},
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields content tokens incrementally."""
        return self._stream_impl(messages, temperature=temperature)

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None]:
        contents = self._build_contents(messages)
        config = self._generation_config(temperature)
        logger.debug(
            "Vertex stream start",
            extra={
                "event": "vertex_stream_start",
                "model": self._model,
                "project": self._project_id,
            },
        )
        try:
            stream_result = await self._client.generate_content_async(
                contents,
                generation_config=config,
                stream=True,
            )
        except Exception as exc:
            logger.error(
                "Vertex stream request failed",
                extra={
                    "event": "vertex_error",
                    "model": self._model,
                    "project": self._project_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise _translate_vertex_error(exc, project=self._project_id) from exc

        async for chunk in _aiter(stream_result):
            text = _extract_text(chunk)
            if not text:
                logger.debug(
                    "Skipping empty Vertex chunk",
                    extra={"event": "vertex_chunk_skipped", "model": self._model},
                )
                continue
            yield text

    async def aclose(self) -> None:
        """Release underlying resources.

        The Vertex SDK manages its own gRPC transport pools; the adapter has
        no owned sockets to release. This method exists to satisfy the
        :class:`~mangomas.adapters.llm.base.LLMClient` protocol.
        """
        if self._owns_client:
            close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result


# ── Helpers (module-private) ─────────────────────────────────────────────────


def _vertex_role_for(role: str) -> str:
    """Map Mango-Mas roles to Vertex Gemini roles.

    Gemini supports ``user`` and ``model``; everything else folds into ``user``.
    """
    return "model" if role == "assistant" else "user"


def _annotate(m: Message) -> str:
    """Prefix non-user/assistant roles so context is preserved across the fold."""
    if m.role in ("user", "assistant"):
        return m.content
    return f"[{m.role}] {m.content}"


def _extract_text(response: Any) -> str:
    """Extract text content from a ``GenerationResponse``-like object.

    The SDK exposes ``.text``; the fake client may expose the same. Empty or
    missing text returns an empty string so the caller can decide what to do.
    """
    text = getattr(response, "text", None)
    if text is None:
        return ""
    return str(text)


async def _aiter(stream_result: Any) -> AsyncIterable[Any]:
    """Normalise ``stream_result`` to an async iterable.

    ``GenerativeModel.generate_content_async(stream=True)`` returns an object
    that is itself async-iterable. Some fakes hand back a plain async generator
    instead. This helper accepts either.
    """
    if hasattr(stream_result, "__aiter__"):
        async for item in stream_result:
            yield item
        return
    for item in stream_result:  # pragma: no cover - sync-iterable fallback
        yield item
