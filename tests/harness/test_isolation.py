"""Unit and live-containment tests for ``mangomas.harness.isolation`` (spec-0044 / ADR-0040)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation import (
    DOCKER_CONTAINER_PYTHON,
    DOCKER_TMPFS_SPEC,
    DOCKER_WORK_DIR,
    DockerIsolationBackend,
    NoIsolationBackend,
    OracleIsolationBackend,
    resolve_sandbox_argv,
    sandbox_pythonpath,
    select_sandbox_env,
)
from mangomas.harness.repair_sandbox import run_oracle_and_promote, stage_candidate_file


def test_backends_satisfy_runtime_checkable_protocol() -> None:
    assert isinstance(NoIsolationBackend(), OracleIsolationBackend)
    assert isinstance(DockerIsolationBackend(), OracleIsolationBackend)


@pytest.mark.parametrize("token", ["{python}", "python"])
def test_resolve_sandbox_argv_substitutes_python_tokens(token: str) -> None:
    resolved = resolve_sandbox_argv(
        [token, "-m", "pytest", "-q"],
        interpreter="/usr/bin/python3",
        backend_name="docker",
    )
    assert resolved == ["/usr/bin/python3", "-m", "pytest", "-q"]


def test_resolve_sandbox_argv_keeps_bare_program_and_rejects_host_path() -> None:
    assert resolve_sandbox_argv(
        ["pytest", "-q"],
        interpreter="python",
        backend_name="docker",
    ) == ["pytest", "-q"]
    assert resolve_sandbox_argv(
        [sys.executable, "-c", "pass"],
        interpreter=sys.executable,
        backend_name="none",
        allow_host_interpreter=True,
    ) == [sys.executable, "-c", "pass"]
    with pytest.raises(ToolExecutionError, match="host-specific path"):
        resolve_sandbox_argv(
            ["C:\\Python311\\python.exe", "-m", "pytest"],
            interpreter="python",
            backend_name="docker",
        )


def test_select_sandbox_env_keeps_only_allowlisted_keys_and_sets_dontwritebytecode() -> None:
    raw = {
        "PATH": "/usr/local/bin:/usr/bin",
        "HOME": "/home/operator",
        "USERPROFILE": "C:\\Users\\operator",
        "PYTEST_ADDOPTS": "-p no:cacheprovider -o addopts=",
        "PYTHONIOENCODING": "utf-8",
        "MANGOMAS_ENV": "local",
    }
    selected = select_sandbox_env(raw)
    assert selected == {
        "MANGOMAS_ENV": "local",
        "PYTEST_ADDOPTS": "-p no:cacheprovider -o addopts=",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_no_isolation_backend_logs_warning_and_runs_subprocess(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = NoIsolationBackend()
    assert backend.is_available() is True
    with caplog.at_level(logging.WARNING):
        res = backend.run(
            ["{python}", "-c", "print('unisolated-ok')"],
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            timeout_seconds=10.0,
        )
    assert res.returncode == 0
    assert res.backend == "none"
    assert "unisolated-ok" in res.stdout
    assert any(getattr(rec, "event", None) == "repair_oracle_unisolated" for rec in caplog.records)


def test_docker_isolation_is_available_probes_daemon_and_image(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def _ok_runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    backend = DockerIsolationBackend(image="mangomas:oracle", runner=_ok_runner)
    assert backend.is_available() is True
    assert seen == [
        ["docker", "info"],
        ["docker", "image", "inspect", "mangomas:oracle"],
    ]

    def _missing_image(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        code = 1 if "inspect" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "", "no such image")

    assert DockerIsolationBackend(runner=_missing_image).is_available() is False

    def _oserror_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("docker binary not found")

    assert DockerIsolationBackend(runner=_oserror_runner).is_available() is False
    assert tmp_path.is_dir()


def test_docker_isolation_run_shapes_hardened_argv(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "container-green", "")

    backend = DockerIsolationBackend(
        image="mangomas:oracle",
        memory_limit="256m",
        pids_limit=64,
        runner=_runner,
        name_factory=lambda: "mangomas-oracle-fixed123456",
    )
    res = backend.run(
        ["{python}", "-m", "pytest", "-q"],
        cwd=tmp_path,
        env={
            "PATH": "/host/path",
            "PYTEST_ADDOPTS": "-p no:cacheprovider -o addopts=",
            "PYTHONIOENCODING": "utf-8",
            "MANGOMAS_ENV": "local",
        },
        timeout_seconds=15.0,
    )
    assert res.returncode == 0
    assert res.backend == "docker"
    assert len(captured) == 1
    argv = captured[0]
    assert argv[:4] == ["docker", "run", "--rm", "--name"]
    assert argv[4] == "mangomas-oracle-fixed123456"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == DOCKER_TMPFS_SPEC
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert (
        "--security-opt" in argv and argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    )
    assert "--pids-limit" in argv and argv[argv.index("--pids-limit") + 1] == "64"
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "256m"
    expected_mount = f"type=bind,src={tmp_path.resolve()},dst={DOCKER_WORK_DIR},readonly"
    assert "--mount" in argv and argv[argv.index("--mount") + 1] == expected_mount
    assert "-w" in argv and argv[argv.index("-w") + 1] == DOCKER_WORK_DIR
    assert not any(token.startswith("PATH=") for token in argv)
    assert "PYTHONDONTWRITEBYTECODE=1" in argv
    assert f"PYTHONPATH={sandbox_pythonpath(DOCKER_WORK_DIR)}" in argv
    assert argv[-4:] == ["mangomas:oracle", DOCKER_CONTAINER_PYTHON, "-m", "pytest"] or argv[
        -5:
    ] == [
        "mangomas:oracle",
        DOCKER_CONTAINER_PYTHON,
        "-m",
        "pytest",
        "-q",
    ]


def test_docker_isolation_mounts_staging_read_only(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def _runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    backend = DockerIsolationBackend(runner=_runner)
    backend.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=5.0)
    mount_val = captured[0][captured[0].index("--mount") + 1]
    assert mount_val == f"type=bind,src={tmp_path.resolve()},dst={DOCKER_WORK_DIR},readonly"


def test_docker_isolation_kills_container_on_timeout_and_translates_oserror(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[list[str]] = []

    def _timeout_runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=2.0)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    backend = DockerIsolationBackend(runner=_timeout_runner)
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(ToolExecutionError, match="exceeded timeout"),
    ):
        backend.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=2.0)

    assert [call[:2] for call in calls] == [
        ["docker", "run"],
        ["docker", "kill"],
        ["docker", "ps"],
    ]
    assert any(
        getattr(rec, "event", None) == "repair_oracle_timeout_killed" for rec in caplog.records
    )

    def _oserror_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("daemon socket error")

    backend_err = DockerIsolationBackend(runner=_oserror_runner)
    with pytest.raises(ToolExecutionError, match="failed to start"):
        backend_err.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=2.0)


@pytest.mark.parametrize(
    ("ps_returncode", "ps_stdout"),
    [
        (0, "abc123\n"),  # daemon says the container is still running
        (1, ""),  # daemon cannot say: termination is unconfirmed, not assumed
    ],
)
def test_docker_isolation_reports_unconfirmed_kill_without_claiming_success(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    ps_returncode: int,
    ps_stdout: str,
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def _runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(cmd, ps_returncode, ps_stdout, "")
        return subprocess.CompletedProcess(cmd, 1, "", "kill failed")

    backend = DockerIsolationBackend(
        runner=_runner,
        name_factory=lambda: "mangomas-oracle-stuck",
        kill_verify_attempts=3,
        kill_verify_interval_seconds=0.25,
        sleep=sleeps.append,
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(ToolExecutionError, match="may still be running"),
    ):
        backend.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=1.0)

    assert sum(1 for call in calls if call[:2] == ["docker", "kill"]) == 3
    assert sleeps == [0.25, 0.25]
    events = {getattr(rec, "event", None) for rec in caplog.records}
    assert "repair_oracle_timeout_kill_failed" in events
    assert "repair_oracle_timeout_killed" not in events


def test_docker_isolation_treats_unrunnable_cli_as_unconfirmed_kill(tmp_path: Path) -> None:
    def _runner(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)
        raise OSError("docker CLI vanished")

    backend = DockerIsolationBackend(runner=_runner, kill_verify_attempts=1, sleep=lambda _s: None)
    with pytest.raises(ToolExecutionError, match="may still be running"):
        backend.run(["{python}", "-c", "pass"], cwd=tmp_path, env={}, timeout_seconds=1.0)


def test_sandbox_pythonpath_lists_staged_src_then_staging_root() -> None:
    assert sandbox_pythonpath("/work") == "/work/src:/work"


def test_run_oracle_and_promote_delegates_to_injected_isolation_backend(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 7\n")

    backend = DockerIsolationBackend(
        runner=lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, "isolated-pass", ""),
    )
    outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=["{python}", "-m", "pytest", "-q"],
        isolation=backend,
    )
    assert outcome.promoted is True
    assert (repo / rel).read_text(encoding="utf-8") == "VALUE = 7\n"


_LIVE_HANG_SECONDS = 60
_LIVE_TIMEOUT_SECONDS = 3.0


@pytest.mark.docker_isolation
def test_live_docker_isolation_kills_a_timed_out_container(tmp_path: Path) -> None:
    """A hung oracle must be confirmed gone after the timeout, not merely reported killed."""
    container_name = "mangomas-oracle-live-timeout-probe"
    backend = DockerIsolationBackend(name_factory=lambda: container_name)
    assert backend.is_available() is True, "Docker daemon or mangomas:oracle image is not ready"
    with pytest.raises(ToolExecutionError, match="exceeded timeout") as exc_info:
        backend.run(
            ["{python}", "-c", f"import time; time.sleep({_LIVE_HANG_SECONDS})"],
            cwd=tmp_path,
            env={},
            timeout_seconds=_LIVE_TIMEOUT_SECONDS,
        )
    assert "may still be running" not in str(exc_info.value)
    docker_bin = shutil.which("docker")
    assert docker_bin is not None
    listed = subprocess.run(  # noqa: S603 -- fixed argv, independent of the backend under test
        [docker_bin, "ps", "-aq", "--filter", f"name=^/{container_name}$"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.stdout.strip() == "", "timed-out oracle container survived the kill"


@pytest.mark.docker_isolation
def test_live_docker_isolation_enforces_filesystem_and_network_containment(
    tmp_path: Path,
) -> None:
    """Live proof against Docker + ``mangomas:oracle``: no host read, no write, no network."""
    backend = DockerIsolationBackend()
    assert backend.is_available() is True, "Docker daemon or mangomas:oracle image is not ready"

    staging = tmp_path / "staging"
    repo = tmp_path / "repo"
    staging.mkdir()
    repo.mkdir()
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("TOP_SECRET_CREDENTIAL\n", encoding="utf-8")

    rel = "src/mangomas/agents/chat.py"
    stage_candidate_file(staging, rel, "VALUE = 101\n")
    stage_candidate_file(staging, "src/oracle_probe_module.py", "MARKER = 'staged'\n")

    probe_code = (
        "import pathlib, pytest, socket, sys\n"
        "# 0. A staged src/ module imports from the sandbox, never a host path.\n"
        "import oracle_probe_module\n"
        "assert oracle_probe_module.MARKER == 'staged'\n"
        "assert oracle_probe_module.__file__.startswith('/work/'), oracle_probe_module.__file__\n"
        "# 1. Outside-staging host file must not exist inside the container.\n"
        "assert not pathlib.Path('../host_secret.txt').exists()\n"
        f"assert not pathlib.Path({host_secret.as_posix()!r}).exists()\n"
        "# 2. Staging mount (/work) must be read-only.\n"
        "try:\n"
        "    pathlib.Path('/work/tamper.py').write_text('evil')\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('staging mount was writable inside container')\n"
        "# 3. Outbound network must be disabled (--network none).\n"
        "try:\n"
        "    s = socket.create_connection(('1.1.1.1', 53), timeout=1.0)\n"
        "    s.close()\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('network connection succeeded inside --network none container')\n"
        "# 4. Staged candidate is readable and pytest is importable.\n"
        "assert 'VALUE = 101' in pathlib.Path('src/mangomas/agents/chat.py').read_text()\n"
        "print('live-docker-isolation-ok', pytest.__version__)\n"
    )
    outcome = run_oracle_and_promote(
        staging_root=staging,
        repo_root=repo,
        relative_path=rel,
        allowed_paths={rel},
        oracle_argv=["{python}", "-c", probe_code],
        isolation=backend,
    )
    assert outcome.promoted is True, outcome.stdout + outcome.stderr
    assert "live-docker-isolation-ok" in outcome.stdout
    assert (repo / rel).read_text(encoding="utf-8") == "VALUE = 101\n"
