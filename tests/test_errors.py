"""Tests for the structured error hierarchy."""

from __future__ import annotations

import pytest

from mangomas.adapters.embeddings.lmstudio import LMStudioEmbeddingError
from mangomas.adapters.llm.lmstudio import LMStudioError
from mangomas.adapters.llm.vertex import VertexError
from mangomas.api.auth import AuthenticationError
from mangomas.api.errors import _ERROR_STATUS
from mangomas.errors import (
    AgentNotFound,
    ConfigError,
    LLMBadResponse,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    MangomasError,
    MaxStepsExceeded,
    PersistenceError,
    SecretsResolutionError,
    StepTimeout,
    ToolExecutionError,
    ToolNotFound,
    UnknownProvider,
)
from mangomas.eval.dataset import DatasetError
from tests.constants import DEFAULT_AGENT_NAME

# ── MangomasError ─────────────────────────────────────────────────────────────


def test_mangomas_error_stores_message_and_detail() -> None:
    exc = MangomasError("oops", detail="more info")
    assert str(exc) == "oops"
    assert exc.detail == "more info"
    assert exc.code == "mangomas_error"


def test_mangomas_error_default_detail_is_empty() -> None:
    exc = MangomasError("oops")
    assert exc.detail == ""


# ── Hierarchy: MangomasError is the root ──────────────────────────────────────


@pytest.mark.parametrize(
    "cls",
    [
        ConfigError,
        UnknownProvider,
        LLMError,
        LLMUnavailable,
        LLMTimeout,
        LLMBadResponse,
        AgentNotFound,
        PersistenceError,
        MaxStepsExceeded,
        StepTimeout,
        ToolNotFound,
        ToolExecutionError,
        SecretsResolutionError,
    ],
)
def test_all_errors_are_mangomas_errors(cls: type[MangomasError]) -> None:
    assert issubclass(cls, MangomasError)


# ── UnknownProvider ───────────────────────────────────────────────────────────


def test_unknown_provider_message_contains_name() -> None:
    exc = UnknownProvider("fancy-llm", ["lmstudio"])
    assert "fancy-llm" in str(exc)


def test_unknown_provider_detail_lists_available() -> None:
    exc = UnknownProvider("x", ["b", "a"])
    # Available should be sorted in the detail string.
    assert "'a'" in exc.detail
    assert "'b'" in exc.detail


def test_unknown_provider_attrs() -> None:
    exc = UnknownProvider("x", ["lmstudio"])
    assert exc.name == "x"
    assert exc.available == ["lmstudio"]


# ── AgentNotFound ─────────────────────────────────────────────────────────────

# The exact published wire value for a missing agent: the API handler puts
# ``str(exc)`` straight into the envelope's ``message``. Spelled out
# character-for-character rather than rebuilt with ``!r`` — restating
# errors.py's own format string would re-derive the bug instead of detecting it.
_AGENT_NOT_FOUND_MESSAGE = f"Unknown agent: '{DEFAULT_AGENT_NAME}'"


def test_agent_not_found_str_is_the_bare_message() -> None:
    """``KeyError.__str__`` must not re-quote the client-facing message.

    ``AgentNotFound`` co-inherits ``KeyError`` on purpose (see the back-compat
    test below), and ``KeyError.__str__`` returns ``repr(args[0])``. Until
    ``MangomasError`` defined its own ``__str__`` the MRO resolved to it, so
    every 404 body shipped a ``message`` with embedded quotes.
    """
    assert str(AgentNotFound(DEFAULT_AGENT_NAME)) == _AGENT_NOT_FOUND_MESSAGE


def test_agent_not_found_keeps_key_error_back_compat() -> None:
    """Rooting ``__str__`` at MangomasError must not cost the KeyError base.

    The dual inheritance is a documented guarantee in the class docstring, so
    the fix for the rendering may not narrow the bases to get there.
    """
    exc = AgentNotFound(DEFAULT_AGENT_NAME)
    assert isinstance(exc, KeyError)
    with pytest.raises(KeyError) as excinfo:
        raise exc
    assert excinfo.value is exc


def test_agent_not_found_caught_as_key_error() -> None:
    """Back-compat: existing ``except KeyError`` callers still work."""
    with pytest.raises(KeyError):
        raise AgentNotFound("my-agent")


def test_agent_not_found_caught_as_mangomas_error() -> None:
    with pytest.raises(MangomasError):
        raise AgentNotFound("my-agent")


def test_agent_not_found_stores_name() -> None:
    exc = AgentNotFound("my-agent")
    assert exc.agent_name == "my-agent"
    assert "my-agent" in str(exc)


# ── LLM error hierarchy ───────────────────────────────────────────────────────


