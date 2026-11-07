"""Unit and contract tests for ``scripts/bootstrap_gpu_env.py`` (ENV-1)."""

from __future__ import annotations

import ssl
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tests._script_loader import load_script_module

bootstrap = load_script_module("bootstrap_gpu_env.py")


def test_default_ca_bundle_path_uses_localappdata_when_present(tmp_path: Path) -> None:
    target = bootstrap.default_ca_bundle_path({"LOCALAPPDATA": str(tmp_path)})
    assert target == tmp_path / "mangomas" / "ca-bundle.pem"


def test_default_ca_bundle_path_falls_back_to_home_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = bootstrap.default_ca_bundle_path({"LOCALAPPDATA": "   "})
    assert target == tmp_path / ".cache" / "mangomas" / "ca-bundle.pem"


def test_export_windows_ca_bundle_deduplicates_x509_and_skips_other_encodings(
    tmp_path: Path,
) -> None:
    der_a = b"0\x03\x02\x01\x01"
    der_b = b"0\x03\x02\x01\x02"

    def _fake_enum(store_name: str) -> list[tuple[bytes, str, object]]:
        if store_name == "ROOT":
            return [
                (der_a, "x509_asn", True),
                (der_a, "x509_asn", True),
                (b"ignored", "pkcs_7_asn", True),
            ]
        return [(der_b, "x509_asn", True)]

    out = tmp_path / "certs" / "ca-bundle.pem"
    written = bootstrap.export_windows_ca_bundle(out, cert_enumerator=_fake_enum)
    assert written == 2
    content = out.read_text(encoding="utf-8")
    assert ssl.DER_cert_to_PEM_cert(der_a) in content
    assert ssl.DER_cert_to_PEM_cert(der_b) in content
    assert content.count("-----BEGIN CERTIFICATE-----") == 2


_SERVER_AUTH_OID = ssl.Purpose.SERVER_AUTH.oid
_CODE_SIGNING_OID = "1.3.6.1.5.5.7.3.3"


@pytest.mark.parametrize(
    ("trust", "expected"),
    [
        (True, True),
        ({_SERVER_AUTH_OID}, True),
        ({_SERVER_AUTH_OID, _CODE_SIGNING_OID}, True),
        (frozenset({_SERVER_AUTH_OID}), True),
        ({_CODE_SIGNING_OID}, False),
        (set(), False),
        (False, False),
        (None, False),
    ],
)
def test_is_trusted_for_server_auth(trust: object, expected: bool) -> None:
    assert bootstrap.is_trusted_for_server_auth(trust) is expected


def test_export_windows_ca_bundle_excludes_certs_not_trusted_for_server_auth(
    tmp_path: Path,
) -> None:
    """PEM cannot carry Windows purpose restrictions, so restricted roots must stay out."""
    server_auth = b"0\x03\x02\x01\x01"
    unrestricted = b"0\x03\x02\x01\x02"
    code_signing_only = b"0\x03\x02\x01\x03"
    empty_trust = b"0\x03\x02\x01\x04"

    def _fake_enum(_store: str) -> list[tuple[bytes, str, object]]:
        return [
            (server_auth, "x509_asn", {_SERVER_AUTH_OID}),
            (unrestricted, "x509_asn", True),
            (code_signing_only, "x509_asn", {_CODE_SIGNING_OID}),
            (empty_trust, "x509_asn", set()),
        ]

    out = tmp_path / "ca-bundle.pem"
    written = bootstrap.export_windows_ca_bundle(out, stores=("ROOT",), cert_enumerator=_fake_enum)
    content = out.read_text(encoding="utf-8")
    assert written == 2
    assert ssl.DER_cert_to_PEM_cert(server_auth) in content
    assert ssl.DER_cert_to_PEM_cert(unrestricted) in content
    assert ssl.DER_cert_to_PEM_cert(code_signing_only) not in content
    assert ssl.DER_cert_to_PEM_cert(empty_trust) not in content


def test_export_windows_ca_bundle_writes_nothing_when_no_cert_qualifies(tmp_path: Path) -> None:
    out = tmp_path / "ca-bundle.pem"
    written = bootstrap.export_windows_ca_bundle(
        out,
        cert_enumerator=lambda _s: [(b"0\x03\x02\x01\x01", "x509_asn", {_CODE_SIGNING_OID})],
    )
    assert written == 0
    assert not out.exists()


