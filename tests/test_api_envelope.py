"""Envelope-shape and facade-identity tests for the api/ split (spec 0014 / M11).

The JSON error envelope must have exactly one construction site —
``api/errors.error_envelope`` — shared by the middleware backpressure
rejections (413/503) and the ``MangomasError`` exception handler. These tests
pin both paths to that single function and to the identical
``{"error", "message"[, "detail"]}`` key shape, and pin the permanent
``api.app`` facade re-exports (ADR-0019) to the objects in their new home
modules.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mangomas.api import app as app_module
from mangomas.api import errors as api_errors
from mangomas.api import middleware as api_middleware
from mangomas.api import models as api_models
from mangomas.api.app import create_app
from mangomas.api.errors import error_envelope
from mangomas.api.middleware import ConcurrencyLimitMiddleware, MaxBodySizeMiddleware
from mangomas.core import Orchestrator
from tests.constants import BACKPRESSURE_MAX_BODY_BYTES

# The two-key envelope produced by the middleware rejections and by any
# MangomasError without a detail; ``detail`` joins only when one is carried.
ENVELOPE_KEYS: frozenset[str] = frozenset({"error", "message"})
ENVELOPE_DETAIL_KEY: str = "detail"

# Handed to ConcurrencyLimitMiddleware directly (not via settings, where 0 means
# "off"): an in-flight cap of zero makes the very first request exceed it, so
# the 503 rejection fires deterministically without real concurrency.
ALWAYS_SATURATED_MAX_CONCURRENT: int = 0

# Injected by the patched constructor in the runtime-sharing proof below.
ENVELOPE_MARKER_KEY: str = "marker"
ENVELOPE_MARKER_VALUE: str = "shared-envelope"

_MSG = {"messages": [{"role": "user", "content": "hi"}]}
_ENVELOPE_CODE = "some_code"
_ENVELOPE_MESSAGE = "some message"
_ENVELOPE_DETAIL = "some detail"

_GHOST_INVOKE_ROUTE = "/agents/ghost/invoke"
_ECHO_ROUTE = "/echo"


def _backpressure_app() -> FastAPI:
    """Bare app with both guards, mirroring ``_install_backpressure`` ordering.

    The concurrency guard is added first (inner) and permanently saturated; the
    body-size guard second (outer). An oversized request is rejected with 413
    before it reaches the concurrency guard; anything else 503s.
    """
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, max_concurrent=ALWAYS_SATURATED_MAX_CONCURRENT)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=BACKPRESSURE_MAX_BODY_BYTES)

    @app.post(_ECHO_ROUTE)
    async def echo() -> dict[str, bool]:  # pragma: no cover — every request rejected
        return {"ok": True}

    return app


# ── error_envelope unit shape ─────────────────────────────────────────────────


def test_error_envelope_two_key_shape() -> None:
    body = error_envelope(_ENVELOPE_CODE, _ENVELOPE_MESSAGE)
    assert body == {"error": _ENVELOPE_CODE, "message": _ENVELOPE_MESSAGE}
    assert set(body) == ENVELOPE_KEYS


def test_error_envelope_adds_detail_only_when_non_empty() -> None:
    assert set(error_envelope(_ENVELOPE_CODE, _ENVELOPE_MESSAGE, "")) == ENVELOPE_KEYS
    body = error_envelope(_ENVELOPE_CODE, _ENVELOPE_MESSAGE, _ENVELOPE_DETAIL)
    assert body[ENVELOPE_DETAIL_KEY] == _ENVELOPE_DETAIL
    assert set(body) == ENVELOPE_KEYS | {ENVELOPE_DETAIL_KEY}


# ── One construction site repo-wide ───────────────────────────────────────────


def test_middleware_imports_the_shared_envelope_function() -> None:
    """The middleware builds its bodies via the exact same function object."""
    assert api_middleware.error_envelope is api_errors.error_envelope


def test_backpressure_and_handler_bodies_share_key_shape(orchestrator: Orchestrator) -> None:
    """413/503 middleware bodies and the handler body carry the same envelope."""
    with TestClient(_backpressure_app()) as client:
        too_large = client.post(_ECHO_ROUTE, content=b"x" * (BACKPRESSURE_MAX_BODY_BYTES + 1))
        saturated = client.post(_ECHO_ROUTE)
    assert too_large.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert saturated.status_code == HTTPStatus.SERVICE_UNAVAILABLE

    with TestClient(create_app(orchestrator=orchestrator)) as client:
        handled = client.post(_GHOST_INVOKE_ROUTE, json=_MSG)
    assert handled.status_code == HTTPStatus.NOT_FOUND

    # Identical key shape: middleware rejections carry the two-key envelope; the
    # handler body adds ``detail`` only because AgentNotFound carries one.
    assert set(too_large.json()) == ENVELOPE_KEYS
    assert set(saturated.json()) == ENVELOPE_KEYS
    assert set(handled.json()) == ENVELOPE_KEYS | {ENVELOPE_DETAIL_KEY}

    # Every body round-trips through the shared constructor unchanged.
    for body in (too_large.json(), saturated.json()):
        assert body == error_envelope(body["error"], body["message"])
    handled_body = handled.json()
    assert handled_body == error_envelope(
        handled_body["error"], handled_body["message"], handled_body[ENVELOPE_DETAIL_KEY]
    )


def test_bodies_are_built_by_the_shared_function_at_runtime(
    orchestrator: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patching the one constructor changes BOTH the 413 body and the handler body."""

    def marked_envelope(code: str, message: str, detail: str = "") -> dict[str, str]:
        return {**error_envelope(code, message, detail), ENVELOPE_MARKER_KEY: ENVELOPE_MARKER_VALUE}

    monkeypatch.setattr(api_errors, "error_envelope", marked_envelope)
    # Seam proof (ADR-0019 amendment): the 413 body is built through the
    # ``api_errors`` module object, so this one patch reaches middleware
    # rejections without also rebinding ``api_middleware.error_envelope``.

    with TestClient(_backpressure_app()) as client:
        too_large = client.post(_ECHO_ROUTE, content=b"x" * (BACKPRESSURE_MAX_BODY_BYTES + 1))
    with TestClient(create_app(orchestrator=orchestrator)) as client:
        handled = client.post(_GHOST_INVOKE_ROUTE, json=_MSG)

    assert too_large.json()[ENVELOPE_MARKER_KEY] == ENVELOPE_MARKER_VALUE
    assert handled.json()[ENVELOPE_MARKER_KEY] == ENVELOPE_MARKER_VALUE


# ── Facade re-export identity (ADR-0019) ──────────────────────────────────────


def test_app_facade_reexports_error_surface() -> None:
    assert app_module._ERROR_STATUS is api_errors._ERROR_STATUS
    assert app_module._error_status is api_errors._error_status
    assert app_module.error_envelope is api_errors.error_envelope


def test_app_facade_reexports_workflow_models() -> None:
    assert app_module.WorkflowRunRequest is api_models.WorkflowRunRequest
    assert app_module.WorkflowValidateRequest is api_models.WorkflowValidateRequest
    assert app_module.WorkflowValidateResponse is api_models.WorkflowValidateResponse