def test_llm_errors_are_subclasses_of_llm_error() -> None:
    assert issubclass(LLMUnavailable, LLMError)
    assert issubclass(LLMTimeout, LLMError)
    assert issubclass(LLMBadResponse, LLMError)


def test_lmstudio_error_is_llm_bad_response() -> None:
    """The concrete adapter error satisfies the LLMBadResponse hierarchy."""
    assert issubclass(LMStudioError, LLMBadResponse)
    assert issubclass(LMStudioError, LLMError)
    assert issubclass(LMStudioError, MangomasError)


# ── Code attributes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cls", "expected_code"),
    [
        (MangomasError, "mangomas_error"),
        (ConfigError, "config_error"),
        (UnknownProvider, "unknown_provider"),
        (LLMError, "llm_error"),
        (LLMUnavailable, "llm_unavailable"),
        (LLMTimeout, "llm_timeout"),
        (LLMBadResponse, "llm_bad_response"),
        (AgentNotFound, "agent_not_found"),
        (PersistenceError, "persistence_error"),
        (MaxStepsExceeded, "max_steps_exceeded"),
        (StepTimeout, "step_timeout"),
        (ToolNotFound, "tool_not_found"),
        (ToolExecutionError, "tool_execution_error"),
        (SecretsResolutionError, "secrets_resolution_error"),
    ],
)
def test_error_codes(cls: type[MangomasError], expected_code: str) -> None:
    assert cls.code == expected_code


# ── SecretsResolutionError ────────────────────────────────────────────────────


def test_secrets_resolution_error_stores_ref_and_provider() -> None:
    exc = SecretsResolutionError("api-key", "gcp", detail="PermissionDenied")
    assert exc.ref == "api-key"
    assert exc.provider == "gcp"
    assert exc.detail == "PermissionDenied"
    assert "api-key" in str(exc)
    assert "gcp" in str(exc)


# ── MaxStepsExceeded ─────────────────────────────────────────────────────────────


def test_max_steps_exceeded_stores_steps() -> None:
    exc = MaxStepsExceeded(5)
    assert exc.steps == 5
    assert "5" in str(exc)


# ── StepTimeout ───────────────────────────────────────────────────────────────


def test_step_timeout_stores_seconds() -> None:
    exc = StepTimeout(1.5)
    assert exc.seconds == 1.5
    assert "1.5" in str(exc)
    assert "1.5" in exc.detail


# ── ToolNotFound ───────────────────────────────────────────────────────────────


def test_tool_not_found_mirrors_unknown_provider_shape() -> None:
    exc = ToolNotFound("hammer", ["echo", "calc"])
    assert "hammer" in str(exc)
    assert exc.name == "hammer"
    assert "echo" in exc.detail
    assert "calc" in exc.detail


# ── ToolExecutionError ──────────────────────────────────────────────────────────


def test_tool_execution_error_stores_tool_name() -> None:
    exc = ToolExecutionError("Tool exploded", tool_name="hammer")
    assert exc.tool_name == "hammer"
    assert "Tool exploded" in str(exc)


# ── Exhaustive subclass → intended-HTTP-status walk (spec-0022 R14) ───────────
#
# api/errors.py's module docstring promises "every MangomasError must be
# mapped here (directly or via a base class)", but only 8 of the concrete
# classes were ever asserted, one subclass proved the MRO fallback, and
# nothing noticed a NEW subclass landing without a deliberate status
# decision — it would silently resolve to the root's 500. This walk makes
# inheritance a decision: every concrete subclass appears in the intended
# table below, the discovery sweep fails when one is missing, and only the
# recorded exemption may resolve through the root entry.


def _all_error_classes() -> set[type[MangomasError]]:
    # Import the defining modules explicitly (see the module imports above) so
    # the recursive __subclasses__ sweep is deterministic, not import-order-
    # dependent. Scoped to the shipped package: a full-suite run also imports
    # test-local throwaway subclasses (tests/adapters define two), and the
    # status contract governs mangomas, not test scaffolding.
    discovered: set[type[MangomasError]] = set()
    pending = [MangomasError]
    while pending:
        cls = pending.pop()
        for sub in cls.__subclasses__():
            if sub not in discovered:
                if sub.__module__.startswith("mangomas"):
                    discovered.add(sub)
                pending.append(sub)
    return discovered