def test_export_ca_only_fails_when_nothing_is_exported(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ca_path = tmp_path / "ca.pem"
    rc = bootstrap.run_bootstrap(
        ca_bundle=ca_path,
        export_ca_only=True,
        cert_enumerator=lambda _s: [],
    )
    assert rc == bootstrap.EXIT_ERROR
    assert not ca_path.exists()
    assert "Exported" not in capsys.readouterr().out


def test_run_bootstrap_does_not_wire_a_stale_bundle_when_export_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT"):
        monkeypatch.delenv(name, raising=False)
    stale = tmp_path / "ca.pem"
    stale.write_text("-----BEGIN CERTIFICATE-----\nstale\n", encoding="utf-8")
    seen_envs: list[dict[str, str]] = []

    def _runner(argv: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        seen_envs.append(dict(env))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    rc = bootstrap.run_bootstrap(
        venv_dir=tmp_path / ".venv-gpu",
        ca_bundle=stale,
        runner=_runner,
        cert_enumerator=lambda _s: [],
    )
    assert rc == bootstrap.EXIT_OK
    assert all("SSL_CERT_FILE" not in env for env in seen_envs)


def test_export_windows_ca_bundle_noops_when_enum_certificates_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delattr(bootstrap.ssl, "enum_certificates", raising=False)
    out = tmp_path / "ca-bundle.pem"
    assert bootstrap.export_windows_ca_bundle(out) == 0
    assert not out.exists()


@pytest.mark.parametrize(
    ("platform", "expected_suffix"),
    [
        ("win32", Path("Scripts") / "python.exe"),
        ("linux", Path("bin") / "python"),
    ],
)
def test_venv_python_path_matches_platform(platform: str, expected_suffix: Path) -> None:
    venv_dir = Path(".venv-gpu")
    assert bootstrap.venv_python_path(venv_dir, platform=platform) == venv_dir / expected_suffix


def test_build_subproc_env_wires_ca_bundle_and_truststore(tmp_path: Path) -> None:
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    env = bootstrap.build_subproc_env(bundle, base_env={"PATH": "/usr/bin"})
    assert env["PIP_USE_FEATURE"] == "truststore"
    assert env["SSL_CERT_FILE"] == str(bundle.resolve())
    assert env["REQUESTS_CA_BUNDLE"] == str(bundle.resolve())
    assert env["PIP_CERT"] == str(bundle.resolve())


def test_build_subproc_env_skips_cert_vars_when_bundle_absent(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pem"
    env = bootstrap.build_subproc_env(missing, base_env={})
    assert env["PIP_USE_FEATURE"] == "truststore"
    assert "SSL_CERT_FILE" not in env
    assert "PIP_CERT" not in env


def test_install_plan_installs_cuda_torch_after_extras_and_before_verification() -> None:
    """Guard for ENV-1 (rca-20261029): `[dev]` replaces CUDA torch unless torch runs last."""
    plan = bootstrap.build_install_plan(Path(".venv-gpu"), platform="win32")
    joined = [" ".join(cmd) for cmd in plan]
    extras_idx = next(i for i, cmd in enumerate(joined) if bootstrap.DEFAULT_EXTRAS_SPEC in cmd)
    contracts_idx = next(
        i for i, cmd in enumerate(joined) if bootstrap.DEFAULT_CONTRACTS_PATH in cmd
    )
    torch_idx = next(i for i, cmd in enumerate(joined) if bootstrap.DEFAULT_TORCH_INDEX_URL in cmd)
    pip_check_idx = next(i for i, cmd in enumerate(joined) if "pip check" in cmd)
    cuda_verify_idx = next(i for i, cmd in enumerate(joined) if "torch.cuda.device_count()" in cmd)

    assert 0 < extras_idx < contracts_idx < torch_idx < pip_check_idx < cuda_verify_idx
    for step in plan[1:5]:
        assert "--use-feature=truststore" in step


def test_main_dry_run_prints_plan_without_invoking_runner(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ca_path = tmp_path / "ca.pem"
    rc = bootstrap.main(
        ["--dry-run", "--venv-dir", str(tmp_path / "v"), "--ca-bundle", str(ca_path)]
    )
    assert rc == bootstrap.EXIT_OK
    out = capsys.readouterr().out
    assert str(ca_path) in out
    assert bootstrap.DEFAULT_TORCH_INDEX_URL in out
    assert not ca_path.exists()


def test_run_bootstrap_export_ca_only_writes_bundle_and_skips_commands(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def _runner(argv: Sequence[str], _env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    ca_path = tmp_path / "ca.pem"
    rc = bootstrap.run_bootstrap(
        ca_bundle=ca_path,
        export_ca_only=True,
        runner=_runner,
        cert_enumerator=lambda _s: [(b"0\x03\x02\x01\x01", "x509_asn", True)],
    )
    assert rc == bootstrap.EXIT_OK
    assert ca_path.is_file()
    assert calls == []
    assert str(ca_path) in capsys.readouterr().out


def test_run_bootstrap_executes_every_step_and_stops_on_first_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    seen_envs: list[dict[str, str]] = []

    def _ok_runner(argv: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        seen_envs.append(dict(env))
        return subprocess.CompletedProcess(list(argv), 0, "ok", "")

    ca_path = tmp_path / "ca.pem"
    rc = bootstrap.run_bootstrap(
        venv_dir=tmp_path / ".venv-gpu",
        ca_bundle=ca_path,
        runner=_ok_runner,
        cert_enumerator=lambda _s: [(b"0\x03\x02\x01\x01", "x509_asn", True)],
    )
    assert rc == bootstrap.EXIT_OK
    assert len(calls) == 7
    assert seen_envs[0]["SSL_CERT_FILE"] == str(ca_path.resolve())

    fail_calls: list[list[str]] = []

    def _fail_runner(
        argv: Sequence[str],
        _env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        fail_calls.append(list(argv))
        code = 1 if len(fail_calls) == 2 else 0
        return subprocess.CompletedProcess(list(argv), code, "out", "err")

    rc_fail = bootstrap.run_bootstrap(
        venv_dir=tmp_path / ".venv-gpu",
        ca_bundle=ca_path,
        runner=_fail_runner,
        cert_enumerator=lambda _s: [],
    )
    assert rc_fail == bootstrap.EXIT_ERROR
    assert len(fail_calls) == 2


def test_default_runner_executes_harmless_python_command() -> None:
    completed = bootstrap._default_runner(
        [bootstrap.sys.executable, "-c", "print('runner-ok')"],
        bootstrap.os.environ,
    )
    assert completed.returncode == 0
    assert "runner-ok" in completed.stdout
