"""Tests for scripts/studio_consumer_probe.py and its OpenAI-compatible stub.

The mock run is the contract test itself: every journey against the real app,
with the stub injected through ``httpx.ASGITransport``. The remaining tests pin
the seams it depends on — config resolution, the network guard, the CORS env
restore, failure reporting and the exit codes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition import llm_registry
from tests._script_loader import load_script_module

stub = load_script_module("_openai_compat_stub.py")
probe = load_script_module("studio_consumer_probe.py")

_LIVE_BASE_URL = "http://probe-host.example:1234/v1"
_LIVE_MODEL = "live-draft"
_LIVE_REVIEW_MODEL = "live-review"


def _mock_config() -> Any:
    return probe.resolve_config(probe.parse_args(["--mode", probe.MODE_MOCK]))


def _live_args(*extra: str) -> list[str]:
    return ["--mode", probe.MODE_LIVE, "--base-url", _LIVE_BASE_URL, "--model", _LIVE_MODEL, *extra]


# ── The stub ─────────────────────────────────────────────────────────────────


def _stub_client() -> httpx.AsyncClient:
    app = stub.build_stub_app([probe.STUB_DRAFT_MODEL])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://stub")


def _chat(model: str, *, stream: bool = False) -> dict[str, Any]:
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hi there"}]}


async def test_stub_lists_its_roster() -> None:
    async with _stub_client() as client:
        resp = await client.get("/v1/models")
    assert [m["id"] for m in resp.json()["data"]] == [probe.STUB_DRAFT_MODEL]


async def test_stub_completion_names_the_model_that_served_it() -> None:
    async with _stub_client() as client:
        resp = await client.post("/v1/chat/completions", json=_chat(probe.STUB_DRAFT_MODEL))
    content = resp.json()["choices"][0]["message"]["content"]
    assert content == f"[{probe.STUB_DRAFT_MODEL}] hi there"


async def test_stub_stream_rebuilds_the_same_text_then_done() -> None:
    async with _stub_client() as client:
        resp = await client.post("/v1/chat/completions", json=_chat("m", stream=True))
    lines = [line for line in resp.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    tokens = [json.loads(line[6:])["choices"][0]["delta"]["content"] for line in lines[:-1]]
    assert len(tokens) > 1
    assert "".join(tokens) == stub.reply_for("m", _chat("m")["messages"])


async def test_stub_rejects_the_fail_model() -> None:
    async with _stub_client() as client:
        resp = await client.post("/v1/chat/completions", json=_chat(stub.FAIL_MODEL))
    assert resp.status_code == 400


@pytest.mark.parametrize("payload", ["one", ["one", "two"]], ids=["single", "batch"])
async def test_stub_embeddings_are_fixed_width_and_deterministic(payload: Any) -> None:
    async with _stub_client() as client:
        resp = await client.post("/v1/embeddings", json={"model": "e", "input": payload})
    vectors = [row["embedding"] for row in resp.json()["data"]]
    assert vectors[0] == stub.embed_text("one")
    assert all(len(v) == stub.EMBEDDING_DIMENSIONS for v in vectors)


def test_reply_without_a_user_turn_is_just_the_model_tag() -> None:
    assert stub.reply_for("m", [{"role": "system", "content": "x"}]) == "[m] "


# ── The probe, end to end ────────────────────────────────────────────────────


async def test_mock_run_passes_every_check_without_touching_the_network() -> None:
    checks = await probe.run_probe(_mock_config())
    failed = [c for c in checks if not c.ok]
    assert failed == [], probe.render(failed)
    journeys = {c.journey for c in checks}
    expected = {j.__name__.removeprefix("journey_") for j in probe.JOURNEYS} | {"network"}
    assert journeys == expected


async def test_a_crashed_journey_is_a_failed_check_not_a_crash() -> None:
    async def journey_explodes(_ctx: Any) -> None:
        raise RuntimeError("boom")

    checks = await probe.run_probe(_mock_config(), journeys=[journey_explodes])
    crashed = [c for c in checks if c.journey == "explodes"]
    assert [c.ok for c in crashed] == [False]
    assert "RuntimeError: boom" in crashed[0].detail


async def test_stub_clients_close_when_composition_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[httpx.AsyncClient]] = []
    real_seam = probe._provider_seam

    @contextmanager
    def _spy(cfg: Any) -> Iterator[list[httpx.AsyncClient]]:
        with real_seam(cfg) as clients:
            captured.append(clients)
            yield clients

    def _build_then_fail(settings: Any) -> Any:
        llm_registry.get("lmstudio")(settings.llm)
        raise RuntimeError("composition failed")

    monkeypatch.setattr(probe, "_provider_seam", _spy)
    monkeypatch.setattr(probe, "build_orchestrator", _build_then_fail)
    with pytest.raises(RuntimeError, match="composition failed"):
        await probe.run_probe(_mock_config())
    assert len(captured[0]) == 1
    assert captured[0][0].is_closed


async def test_stored_turn_binds_to_its_prompt_not_to_the_newest_row() -> None:
    found: dict[str, Any] = {}

    async def journey_lookup(ctx: Any) -> None:
        await ctx.client.post("/agents/chat/invoke", json=probe._request("first"))
        await ctx.client.post("/agents/chat/invoke", json=probe._request("second"))
        found["first"] = await probe._stored_turn(ctx.client, "first")
        found["absent"] = await probe._stored_turn(ctx.client, "never sent")

    await probe.run_probe(_mock_config(), journeys=[journey_lookup])
    assert found["first"]["request"]["messages"][-1]["content"] == "first"
    assert found["absent"] is None
    assert probe._turn_metadata(None) == {}


def test_main_mock_exits_zero_and_writes_the_report(tmp_path: Path) -> None:
    report = tmp_path / "nested" / "report.json"
    assert probe.main(["--mode", probe.MODE_MOCK, "--report", str(report)]) == probe.EXIT_OK
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["mode"] == probe.MODE_MOCK
    assert payload["checks"]
    assert all(c["ok"] for c in payload["checks"])


def test_main_exits_runtime_error_when_any_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _one_failure(_cfg: Any) -> list[Any]:
        return [probe.Check("j", "n", ok=False)]

    monkeypatch.setattr(probe, "run_probe", _one_failure)
    assert probe.main(["--mode", probe.MODE_MOCK]) == EXIT_RUNTIME_ERROR


def test_main_live_without_a_review_model_is_a_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert probe.main([*_live_args(), "--review-model", ""]) == EXIT_CONFIG_ERROR
    assert probe.LIVE_REVIEW_MODEL_ENV in capsys.readouterr().err


def test_render_tallies_passes_and_failures() -> None:
    text = probe.render(
        [probe.Check("a", "x", ok=True), probe.Check("b", "y", ok=False, detail="d")]
    )
    assert text.splitlines() == ["PASS  a: x  ", "FAIL  b: y  d", "1/2 checks passed"]


# ── Configuration ────────────────────────────────────────────────────────────


def test_live_config_routes_broken_to_an_unserved_api_version() -> None:
    cfg = probe.resolve_config(probe.parse_args(_live_args("--review-model", _LIVE_REVIEW_MODEL)))
    assert cfg.broken_base_url == "http://probe-host.example:1234/v999"
    assert cfg.broken_model == _LIVE_MODEL
    assert cfg.review_model == _LIVE_REVIEW_MODEL
    assert cfg.allowed_hosts == frozenset({"probe-host.example"})
    assert not cfg.echoes_model


def test_mock_config_allows_no_network_and_fails_through_the_stub() -> None:
    cfg = _mock_config()
    assert cfg.allowed_hosts == frozenset()
    assert cfg.broken_model == stub.FAIL_MODEL
    assert cfg.broken_base_url is None
    assert cfg.echoes_model


def test_live_defaults_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(probe.LIVE_BASE_URL_ENV, _LIVE_BASE_URL)
    monkeypatch.setenv(probe.LIVE_MODEL_ENV, _LIVE_MODEL)
    monkeypatch.setenv(probe.LIVE_REVIEW_MODEL_ENV, _LIVE_REVIEW_MODEL)
    monkeypatch.setenv(probe.LIVE_TIMEOUT_ENV, "12.5")
    args = probe.parse_args(["--mode", probe.MODE_LIVE])
    assert (args.base_url, args.model, args.review_model) == (
        _LIVE_BASE_URL,
        _LIVE_MODEL,
        _LIVE_REVIEW_MODEL,
    )
    assert args.timeout_seconds == 12.5


def test_settings_carry_three_profiles_over_one_provider() -> None:
    settings = probe.build_settings(_mock_config())
    assert sorted(settings.llm_profiles) == sorted(
        [probe.BROKEN_PROFILE, probe.DRAFT_PROFILE, probe.REVIEW_PROFILE]
    )
    assert {p.provider for p in settings.llm_profiles.values()} == {"lmstudio"}


# ── Seams ────────────────────────────────────────────────────────────────────


async def test_network_guard_records_real_transport_hosts_then_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_socket(_self: Any, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    def _no_socket_sync(_self: Any, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _no_socket)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_socket_sync)
    with probe.record_network_hosts() as hosts:
        await httpx.AsyncHTTPTransport().handle_async_request(
            httpx.Request("GET", "http://a.test/")
        )
        httpx.HTTPTransport().handle_request(httpx.Request("GET", "http://b.test/"))
    assert hosts == {"a.test", "b.test"}
    assert httpx.AsyncHTTPTransport.handle_async_request is _no_socket
    assert httpx.HTTPTransport.handle_request is _no_socket_sync


@pytest.mark.parametrize("previous", [None, '["http://kept.example"]'], ids=["unset", "set"])
def test_cors_env_is_restored(monkeypatch: pytest.MonkeyPatch, previous: str | None) -> None:
    if previous is None:
        monkeypatch.delenv(probe.CORS_ORIGINS_ENV, raising=False)
    else:
        monkeypatch.setenv(probe.CORS_ORIGINS_ENV, previous)
    with probe.cors_allow_origin(probe.STUDIO_ORIGIN):
        assert json.loads(os.environ[probe.CORS_ORIGINS_ENV]) == [probe.STUDIO_ORIGIN]
    assert os.environ.get(probe.CORS_ORIGINS_ENV) == previous


def test_live_provider_seam_injects_nothing() -> None:
    cfg = probe.resolve_config(probe.parse_args(_live_args("--review-model", _LIVE_REVIEW_MODEL)))
    with probe._provider_seam(cfg) as clients:
        assert clients == []