# The review record for status decisions. Classes mapped directly in
# _ERROR_STATUS restate their status; classes that inherit one list the
# inherited value so the inheritance is deliberate, not accidental.
_INTENDED_STATUS: dict[type[MangomasError], int] = {
    UnknownProvider: 400,
    ConfigError: 400,
    ToolNotFound: 400,
    AuthenticationError: 401,
    AgentNotFound: 404,
    LLMTimeout: 504,
    # The server-side per-step budget elapsed while waiting on the agent's
    # upstream work — mirrors LLMTimeout's 504 (spec-0026).
    StepTimeout: 504,
    LLMUnavailable: 503,
    LLMBadResponse: 502,
    LLMError: 502,
    ToolExecutionError: 502,
    MaxStepsExceeded: 422,
    SecretsResolutionError: 503,
    PersistenceError: 500,
    # Inherited via LLMBadResponse — listed so the inheritance is a decision.
    LMStudioError: 502,
    VertexError: 502,
    LMStudioEmbeddingError: 502,
    # Root-mapped 500 is intended: DatasetError is raised by the eval CLI,
    # which catches it (exit code 2) before any HTTP surface is involved.
    # Adding it to _ERROR_STATUS would be an error-taxonomy change owned by
    # mango-error-taxonomy-dev, not this contract test.
    DatasetError: 500,
}


def _resolved_status(cls: type[MangomasError]) -> int:
    return next(int(_ERROR_STATUS[base]) for base in cls.__mro__ if base in _ERROR_STATUS)


def test_every_discovered_subclass_has_an_intended_status() -> None:
    """A new MangomasError subclass must record its status decision here."""
    discovered = _all_error_classes()
    missing = sorted(cls.__name__ for cls in discovered if cls not in _INTENDED_STATUS)
    assert missing == [], (
        f"MangomasError subclass(es) without a recorded intended status: {missing}. "
        "Add each to _ERROR_STATUS in api/errors.py or record its inherited/"
        "root mapping in _INTENDED_STATUS here."
    )
    # No `cls is not MangomasError` guard: the root is deliberately absent
    # from _INTENDED_STATUS (it is the fallback, not a decision), so such a
    # condition would read as an exemption while never doing anything.
    extinct = sorted(cls.__name__ for cls in _INTENDED_STATUS if cls not in discovered)
    assert extinct == [], f"intended-status rows for classes that no longer exist: {extinct}"


_INTENDED_STATUS_CLASSES = sorted(_INTENDED_STATUS, key=lambda cls: cls.__name__)


@pytest.mark.parametrize(
    "cls", _INTENDED_STATUS_CLASSES, ids=[cls.__name__ for cls in _INTENDED_STATUS_CLASSES]
)
def test_subclass_resolves_to_its_intended_status(cls: type[MangomasError]) -> None:
    assert _resolved_status(cls) == _INTENDED_STATUS[cls]


def test_no_unexempted_class_resolves_through_the_root_fallback() -> None:
    """Only the recorded exemption may map to a status via bare MangomasError.

    Everything else must hit a non-root _ERROR_STATUS entry somewhere in its
    MRO — a class that only resolves through the root is an undecided 500
    wearing a green test.
    """
    root_exempt = {DatasetError}
    for cls in _all_error_classes() - root_exempt:
        non_root_hit = any(
            base in _ERROR_STATUS for base in cls.__mro__ if base is not MangomasError
        )
        assert non_root_hit, (
            f"{cls.__name__} resolves only via the root MangomasError→500 "
            "fallback; decide its status in _ERROR_STATUS or exempt it here."
        )


# ── Exhaustive subclass → message-rendering walk ──────────────────────────────
#
# The status walk above covers one half of the error envelope; this covers the
# other. ``api/errors.py`` publishes ``str(exc)`` verbatim as the body's
# ``message``, so a subclass that co-inherits a builtin carrying its own
# ``__str__`` silently reshapes a wire value with no test in sight —
# ``AgentNotFound`` did exactly that through ``KeyError``. Reuses the same
# recursive discovery as the status walk, so a new subclass is swept the day
# it lands.

_RENDERING_PROBE_MESSAGE = "probe message"


def test_every_subclass_renders_its_message_verbatim() -> None:
    """``str(exc)`` is the message handed to ``MangomasError.__init__``."""
    for cls in _all_error_classes() | {MangomasError}:
        # Initialise through the root, bypassing each subclass's own __init__:
        # the signatures differ (steps / ref+provider / name+available / ...),
        # so this isolates the rendering contract and needs no per-class
        # constructor table to stay complete.
        exc = cls.__new__(cls)
        MangomasError.__init__(exc, _RENDERING_PROBE_MESSAGE)
        assert str(exc) == _RENDERING_PROBE_MESSAGE, (
            f"{cls.__name__} renders {str(exc)!r} instead of the message it was "
            "given; a co-inherited __str__ is shadowing MangomasError's."
        )
        assert cls.__str__ is MangomasError.__str__, (
            f"{cls.__name__} resolves __str__ to "
            f"{cls.__str__.__qualname__}, not MangomasError.__str__; the JSON "
            "envelope's message is whatever that returns."
        )
