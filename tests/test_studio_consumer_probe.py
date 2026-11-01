"""Tests for the Studio consumer probe (spec-0041) and its OpenAI-compatible stub.

The mock run is the contract test itself: every journey against the real app,
with the stub injected through ``httpx.ASGITransport``. The remaining tests pin
the seams it depends on: config resolution and its failure modes, skip-not-pass
for unobservable checks, the network guard, the CORS env restore, client
cleanup, failure reporting, logging and the exit codes.

Sibling helpers are reached through the probe module (``probe.journeys``,
``probe.Check``) so a test sees the same objects the probe uses.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from mangomas.composition import llm_registry
from tests._script_loader import load_script_module
from tests.constants import (
    STUDIO_PROBE_LIVE_BASE_URL,
    STUDIO_PROBE_LIVE_HOST,
    STUDIO_PROBE_LIVE_MODEL,
    STUDIO_PROBE_LIVE_REVIEW_MODEL,
    STUDIO_PROBE_LIVE_UNSERVED_URL,
)

stub = load_script_module("_openai_compat_stub.py")
probe = load_script_module("studio_consumer_probe.py")
journeys = probe.journeys

_STUB_ORIGIN = "http://stub"


def _mock_config() -> Any:
    return probe.resolve_config(probe.parse_args(["--mode", probe.MODE_MOCK]))


def _live_args(*extra: str) -> list[str]:
    return [
        "--mode",
        probe.MODE_LIVE,
        "--base-url",
        STUDIO_PROBE_LIVE_BASE_URL,
        "--model",
        STUDIO_PROBE_LIVE_MODEL,
        *extra,
    ]


def _live_config() -> Any:
    args = _live_args("--review-model", STUDIO_PROBE_LIVE_REVIEW_MODEL)
    return probe.resolve_config(probe.parse_args(args))


def _offline_context(cfg: Any) -> Any:
    """A journey context for helpers that never touch the client or orchestrator."""
    return probe.JourneyContext(
        client=cast(Any, None), orch=cast(Any, None), cfg=cfg, rec=probe.Recorder()
    )


# ── The stub ─────────────────────────────────────────────────────────────────


def _stub_client() -> httpx.AsyncClient:
    app = stub.build_stub_app([probe.STUB_DRAFT_MODEL])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_STUB_ORIGIN)


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


@pytest.mark.parametrize("route", ["/v1/chat/completions", "/v1/embeddings"])
async def test_stub_rejects_a_body_that_is_not_an_object(route: str) -> None:
    async with _stub_client() as client:
        resp = await client.post(route, json=["not", "an", "object"])
    assert resp.status_code == 400
    assert "JSON object" in resp.json()["error"]["message"]


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
    assert [c for c in checks if c.status != probe.CHECK_OK] == [], probe.render(checks)
    names = {j.__name__.removeprefix("journey_") for j in probe.JOURNEYS}
    assert {c.journey for c in checks} == names | {"network"}


@pytest.mark.parametrize(
    "journey", probe.JOURNEYS, ids=lambda j: j.__name__.removeprefix("journey_")
)
async def test_each_journey_passes_alone_in_mock_mode(journey: Any) -> None:
    """One node per journey, so a guard-probe gate names the journey that must catch it."""
    checks = await probe.run_probe(_mock_config(), (journey,))
    name = journey.__name__.removeprefix("journey_")
    assert {c.journey for c in checks} == {name, "network"}
    assert [c for c in checks if c.status != probe.CHECK_OK] == [], probe.render(checks)


async def test_a_crashed_journey_is_a_failed_check_and_a_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def journey_explodes(_ctx: Any) -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.DEBUG, logger="studio_consumer_probe"):
        checks = await probe.run_probe(_mock_config(), [journey_explodes])
    crashed = [c for c in checks if c.journey == "explodes"]
    assert [c.status for c in crashed] == ["fail"]
    assert "RuntimeError: boom" in crashed[0].detail
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert warning.exc_info is not None
    assert any("explodes finished" in r.getMessage() for r in caplog.records)


async def test_stub_clients_close_when_composition_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[httpx.AsyncClient]] = []
    real_seam = probe.provider_seam

    @contextmanager
    def _spy(cfg: Any) -> Iterator[list[httpx.AsyncClient]]:
        with real_seam(cfg) as clients:
            captured.append(clients)
            yield clients

    def _build_then_fail(settings: Any) -> Any:
        llm_registry.get("lmstudio")(settings.llm)
        raise RuntimeError("composition failed")

    monkeypatch.setattr(probe, "provider_seam", _spy)
    monkeypatch.setattr(probe, "build_orchestrator", _build_then_fail)
    with pytest.raises(RuntimeError, match="composition failed"):
        await probe.run_probe(_mock_config())
    assert len(captured[0]) == 1
    assert captured[0][0].is_closed


async def test_stored_turn_binds_to_its_prompt_not_to_the_newest_row() -> None:
    found: dict[str, Any] = {}

    async def journey_lookup(ctx: Any) -> None:
        await ctx.client.post("/agents/chat/invoke", json=journeys.build_request("first"))
        await ctx.client.post("/agents/chat/invoke", json=journeys.build_request("second"))
        found["first"] = await journeys.stored_turn(ctx.client, "first")
        found["absent"] = await journeys.stored_turn(ctx.client, "never sent")

    await probe.run_probe(_mock_config(), [journey_lookup])
    assert found["first"]["request"]["messages"][-1]["content"] == "first"
    assert found["absent"] is None
    assert journeys.turn_metadata(None) == {}


# ── Skip, never a vacuous pass ───────────────────────────────────────────────


def test_routing_is_skipped_where_no_reply_names_its_model() -> None:
    ctx = _offline_context(_live_config())
    journeys.expect_routed(ctx, "served", "[anything] text", STUDIO_PROBE_LIVE_REVIEW_MODEL)
    assert [(c.status, c.detail) for c in ctx.rec.checks] == [
        ("skip", journeys.LIVE_ROUTING_SKIP_REASON)
    ]
    assert not ctx.rec.checks[0].failed


@pytest.mark.parametrize(("prefix", "status"), [("stub-review", "pass"), ("stub-draft", "fail")])
def test_routing_is_judged_where_the_reply_names_its_model(prefix: str, status: str) -> None:
    ctx = _offline_context(_mock_config())
    journeys.expect_routed(ctx, "served", f"[{prefix}] text", probe.STUB_REVIEW_MODEL)
    assert [c.status for c in ctx.rec.checks] == [status]


# ── CLI, exit codes and reporting ────────────────────────────────────────────


def test_main_mock_exits_zero_and_writes_the_report(tmp_path: Path) -> None:
    report = tmp_path / "nested" / "report.json"
    assert probe.main(["--mode", probe.MODE_MOCK, "--report", str(report)]) == probe.EXIT_OK
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["mode"] == probe.MODE_MOCK
    assert payload["checks"]
    assert {c["status"] for c in payload["checks"]} == {"pass"}


def test_main_exits_runtime_error_when_any_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _one_failure(_cfg: Any) -> list[Any]:
        return [probe.Check("j", "n", "fail")]

    monkeypatch.setattr(probe, "run_probe", _one_failure)
    assert probe.main(["--mode", probe.MODE_MOCK]) == EXIT_RUNTIME_ERROR


def test_main_treats_a_skip_as_not_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _one_skip(_cfg: Any) -> list[Any]:
        return [probe.Check("j", "n", "skip", "not observable")]

    monkeypatch.setattr(probe, "run_probe", _one_skip)
    assert probe.main(["--mode", probe.MODE_MOCK]) == probe.EXIT_OK


def test_an_unwritable_report_is_a_runtime_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _no_checks(_cfg: Any) -> list[Any]:
        return []

    monkeypatch.setattr(probe, "run_probe", _no_checks)
    # An existing directory cannot be opened for writing as a file.
    assert probe.main(["--mode", probe.MODE_MOCK, "--report", str(tmp_path)]) == EXIT_RUNTIME_ERROR
    assert "cannot write report" in capsys.readouterr().err


def test_main_live_without_a_review_model_is_a_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert probe.main([*_live_args(), "--review-model", ""]) == EXIT_CONFIG_ERROR
    assert probe.LIVE_REVIEW_MODEL_ENV in capsys.readouterr().err


def test_render_tallies_passes_failures_and_skips() -> None:
    text = probe.render(
        [
            probe.Check("a", "x", "pass"),
            probe.Check("b", "y", "fail", "d"),
            probe.Check("c", "z", "skip", "r"),
        ]
    )
    assert text.splitlines() == [
        "PASS  a: x  ",
        "FAIL  b: y  d",
        "SKIP  c: z  r",
        "1/3 checks passed, 1 failed, 1 skipped",
    ]


# ── Configuration ────────────────────────────────────────────────────────────


def test_live_config_routes_broken_to_an_unserved_api_version() -> None:
    cfg = _live_config()
    assert cfg.broken_base_url == STUDIO_PROBE_LIVE_UNSERVED_URL
    assert cfg.broken_model == STUDIO_PROBE_LIVE_MODEL
    assert cfg.review_model == STUDIO_PROBE_LIVE_REVIEW_MODEL
    assert cfg.allowed_hosts == frozenset({STUDIO_PROBE_LIVE_HOST})
    assert not cfg.echoes_model


@pytest.mark.parametrize(
    "base_url",
    ["localhost:1234/v1", "ftp://host.example/v1", "http:///v1"],
    ids=["no-scheme", "wrong-scheme", "no-host"],
)
def test_live_base_url_without_an_http_host_is_a_config_error(base_url: str) -> None:
    args = probe.parse_args(
        ["--mode", probe.MODE_LIVE, "--base-url", base_url, "--review-model", "r"]
    )
    with pytest.raises(probe.ProbeConfigError, match="http"):
        probe.resolve_config(args)


def test_mock_config_allows_no_network_and_fails_through_the_stub() -> None:
    cfg = _mock_config()
    assert cfg.allowed_hosts == frozenset()
    assert cfg.broken_model == stub.FAIL_MODEL
    assert cfg.broken_base_url is None
    assert cfg.echoes_model


def test_live_defaults_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(probe.LIVE_BASE_URL_ENV, STUDIO_PROBE_LIVE_BASE_URL)
    monkeypatch.setenv(probe.LIVE_MODEL_ENV, STUDIO_PROBE_LIVE_MODEL)
    monkeypatch.setenv(probe.LIVE_REVIEW_MODEL_ENV, STUDIO_PROBE_LIVE_REVIEW_MODEL)
    monkeypatch.setenv(probe.LIVE_TIMEOUT_ENV, "12.5")
    args = probe.parse_args(["--mode", probe.MODE_LIVE])
    assert (args.base_url, args.model, args.review_model) == (
        STUDIO_PROBE_LIVE_BASE_URL,
        STUDIO_PROBE_LIVE_MODEL,
        STUDIO_PROBE_LIVE_REVIEW_MODEL,
    )
    assert args.timeout_seconds == 12.5


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "inf", "nan"])
def test_a_malformed_timeout_env_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(probe.LIVE_TIMEOUT_ENV, raw)
    with pytest.raises(SystemExit) as exc:
        probe.parse_args([])
    assert exc.value.code == EXIT_CONFIG_ERROR


def test_settings_carry_three_profiles_over_one_provider() -> None:
    settings = probe.build_settings(_mock_config())
    assert sorted(settings.llm_profiles) == sorted(
        [journeys.BROKEN_PROFILE, journeys.DRAFT_PROFILE, journeys.REVIEW_PROFILE]
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
    with probe.provider_seam(_live_config()) as clients:
        assert clients == []
