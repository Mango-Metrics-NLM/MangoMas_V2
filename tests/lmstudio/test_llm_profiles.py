"""LM Studio E2E — scenario 13: named LLM provider profiles live (spec-0040 / spec-0041).

Studio v1's gate G2 asks for a two-profile run against one LM Studio server.
Three profiles share the live server:

- ``draft`` — the base model, so it runs whenever the suite does;
- ``review`` — the second loaded model (``LMSTUDIO_OVERRIDE_MODEL``); the
  scenarios that need it skip at runtime with the sanctioned
  ``set LMSTUDIO_OVERRIDE_MODEL to run ...`` reason;
- ``broken`` — the base model behind an API path LM Studio does not serve.

``broken`` is the routing oracle. ``response.metadata["llm_profile"]`` is
stamped by the composition mixin whichever client answered, so a green stamp
cannot prove the profile client made the call. LM Studio also serves an
unknown model id from whatever is loaded, so a bad *model* id proves nothing
either. A bad *path* that only the profile carries does: the 502 can come from
nowhere but the profile's own client, while the same request without the
profile succeeds against ``ctx.llm``.

Skipped unless ``RUN_LMSTUDIO=1``.
"""

from __future__ import annotations

import logging
import os

import httpx
import pytest
from pydantic import ValidationError

from mangomas.api.app import create_app
from mangomas.composition import LLM_PROFILE_METADATA_KEY, build_orchestrator
from mangomas.config import LLMProfileSettings, LLMSettings, Settings
from mangomas.core import AgentRequest, Message, Orchestrator
from tests.constants import (
    ASGI_TEST_BASE_URL,
    DEFAULT_VERTEX_TEST_MODEL,
    HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
    LIVE_BROKEN_PROFILE,
    LIVE_CLOUD_PROFILE,
    LIVE_DRAFT_PROFILE,
    LIVE_REVIEW_PROFILE,
    LLM_PROFILES_ROUTE,
    LMSTUDIO_OVERRIDE_MODEL_ENV,
    UNKNOWN_LLM_PROFILE_NAME,
    UNSERVED_API_VERSION_SUFFIX,
)
from tests.lmstudio.conftest import make_lmstudio_settings, parse_sse_data

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.lmstudio

_PROMPT = "Say hello in one short sentence."
_LOCAL_PROVIDER = "lmstudio"


def _body(profile: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"messages": [{"role": "user", "content": _PROMPT}]}
    if profile is not None:
        body["metadata"] = {LLM_PROFILE_METADATA_KEY: profile}
    return body


def _profiles(base_url: str, model: str, review_model: str | None) -> dict[str, LLMProfileSettings]:
    unserved = f"{base_url.rsplit('/v', 1)[0]}{UNSERVED_API_VERSION_SUFFIX}"
    profiles = {
        LIVE_DRAFT_PROFILE: LLMProfileSettings(provider=_LOCAL_PROVIDER, model=model),
        LIVE_BROKEN_PROFILE: LLMProfileSettings(
            provider=_LOCAL_PROVIDER, model=model, base_url=unserved
        ),
    }
    if review_model:
        profiles[LIVE_REVIEW_PROFILE] = LLMProfileSettings(
            provider=_LOCAL_PROVIDER, model=review_model
        )
    return profiles


@pytest.fixture
def review_model() -> str:
    """The second loaded model id, or a sanctioned runtime skip."""
    model = os.environ.get(LMSTUDIO_OVERRIDE_MODEL_ENV, "")
    if not model:
        pytest.skip(f"set {LMSTUDIO_OVERRIDE_MODEL_ENV} to run LM Studio profile tests")
    return model


def _build(base_url: str, model: str, review: str | None = None) -> Orchestrator:
    settings = make_lmstudio_settings(base_url, model, profiles=_profiles(base_url, model, review))
    return build_orchestrator(settings)


def _client(orch: Orchestrator) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(orchestrator=orch))
    return httpx.AsyncClient(transport=transport, base_url=ASGI_TEST_BASE_URL)


async def test_profile_picker_lists_the_roster_without_secrets(
    lmstudio_base_url: str, lmstudio_model: str
) -> None:
    """R6 against real clients: names, providers and models — nothing else."""
    orch = _build(lmstudio_base_url, lmstudio_model)
    try:
        async with _client(orch) as client:
            response = await client.get(LLM_PROFILES_ROUTE)
    finally:
        await orch.aclose()

    assert response.status_code == 200, response.text
    names = [entry["name"] for entry in response.json()["profiles"]]
    assert names == sorted([LIVE_BROKEN_PROFILE, LIVE_DRAFT_PROFILE])
    for forbidden in ("api_key", "secret_ref", "base_url", UNSERVED_API_VERSION_SUFFIX):
        assert forbidden not in response.text


