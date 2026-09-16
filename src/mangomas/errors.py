"""Structured error hierarchy for Mango-Mas.

Every public function that can fail raises a subclass of ``MangomasError``.
All subclasses map to HTTP status codes via ``_ERROR_STATUS`` in ``api/errors.py``.
"""

from __future__ import annotations

__all__ = [
    "AgentNotFound",
    "ConfigError",
    "LLMBadResponse",
    "LLMError",
    "LLMTimeout",
    "LLMUnavailable",
    "MangomasError",
    "MaxStepsExceeded",
    "PersistenceError",
    "SecretsResolutionError",
    "StepTimeout",
    "ToolExecutionError",
    "ToolNotFound",
    "UnknownProvider",
]


class MangomasError(Exception):
    """Root exception.  Carries a ``code`` string for JSON error envelopes."""

    code: str = "mangomas_error"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail

    def __str__(self) -> str:
        """Render *message* verbatim, whatever else the subclass co-inherits.

        The API handler publishes ``str(exc)`` as the error envelope's
        ``message`` field, so this is a wire value. Rooting the rendering here
        makes it identical for every subclass instead of depending on each
        one's MRO: ``AgentNotFound`` co-inherits ``KeyError`` (a deliberate
        back-compat guarantee), and ``KeyError.__str__`` returns
        ``repr(args[0])``, so every 404 body used to ship
        ``'"Unknown agent: \'chat\'"'`` — embedded quotes included.

        ``Exception.__str__`` is bound explicitly rather than reached via
        ``super()``: on ``AgentNotFound``'s MRO the class after
        ``MangomasError`` is ``KeyError``, so ``super()`` here would resolve
        right back to the offending implementation.
        """
        return Exception.__str__(self)


# ── Config / Registry ─────────────────────────────────────────────────────────


class ConfigError(MangomasError):
    """Raised for invalid or missing configuration."""

    code = "config_error"


class UnknownProvider(ConfigError):
    """Raised when a registry look-up finds no matching provider."""

    code = "unknown_provider"

    def __init__(self, name: str, available: list[str]) -> None:
        super().__init__(
            f"Unknown provider {name!r}.",
            detail=f"Available: {sorted(available)!r}",
        )
        self.name = name
        self.available = available


# ── Secrets ───────────────────────────────────────────────────────────────────


class SecretsResolutionError(MangomasError):
    """Raised when a cloud secrets backend fails in strict mode.

    Only raised when ``SecretsSettings.strict`` is ``True``; by default (ADR-002)
    backends collapse auth/permission/timeout failures to ``None``. See ADR-0010.
    The *detail* carries only the exception class name — never the secret value,
    resource path, or version.
    """

    code = "secrets_resolution_error"

    def __init__(self, ref: str, provider: str, *, detail: str = "") -> None:
        super().__init__(
            f"Failed to resolve secret {ref!r} from provider {provider!r}.",
            detail=detail,
        )
        self.ref = ref
        self.provider = provider


# ── LLM ───────────────────────────────────────────────────────────────────────


class LLMError(MangomasError):
    """Base class for LLM adapter errors."""

    code = "llm_error"


class LLMUnavailable(LLMError):
    """LLM endpoint is unreachable."""

    code = "llm_unavailable"


class LLMTimeout(LLMError):
    """LLM request timed out."""

    code = "llm_timeout"


class LLMBadResponse(LLMError):
    """LLM returned a malformed or unexpected response."""

    code = "llm_bad_response"


# ── Agent ─────────────────────────────────────────────────────────────────────


class AgentNotFound(MangomasError, KeyError):
    """Raised when the requested agent is not registered.

    Subclasses *both* ``MangomasError`` and ``KeyError`` so existing
    ``except KeyError`` callers continue to work without modification.
    """

    code = "agent_not_found"

    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown agent: {name!r}", detail=f"agent_name={name!r}")
        self.agent_name = name


# ── Persistence ───────────────────────────────────────────────────────────────


class PersistenceError(MangomasError):
    """Raised for storage adapter failures."""

    code = "persistence_error"


# ── Control loop ──────────────────────────────────────────────────────────────


class MaxStepsExceeded(MangomasError):
    """Raised when an acceptance function is provided but never satisfied."""

    code = "max_steps_exceeded"

    def __init__(self, steps: int) -> None:
        super().__init__(
            f"Agent did not satisfy acceptance criteria in {steps} step(s).",
            detail=f"max_steps={steps}",
        )
        self.steps = steps


class StepTimeout(MangomasError):
    """Raised when a single dispatch step exceeds the configured per-step timeout.

    Only raised when the orchestrator is constructed with ``loop_settings``
    (the composition root wires ``Settings.loop``); a bare ``Orchestrator``
    applies no step timeout. See spec-0026 / ADR-0026.
    """

    code = "step_timeout"

    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"Agent step exceeded the per-step timeout of {seconds} second(s).",
            detail=f"step_timeout_seconds={seconds}",
        )
        self.seconds = seconds


# ── Tool calling ──────────────────────────────────────────────────────────────


class ToolNotFound(MangomasError):
    """Raised when a requested tool is not registered."""

    code = "tool_not_found"

    def __init__(self, name: str, available: list[str]) -> None:
        super().__init__(
            f"Unknown tool {name!r}.",
            detail=f"Available: {sorted(available)!r}",
        )
        self.name = name
        self.available = available


class ToolExecutionError(MangomasError):
    """Raised when a tool raises during execution."""

    code = "tool_execution_error"

    def __init__(self, message: str, *, tool_name: str, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.tool_name = tool_name
