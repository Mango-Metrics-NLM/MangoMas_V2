"""Unit tests for ``mangomas.harness.repair_sandbox`` (Ring 2 automated LLM coding)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mangomas.errors import ConfigError, ToolExecutionError
from mangomas.harness.repair_sandbox import (
    build_scrubbed_oracle_env,
    run_oracle_and_promote,
    stage_candidate_file,
    validate_loopback_llm_url,
    validate_promotion_target,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "https://LOCALHOST:8000/v1",
    ],
)
def test_validate_loopback_llm_url_accepts_loopback_hosts(url: str) -> None:
    assert validate_loopback_llm_url(url) == url


@pytest.mark.parametrize(
    ("bad_url", "expected_match"),
    [
        ("http://[::1", "could not be parsed"),
        ("ftp://localhost:1234", "http\\(s\\) URL"),
        ("http:///no-host", "explicit host"),
        ("https://api.openai.com/v1", "not a loopback address"),
        ("http://192.168.1.10:1234/v1", "not a loopback address"),
    ],
)
def test_validate_loopback_llm_url_rejects_non_loopback_or_malformed(
    bad_url: str,
    expected_match: str,
) -> None:
    with pytest.raises(ConfigError, match=expected_match):
        validate_loopback_llm_url(bad_url)


def test_stage_candidate_file_writes_inside_staging_root(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    written = stage_candidate_file(staging, "src/mangomas/agents/chat.py", "x = 1\n")
    assert written == (staging / "src" / "mangomas" / "agents" / "chat.py").resolve()
    assert written.read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.parametrize(
    ("bad_rel", "expected_match"),
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("\\\\server\\share\\file.py", "refusing absolute/drive/UNC"),
        ("//server/share/file.py", "refusing absolute/drive/UNC"),
        ("C:\\Windows\\System32\\cmd.exe", "refusing absolute/drive/UNC"),
        ("/etc/passwd", "refusing absolute/drive/UNC"),
        ("../outside.py", "without '\\.\\.' traversal"),
        ("sub/../../outside.py", "without '\\.\\.' traversal"),
        ("file.py:stream", "without '\\.\\.' traversal"),
        ("NUL", "reserved Windows device name"),
        ("con", "reserved Windows device name"),
        ("com1", "reserved Windows device name"),
        ("lpt1", "reserved Windows device name"),
        ("nul.txt", "reserved Windows device name"),
    ],
)
def test_stage_candidate_file_rejects_traversal_and_non_relative_paths(
    tmp_path: Path,
    bad_rel: str,
    expected_match: str,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(ToolExecutionError, match=expected_match):
        stage_candidate_file(staging, bad_rel, "evil\n")


def test_resolve_contained_path_rejects_symlink_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == staging)
    with pytest.raises(ToolExecutionError, match="root must not be a symlink"):
        stage_candidate_file(staging, "a.py", "x = 1\n")


def test_resolve_contained_path_rejects_symlink_in_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    linked_child = staging / "sub"
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked_child)
    with pytest.raises(ToolExecutionError, match="refused symlink or junction"):
        stage_candidate_file(staging, "sub/a.py", "x = 1\n")


def test_resolve_contained_path_rejects_escaped_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside.py"
    orig_resolve = Path.resolve

    def _fake_resolve(self: Path, strict: bool = False) -> Path:
        if self.name == "escape.py":
            return orig_resolve(outside, strict=strict)
        return orig_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    with pytest.raises(ToolExecutionError, match="escapes root"):
        stage_candidate_file(staging, "escape.py", "x = 1\n")


def test_validate_promotion_target_accepts_allowlisted_unprotected_file(
    tmp_path: Path,
) -> None:
    target = validate_promotion_target(
        tmp_path,
        "src/mangomas/agents/chat.py",
        allowed_paths={"src/mangomas/agents/chat.py"},
    )
    assert target == (tmp_path / "src" / "mangomas" / "agents" / "chat.py").resolve()


@pytest.mark.parametrize(
    ("rel_path", "allowlist", "expected_match"),
    [
        (
            "src/mangomas/agents/summarize.py",
            {"src/mangomas/agents/chat.py"},
            "not in the repair allowlist",
        ),
        (
            "src/mangomas/errors.py",
            {"src/mangomas/errors.py"},
            "protected or governance-surface",
        ),
        (
            "pyproject.toml",
            {"pyproject.toml"},
            "protected or governance-surface",
        ),
        (
            ".claude/settings.json",
            {".claude/settings.json"},
            "forbidden directory",
        ),
        (
            ".git/config",
            {".git/config"},
            "forbidden directory",
        ),
        (
            ".env.production",
            {".env.production"},
            "forbidden configuration/secret file",
        ),
        (
            "config/.env",
            {"config/.env"},
            "forbidden configuration/secret file",
        ),
        (
            ".ENV.production",
            {".ENV.production"},
            "forbidden configuration/secret file",
        ),
    ],
)
def test_validate_promotion_target_rejects_forbidden_targets(
    tmp_path: Path,
    rel_path: str,
    allowlist: set[str],
    expected_match: str,
) -> None:
    with pytest.raises(ToolExecutionError, match=expected_match):
        validate_promotion_target(tmp_path, rel_path, allowed_paths=allowlist)


def test_build_scrubbed_oracle_env_strips_live_gates_and_secrets() -> None:
    raw = {
        "PATH": "/usr/bin",
        "RUN_LMSTUDIO": "1",
        "RUN_GPU": "1",
        "MANGOMAS_LLM__API_KEY": "secret",
        "MANGOMAS_SECRETS__PROJECT_ID": "proj",
        "MANGOMAS_AUTH__SECRET_REF": "ref",
        "GITHUB_TOKEN": "ghp_xxx",
        "OPENAI_API_KEY": "sk-xxx",
        "SAFE_VAR": "keep",
    }
    scrubbed = build_scrubbed_oracle_env(raw)
    assert scrubbed["SAFE_VAR"] == "keep"
    assert scrubbed["PYTEST_ADDOPTS"] == "-p no:cacheprovider -o addopts="
    for dropped in (
        "RUN_LMSTUDIO",
        "RUN_GPU",
        "MANGOMAS_LLM__API_KEY",
        "MANGOMAS_SECRETS__PROJECT_ID",
        "MANGOMAS_AUTH__SECRET_REF",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
    ):
        assert dropped not in scrubbed


def test_build_scrubbed_oracle_env_strips_profile_and_db_credentials() -> None:
    raw = {
        "MANGOMAS_LLM_PROFILES__REVIEW__API_KEY": "sk-review",
        "MANGOMAS_LLM_PROFILES__DRAFT__SECRET_REF": "sm://draft",
        "MANGOMAS_DB__URL": "postgresql://u:p@host/db",
        "MANGOMAS_EVAL__SINK_OPTIONS": '{"webhook":{"url":"https://exfil.example"}}',
        "LANGFUSE_SECRET_KEY": "lf-xxx",
        "MANGOMAS_ENV": "local",
        "SAFE_VAR": "keep",
    }
    scrubbed = build_scrubbed_oracle_env(raw)
    assert scrubbed["SAFE_VAR"] == "keep"
    assert scrubbed["MANGOMAS_ENV"] == "local"
    for dropped in (
        "MANGOMAS_LLM_PROFILES__REVIEW__API_KEY",
        "MANGOMAS_LLM_PROFILES__DRAFT__SECRET_REF",
        "MANGOMAS_DB__URL",
        "MANGOMAS_EVAL__SINK_OPTIONS",
        "LANGFUSE_SECRET_KEY",
    ):
        assert dropped not in scrubbed


def test_build_scrubbed_oracle_env_is_case_insensitive_on_windows() -> None:
    raw = {
        "openai_api_key": "sk-lower",
        "OPENAI_API_KEY": "sk-upper",
        "github_token": "ghp-lower",
        "MANGOMAS_LLM_PROFILES__BROKEN__API_KEY": "sk-prof",
        "SAFE_VAR": "keep",
    }
    scrubbed = build_scrubbed_oracle_env(raw)
    assert scrubbed["SAFE_VAR"] == "keep"
    for dropped in (
        "openai_api_key",
        "OPENAI_API_KEY",
        "github_token",
        "MANGOMAS_LLM_PROFILES__BROKEN__API_KEY",
    ):
        assert dropped not in scrubbed


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), -1.0])
def test_run_oracle_and_promote_rejects_non_finite_timeout(
    tmp_path: Path,
    bad_timeout: float,
) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 1\n")
    with pytest.raises(ToolExecutionError, match="positive and finite"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[sys.executable, "-c", "pass"],
            timeout_seconds=bad_timeout,
        )


def test_run_oracle_and_promote_detects_staged_mutation(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    staged = stage_candidate_file(staging, rel, "VALUE = 1\n")
    oracle_cmd = (
        "from pathlib import Path; "
        f"Path(r'{staged}').write_text('VALUE = 999\\n')"
    )
    with pytest.raises(ToolExecutionError, match="modified the staged candidate file"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[sys.executable, "-c", oracle_cmd],
        )


def test_run_oracle_and_promote_promotes_only_when_oracle_exits_zero(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 42\n")

    fail_outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )
    assert fail_outcome.promoted is False
    assert fail_outcome.returncode == 1
    assert not (repo / rel).exists()

    pass_outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=[sys.executable, "-c", "print('oracle green')"],
    )
    assert pass_outcome.promoted is True
    assert pass_outcome.returncode == 0
    assert (repo / rel).read_text(encoding="utf-8") == "VALUE = 42\n"


def test_run_oracle_and_promote_rejects_invalid_argv_timeout_or_missing_staged(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"

    with pytest.raises(ToolExecutionError, match="non-empty sequence"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[],
        )

    with pytest.raises(ToolExecutionError, match="timeout_seconds must be positive"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[sys.executable, "-c", "pass"],
            timeout_seconds=0,
        )

    with pytest.raises(ToolExecutionError, match="does not exist"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[sys.executable, "-c", "pass"],
        )


def test_run_oracle_and_promote_translates_timeout_and_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 1\n")

    def _raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    with pytest.raises(ToolExecutionError, match="exceeded timeout"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=[sys.executable, "-c", "pass"],
            timeout_seconds=1.0,
        )

    def _raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("missing binary")

    monkeypatch.setattr(subprocess, "run", _raise_oserror)
    with pytest.raises(ToolExecutionError, match="failed to start"):
        run_oracle_and_promote(
            staging_root=staging,
            repo_root=repo,
            relative_path=rel,
            allowed_paths={rel},
            oracle_argv=["nonexistent-binary"],
        )
