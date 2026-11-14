"""Direct unit tests for shared script helpers.

``scripts/_governance.py`` and ``scripts/_stdin_json.py`` are each shared by
two consumers. ``scripts/_stdio.py`` is the Windows UTF-8 stdio seam. This
file tests those modules on their own terms so every branch has direct
coverage, not only the path one caller happens to hit.
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

import pytest

from tests._script_loader import load_script_module

governance = load_script_module("_governance.py")
stdin_json = load_script_module("_stdin_json.py")

_VALID_TOML = """
[tool.mangomas.governance]
protected_paths = ["src/mangomas/core/agent.py"]
breaking_change_marker_aliases = ["BREAKING-CHANGE"]
"""


# ── _governance.load_governance ────────────────────────────────────────────


def test_load_governance_reads_valid_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_VALID_TOML, encoding="utf-8")
    protected, aliases = governance.load_governance(pyproject)
    assert protected == frozenset({"src/mangomas/core/agent.py"})
    assert aliases == frozenset({"BREAKING-CHANGE"})


def test_load_governance_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(governance.GovernanceLoadError, match="cannot read"):
        governance.load_governance(tmp_path / "does-not-exist.toml")


def test_load_governance_raises_on_malformed_toml_syntax(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("not [ valid toml", encoding="utf-8")
    with pytest.raises(governance.GovernanceLoadError, match="malformed TOML"):
        governance.load_governance(pyproject)


def test_load_governance_raises_on_missing_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.mangomas]\n# no governance table\n", encoding="utf-8")
    with pytest.raises(governance.GovernanceLoadError, match="missing or malformed key"):
        governance.load_governance(pyproject)


def test_load_governance_raises_on_empty_protected_paths(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.mangomas.governance]\nprotected_paths = []\n"
        'breaking_change_marker_aliases = ["BREAKING-CHANGE"]\n',
        encoding="utf-8",
    )
    with pytest.raises(governance.GovernanceLoadError, match="non-empty"):
        governance.load_governance(pyproject)


# ── _governance.find_breaking_change_marker ────────────────────────────────

_ALIASES = frozenset({"BREAKING-CHANGE", "# approved-breaking-change"})


@pytest.mark.parametrize(
    ("text", "expect_match"),
    [
        ("BREAKING-CHANGE: widened protocol", True),
        ("BREAKING-CHANGE", True),
        ("# approved-breaking-change", True),
        ("+BREAKING-CHANGE: widened protocol", True),  # added diff line
        ("-BREAKING-CHANGE: widened protocol", False),  # deleted diff line
        ("This is NOT a BREAKING-CHANGE, just a cleanup.", False),
        ("just an ordinary message", False),
        ("BREAKING-CHANGEFOO: not the marker", False),
    ],
)
def test_find_breaking_change_marker(text: str, expect_match: bool) -> None:
    result = governance.find_breaking_change_marker(text, _ALIASES)
    assert (result is not None) is expect_match


# ── _stdin_json.read_json_payload ──────────────────────────────────────────


def test_read_json_payload_parses_a_valid_object() -> None:
    assert stdin_json.read_json_payload(StringIO('{"a": 1}')) == {"a": 1}


def test_read_json_payload_returns_empty_dict_on_empty_stream() -> None:
    assert stdin_json.read_json_payload(StringIO("")) == {}


def test_read_json_payload_returns_empty_dict_on_malformed_json_no_logger() -> None:
    assert stdin_json.read_json_payload(StringIO("not json")) == {}


def test_read_json_payload_returns_empty_dict_on_non_dict_json_no_logger() -> None:
    assert stdin_json.read_json_payload(StringIO("[1, 2, 3]")) == {}


def test_read_json_payload_logs_a_warning_on_malformed_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_scripts_shared_helpers.malformed")
    caplog.set_level(logging.WARNING, logger=logger.name)
    assert stdin_json.read_json_payload(StringIO("not json"), logger=logger) == {}
    assert any("malformed JSON" in r.message for r in caplog.records)


def test_read_json_payload_logs_a_warning_on_non_dict_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test_scripts_shared_helpers.non_dict")
    caplog.set_level(logging.WARNING, logger=logger.name)
    assert stdin_json.read_json_payload(StringIO("[1, 2, 3]"), logger=logger) == {}
    records = [r for r in caplog.records if "not an object" in r.message]
    assert len(records) == 1
    assert getattr(records[0], "payload_type", None) == "list"


stdio = load_script_module("_stdio.py")


class _RecordingStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def test_configure_utf8_stdio_is_a_no_op_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _RecordingStream()
    monkeypatch.setattr(stdio.sys, "platform", "linux")
    monkeypatch.setattr(stdio.sys, "stdout", stream)
    monkeypatch.setattr(stdio.sys, "stderr", stream)
    stdio.configure_utf8_stdio()
    assert stream.calls == []


def test_configure_utf8_stdio_skips_streams_that_cannot_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio.sys, "platform", "win32")
    monkeypatch.setattr(stdio.sys, "stdout", object())
    monkeypatch.setattr(stdio.sys, "stderr", object())
    stdio.configure_utf8_stdio()


def test_configure_utf8_stdio_reconfigures_both_streams_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _RecordingStream()
    stderr = _RecordingStream()
    monkeypatch.setattr(stdio.sys, "platform", "win32")
    monkeypatch.setattr(stdio.sys, "stdout", stdout)
    monkeypatch.setattr(stdio.sys, "stderr", stderr)
    stdio.configure_utf8_stdio()
    expected = {"encoding": "utf-8", "errors": "replace"}
    assert stdout.calls == [expected]
    assert stderr.calls == [expected]


script_logging = load_script_module("_logging.py")


def test_configure_script_logging_attaches_module_handler_when_unconfigured() -> None:
    mod_log = logging.getLogger("test_scripts_shared_helpers.mod_fresh")
    mod_log.handlers.clear()
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    root.handlers.clear()
    buf = StringIO()
    try:
        script_logging.configure_script_logging(module_logger=mod_log, stream=buf)
        assert len(mod_log.handlers) == 1
        assert mod_log.level == logging.INFO
        mod_log.info("hello")
        assert "INFO test_scripts_shared_helpers.mod_fresh: hello" in buf.getvalue()
    finally:
        mod_log.handlers.clear()
        root.handlers[:] = saved_root_handlers


def test_configure_script_logging_skips_handler_when_root_configured() -> None:
    mod_log = logging.getLogger("test_scripts_shared_helpers.mod_with_root")
    mod_log.handlers.clear()
    script_logging.configure_script_logging(level=logging.DEBUG, module_logger=mod_log)
    assert mod_log.handlers == []
    assert mod_log.level == logging.DEBUG


def test_configure_script_logging_only_if_unconfigured_and_basic_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.append(kw))
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = [logging.NullHandler()]
        script_logging.configure_script_logging(only_if_unconfigured=True)
        assert calls == []

        script_logging.configure_script_logging(level=logging.WARNING, force=True)
        assert calls == [
            {
                "level": logging.WARNING,
                "format": script_logging.DEFAULT_LOG_FORMAT,
                "force": True,
            }
        ]
    finally:
        root.handlers[:] = saved


def test_configure_harness_telemetry_logging_happy_path_and_fallbacks(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cfg:
        class harness:
            metrics_namespace = "mango.test"
            hook_log_level = "INFO"

        class log:
            format = "json"

        class telemetry:
            exporter = "console"

    recorded: list[dict[str, object]] = []
    log = logging.getLogger("test_scripts_shared_helpers.telemetry")
    caplog.set_level(logging.WARNING, logger=log.name)

    script_logging.configure_harness_telemetry_logging(
        log,
        get_settings_fn=_Cfg,
        configure_telemetry_fn=lambda **kw: recorded.append(kw),
    )
    assert recorded == [
        {
            "service_name": "mango.test",
            "log_level": "INFO",
            "log_format": "json",
            "exporter": "console",
        }
    ]

    recorded.clear()
    script_logging.configure_harness_telemetry_logging(
        log,
        cfg=_Cfg(),
        configure_telemetry_fn=lambda **kw: recorded.append(kw),
    )
    assert len(recorded) == 1

    basic_calls: list[dict[str, object]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: basic_calls.append(kw))
    script_logging.configure_harness_telemetry_logging(
        log,
        available=False,
        fallback_message="unavailable fallback",
        include_exc_info=False,
        extra_on_error=True,
    )
    script_logging.configure_harness_telemetry_logging(
        log,
        cfg=_Cfg(),
        configure_telemetry_fn=lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")),
        fallback_message="boom fallback",
        include_exc_info=False,
        extra_on_error=False,
    )
    assert len(basic_calls) == 2
    assert any(r.message == "unavailable fallback" for r in caplog.records)
    assert any(r.message == "boom fallback" for r in caplog.records)
    # Also exercise default-import resolution branches cleanly against live mangomas.
    monkeypatch.setattr("mangomas.telemetry.configure_telemetry", lambda **kw: recorded.append(kw))
    script_logging.configure_harness_telemetry_logging(log)
    assert len(recorded) == 2