async def test_the_broken_profile_fails_while_the_default_client_answers(
    lmstudio_base_url: str, lmstudio_model: str, lmstudio_client_timeout: float
) -> None:
    """The routing oracle: only the profile's own client knows the bad path."""
    orch = _build(lmstudio_base_url, lmstudio_model)
    try:
        async with _client(orch) as client:
            broken = await client.post(
                "/agents/chat/invoke",
                json=_body(LIVE_BROKEN_PROFILE),
                timeout=HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
            )
            default = await client.post(
                "/agents/chat/invoke", json=_body(), timeout=lmstudio_client_timeout
            )
    finally:
        await orch.aclose()

    assert broken.status_code == 502, broken.text
    assert broken.json()["error"] == "llm_bad_response"
    assert default.status_code == 200, default.text
    assert default.json()["content"]
    assert LLM_PROFILE_METADATA_KEY not in default.json()["metadata"]


async def test_review_profile_answers_and_the_stored_turn_carries_the_stamp(
    lmstudio_base_url: str,
    lmstudio_model: str,
    review_model: str,
    lmstudio_client_timeout: float,
) -> None:
    """A Studio timeline reads ``/history``: the stored turn must match the response."""
    orch = _build(lmstudio_base_url, lmstudio_model, review_model)
    try:
        async with _client(orch) as client:
            invoked = await client.post(
                "/agents/chat/invoke",
                json=_body(LIVE_REVIEW_PROFILE),
                timeout=lmstudio_client_timeout,
            )
            history = await client.get("/history", params={"limit": 1})
    finally:
        await orch.aclose()

    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["content"]
    assert invoked.json()["metadata"][LLM_PROFILE_METADATA_KEY] == LIVE_REVIEW_PROFILE
    stored = history.json()["turns"][0]["response"]["metadata"]
    assert stored[LLM_PROFILE_METADATA_KEY] == LIVE_REVIEW_PROFILE
    logger.info(
        "Review profile answered a live request",
        extra={"base_model": lmstudio_model, "review_model": review_model},
    )


async def test_stream_metadata_frame_names_the_profile(
    lmstudio_base_url: str,
    lmstudio_model: str,
    review_model: str,
    lmstudio_client_timeout: float,
) -> None:
    """Streams have no response object; the SSE ``metadata`` frame carries the name."""
    orch = _build(lmstudio_base_url, lmstudio_model, review_model)
    frames: list[dict[str, object]] = []
    try:
        async with (
            _client(orch) as client,
            client.stream(
                "POST",
                "/agents/chat/stream",
                json=_body(LIVE_REVIEW_PROFILE),
                timeout=lmstudio_client_timeout,
            ) as response,
        ):
            assert response.status_code == 200
            async for line in response.aiter_lines():
                frame = parse_sse_data(line)
                if frame is not None:
                    frames.append(frame)
    finally:
        await orch.aclose()

    metadata = [f for f in frames if f.get("event") == "metadata"]
    assert len(metadata) == 1
    data = metadata[0]["data"]
    assert isinstance(data, dict)
    assert data[LLM_PROFILE_METADATA_KEY] == LIVE_REVIEW_PROFILE
    assert any(f.get("event") == "token" for f in frames)


async def test_unknown_profile_is_a_json_400_before_any_stream_header(
    lmstudio_base_url: str, lmstudio_model: str
) -> None:
    orch = _build(lmstudio_base_url, lmstudio_model)
    try:
        async with (
            _client(orch) as client,
            client.stream(
                "POST",
                "/agents/chat/stream",
                json=_body(UNKNOWN_LLM_PROFILE_NAME),
                timeout=HTTPX_ERROR_PATH_TIMEOUT_SECONDS,
            ) as response,
        ):
            await response.aread()
    finally:
        await orch.aclose()

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"] == "config_error"


async def test_a_pipeline_carries_the_profile_across_stages(
    lmstudio_base_url: str, lmstudio_model: str
) -> None:
    """``_pipeline_pass`` seeds stage 2 from stage 1's stamped metadata."""
    orch = _build(lmstudio_base_url, lmstudio_model)
    request = AgentRequest(
        messages=[Message(role="user", content=_PROMPT)],
        metadata={LLM_PROFILE_METADATA_KEY: LIVE_DRAFT_PROFILE},
    )
    try:
        response = await orch.dispatch_pipeline(["chat", "summarize"], request)
    finally:
        await orch.aclose()

    assert response.content
    assert response.metadata[LLM_PROFILE_METADATA_KEY] == LIVE_DRAFT_PROFILE


def test_local_only_allowlist_refuses_a_cloud_profile_at_parse() -> None:
    """G2's third leg: ``PROVIDER_ALLOWLIST=["lmstudio"]`` never builds a cloud client."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            llm=LLMSettings(provider=_LOCAL_PROVIDER, provider_allowlist=[_LOCAL_PROVIDER]),
            llm_profiles={
                LIVE_CLOUD_PROFILE: LLMProfileSettings(
                    provider="vertex", model=DEFAULT_VERTEX_TEST_MODEL
                )
            },
        )
    assert LIVE_CLOUD_PROFILE in str(excinfo.value)
