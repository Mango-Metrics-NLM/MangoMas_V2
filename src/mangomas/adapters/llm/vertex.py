"""Vertex AI LLM adapter using ``google-cloud-aiplatform`` (Gemini).

Mirrors the shape of :class:`~mangomas.adapters.llm.lmstudio.LMStudioClient`
and satisfies :class:`LLMClient`, :class:`StreamingLLMClient`, and
:class:`PingableLLMClient`. Credentials are sourced from Application
Default Credentials / Workload Identity Federation per ADR-001 —
service-account JSON keys are never accepted by this module.

The Vertex SDK ships a synchronous ``generate_content`` API; we wrap it
in :func:`asyncio.to_thread` so the LLM client remains async at the
boundary. A follow-up will switch to ``generate_content_async`` when we
pin a newer SDK version that exposes it consistently.

``aiplatform.init()`` mutates a process-global; v0.3.0 only constructs
one ``VertexLLMClient`` per orchestrator (single-tenant per process) so
this is fine. Multi-tenancy is a long-term roadmap item.

The ``vertexai`` / ``google.cloud.aiplatform`` imports live inside
function bodies so this module remains importable even when the
optional ``vertex`` extra is not installed; the unit tests inject a
fake ``GenerativeModel`` via the constructor seam.

Readiness ping
--------------
``ping()`` issues a one-token ``generate_content`` call. The call is
cheap but not free — operators running aggressive K8s/Cloud-Run health
probes against ``/readyz`` should consider widening the probe interval
or implementing a cheaper liveness signal upstream. A zero-token
metadata-fetch ping is tracked as a v0.4.0 improvement once the SDK
exposes a stable surface for it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any

from mangomas.config import DEFAULT_ERROR_DETAIL_TRUNCATE
from mangomas.core.agent import Message
from mangomas.errors import LLMBadResponse, LLMTimeout, LLMUnavailable

if TYPE_CHECKING:  # pragma: no cover
    from vertexai.generative_models import GenerativeModel

logger = logging.getLogger(__name__)

_PING_PROMPT: str = "ping"
_PING_MAX_OUTPUT_TOKENS: int = 1


class VertexError(LLMBadResponse):
    """Raised when Vertex AI returns an unexpected or malformed response."""


def _translate_vertex_error(
    exc: BaseException,
    *,
    project: str,
    location: str,
    model: str,
) -> Exception:
    """Map Google SDK exceptions to typed :class:`LLMError` subclasses.

    Imports are deferred so this helper does not pull in the SDK at
    module-load time when the optional ``vertex`` extra is absent.
    """
    from google.api_core import exceptions as gax  # noqa: PLC0415
    from google.auth import exceptions as gauth_exc  # noqa: PLC0415

    detail_ctx = f"project={project}, location={location}, model={model}"
    if isinstance(exc, gauth_exc.DefaultCredentialsError):
        return LLMUnavailable(
            "Vertex AI credentials missing (ADC not configured)",
            detail=f"ADC missing; {detail_ctx}",
        )
    if isinstance(exc, gax.DeadlineExceeded | gax.RetryError):
        return LLMTimeout(
            f"Vertex AI request timed out ({detail_ctx})",
            detail=str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
        )
    if isinstance(exc, gax.ServiceUnavailable | gax.Aborted):
        return LLMUnavailable(
            f"Vertex AI unavailable ({detail_ctx})",
            detail=str(exc)[:DEFAULT_ERROR_DETAIL_TRUNCATE],
        )
    if isinstance(exc, gax.GoogleAPIError):
        return VertexError(
            f"Vertex AI returned an error ({detail_ctx})",
            detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
        )
    return LLMUnavailable(
        f"Vertex AI unreachable ({detail_ctx})",
        detail=f"{type(exc).__name__}: {exc}"[:DEFAULT_ERROR_DETAIL_TRUNCATE],
    )


def _to_vertex_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate :class:`Message` list into Gemini's ``(system_instruction, contents)``.

    ``system`` messages collapse into a single ``system_instruction`` string;
    ``user`` and ``assistant`` map to Gemini's ``user`` / ``model`` roles;
    ``tool`` is rejected (tool-calling is not part of v0.3.0).
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
            continue
        if msg.role == "tool":
            raise LLMBadResponse(
                "Vertex adapter does not yet support tool-role messages",
                detail=f"got role={msg.role!r}",
            )
        gemini_role = "user" if msg.role == "user" else "model"
        contents.append({"role": gemini_role, "parts": [{"text": msg.content}]})
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


class VertexLLMClient:
    """Vertex AI Gemini client satisfying the LLM protocols.

    Parameters
    ----------
    project:
        GCP project id (``MANGOMAS_LLM__PROJECT``).
    location:
        Vertex region, e.g. ``"us-central1"``.
    model:
        Gemini model id, e.g. ``"gemini-1.5-flash"``.
    request_timeout_seconds:
        Per-request deadline; today this primarily applies to streaming
        chunk pulls — the SDK does not honour a top-level timeout
        parameter on every release.
    default_temperature:
        Sampling temperature applied when callers pass ``temperature=None``.
    max_output_tokens:
        Optional ceiling on Gemini ``generation_config.max_output_tokens``.
        ``None`` lets the model use its own default.
    client:
        Optional pre-built ``GenerativeModel`` (test-injection seam). When
        ``None`` the SDK is imported and the model is built lazily on
        first call.
    """

    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str,
        request_timeout_seconds: float,
        default_temperature: float,
        max_output_tokens: int | None,
        client: Any | None = None,
    ) -> None:
        self._project = project
        self._location = location
        self._model_id = model
        self._request_timeout_seconds = request_timeout_seconds
        self._default_temperature = default_temperature
        self._max_output_tokens = max_output_tokens
        self._model: GenerativeModel | None = client

    def _ensure_model(self) -> GenerativeModel:
        """Build the Vertex ``GenerativeModel`` on first use (idempotent)."""
        # The SDK-construction branch is exercised only when the optional
        # ``vertex`` extra is installed; unit tests always inject the model.
        if self._model is None:  # pragma: no cover
            import vertexai  # noqa: PLC0415
            from vertexai.generative_models import GenerativeModel  # noqa: PLC0415

            vertexai.init(project=self._project, location=self._location)
            logger.info(
                "Vertex GenerativeModel initialised",
                extra={
                    "project": self._project,
                    "location": self._location,
                    "model": self._model_id,
                },
            )
            self._model = GenerativeModel(self._model_id)
        return self._model

    def _generation_config(self, temperature: float | None) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "temperature": (self._default_temperature if temperature is None else temperature),
        }
        if self._max_output_tokens is not None:
            cfg["max_output_tokens"] = self._max_output_tokens
        return cfg

    def _log_context(self) -> dict[str, Any]:
        return {
            "project": self._project,
            "location": self._location,
            "model": self._model_id,
        }

    async def complete(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> str:
        """Issue a buffered Gemini completion and return the assistant text."""
        system_instruction, contents = _to_vertex_messages(messages)
        gen_cfg = self._generation_config(temperature)
        model = self._ensure_model()

        def _call() -> Any:
            kwargs: dict[str, Any] = {"generation_config": gen_cfg}
            if system_instruction is not None:
                kwargs["system_instruction"] = system_instruction
            return model.generate_content(contents, **kwargs)

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:
            logger.error(
                "Vertex complete failed",
                extra={"error": type(exc).__name__, **self._log_context()},
            )
            raise _translate_vertex_error(
                exc,
                project=self._project,
                location=self._location,
                model=self._model_id,
            ) from exc
        text = _extract_text(response)
        if not text:
            logger.error(
                "Vertex returned empty/blocked response",
                extra=self._log_context(),
            )
            raise VertexError("Vertex AI returned no candidate text")
        # Trace successful completions for operability — payload bodies
        # are deliberately omitted (only character count + model context).
        logger.debug(
            "Vertex complete succeeded",
            extra={**self._log_context(), "chars": len(text)},
        )
        return text

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields Gemini content tokens."""
        return self._stream_impl(messages, temperature=temperature)

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        temperature: float | None,
    ) -> AsyncGenerator[str, None]:
        system_instruction, contents = _to_vertex_messages(messages)
        gen_cfg = self._generation_config(temperature)
        model = self._ensure_model()

        def _start() -> Any:
            kwargs: dict[str, Any] = {"generation_config": gen_cfg, "stream": True}
            if system_instruction is not None:
                kwargs["system_instruction"] = system_instruction
            return model.generate_content(contents, **kwargs)

        try:
            iterator = await asyncio.to_thread(_start)
        except Exception as exc:
            logger.error(
                "Vertex stream start failed",
                extra={"error": type(exc).__name__, **self._log_context()},
            )
            raise _translate_vertex_error(
                exc,
                project=self._project,
                location=self._location,
                model=self._model_id,
            ) from exc

        # Pull each chunk on a worker thread; ``StopIteration`` signals end.
        _sentinel = object()
        logger.debug("Vertex stream started", extra=self._log_context())

        def _next_chunk(it: Any) -> Any:
            try:
                return next(it)
            except StopIteration:
                return _sentinel

        chunk_count = 0
        while True:
            try:
                chunk = await asyncio.to_thread(_next_chunk, iterator)
            except Exception as exc:
                logger.error(
                    "Vertex stream chunk failed",
                    extra={"error": type(exc).__name__, **self._log_context()},
                )
                raise _translate_vertex_error(
                    exc,
                    project=self._project,
                    location=self._location,
                    model=self._model_id,
                ) from exc
            if chunk is _sentinel:
                logger.debug(
                    "Vertex stream completed",
                    extra={**self._log_context(), "chunks": chunk_count},
                )
                return
            text = _extract_text(chunk)
            if text:
                chunk_count += 1
                yield text

    async def ping(self) -> None:
        """Issue a one-token completion to verify Vertex reachability."""
        gen_cfg = {
            "temperature": 0.0,
            "max_output_tokens": _PING_MAX_OUTPUT_TOKENS,
        }
        model = self._ensure_model()

        def _call() -> Any:
            return model.generate_content(
                [{"role": "user", "parts": [{"text": _PING_PROMPT}]}],
                generation_config=gen_cfg,
            )

        try:
            await asyncio.to_thread(_call)
        except Exception as exc:
            logger.error(
                "Vertex ping failed",
                extra={"error": type(exc).__name__, **self._log_context()},
            )
            raise _translate_vertex_error(
                exc,
                project=self._project,
                location=self._location,
                model=self._model_id,
            ) from exc
        logger.debug("Vertex ping OK", extra=self._log_context())

    async def aclose(self) -> None:
        """No-op — the Vertex SDK has no resource handle to close."""
        logger.debug("Vertex aclose (noop)", extra=self._log_context())


def _extract_text(response: Any) -> str:
    """Return concatenated text from a Vertex GenerateContent response/chunk.

    Tolerates the multiple shapes the SDK returns:

    - ``response.text`` (convenience property — preferred when present)
    - ``response.candidates[].content.parts[].text`` (canonical structure)

    Returns ``""`` when neither shape produces text (blocked safety
    response, empty chunk, missing candidates).
    """
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    candidates = getattr(response, "candidates", None) or []
    parts_text: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text:
                parts_text.append(part_text)
    return "".join(parts_text)
