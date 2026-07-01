"""Tests for the structured error hierarchy."""

from __future__ import annotations

import pytest

from mangomas.adapters.llm.lmstudio import LMStudioError
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
    ToolExecutionError,
    ToolNotFound,
    UnknownProvider,
)

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
