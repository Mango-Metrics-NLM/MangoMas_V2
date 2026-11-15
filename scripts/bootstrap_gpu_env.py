"""Reproducible `.venv-gpu` bootstrap and Windows ROOT/CA PEM exporter (ENV-1).

Solves two coupled failure modes documented in ``docs/testing/rca-20261029.md``
and ``docs/testing/rca-20261106.md``:

1. **TLS inspection proxy (``ENV-1``, 2026-11-06):** a Windows kernel/user-mode
   TLS filter re-signs outbound HTTPS with a local root CA stored in the
   Windows ``ROOT`` / ``CA`` certificate stores. Bundled OpenSSL/certifi in
   Python, Node MCP clients (including Context7), and ``gcloud`` do not read
   the Windows store by default. :func:`export_windows_ca_bundle` writes a
   deduplicated PEM bundle to ``%LOCALAPPDATA%/mangomas/ca-bundle.pem`` and
   wires ``SSL_CERT_FILE``, ``REQUESTS_CA_BUNDLE``, ``PIP_CERT``, and
   ``--use-feature=truststore`` into every bootstrap ``pip`` call.
2. **CPU wheel replacement (``ENV-1``, 2026-10-29):** installing
   ``.[dev,embeddings-local,rag]`` pulls PyPI's CPU-only ``torch`` wheel unless
   the CUDA wheel (``torch==2.14.1+cu130``) is installed **last** from the
   PyTorch CUDA index, followed by ``pip check`` and a CUDA device-count
   assertion.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import ssl
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

import _logging
import _stdio

logger = logging.getLogger(__name__)

DEFAULT_VENV_DIR: Final[Path] = Path(".venv-gpu")
DEFAULT_EXTRAS_SPEC: Final[str] = ".[dev,embeddings-local,rag]"
DEFAULT_CONTRACTS_PATH: Final[str] = "./mango-integration-contracts"
DEFAULT_TORCH_SPEC: Final[str] = "torch==2.14.1+cu130"
DEFAULT_TORCH_INDEX_URL: Final[str] = "https://download.pytorch.org/whl/cu130"
DEFAULT_WINDOWS_STORES: Final[tuple[str, ...]] = ("ROOT", "CA")
DEFAULT_CA_RELATIVE_PATH: Final[Path] = Path("mangomas") / "ca-bundle.pem"
X509_ASN_ENCODING: Final[str] = "x509_asn"
MIN_CUDA_DEVICES: Final[int] = 1
EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1

_CUDA_VERIFY_CODE: Final[str] = (
    "import sys, torch; "
    "count = torch.cuda.device_count(); "
    "print(f'torch={torch.__version__} cuda_available={torch.cuda.is_available()} "
    "device_count={count}'); "
    f"sys.exit(0 if torch.cuda.is_available() and count >= {MIN_CUDA_DEVICES} else 1)"
)

CertEntry = tuple[bytes, str, object]
CertEnumerator = Callable[[str], Sequence[CertEntry]]
SubprocessRunner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


def default_ca_bundle_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the per-user PEM path outside the repository tree."""
    source = os.environ if env is None else env
    local_app_data = source.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".cache"
    return base / DEFAULT_CA_RELATIVE_PATH


def is_trusted_for_server_auth(trust: object) -> bool:
    """True when a Windows store entry is trusted for TLS server authentication.

    ``ssl.enum_certificates`` reports ``True`` for unrestricted trust, or a set
    of purpose OIDs. A PEM bundle cannot carry those restrictions, so an entry
    restricted to other purposes (code signing, e-mail) must be left out rather
    than silently promoted to a TLS trust anchor.
    """
    if trust is True:
        return True
    return isinstance(trust, (set, frozenset)) and ssl.Purpose.SERVER_AUTH.oid in trust


def export_windows_ca_bundle(
    output_path: Path,
    *,
    stores: Sequence[str] = DEFAULT_WINDOWS_STORES,
    cert_enumerator: CertEnumerator | None = None,
) -> int:
    """Export X.509 certificates from Windows stores to *output_path* as PEM.

    Returns the number of unique certificates written, or ``0`` on platforms
    without ``ssl.enum_certificates`` when no *cert_enumerator* is injected.
    """
    enumerator = (
        cert_enumerator if cert_enumerator is not None else getattr(ssl, "enum_certificates", None)
    )
    if enumerator is None:
        logger.debug("ssl.enum_certificates unavailable; skipping Windows CA export")
        return 0
    pem_blocks: list[str] = []
    skipped_untrusted = 0
    for store_name in stores:
        for cert_bytes, encoding, trust in enumerator(store_name):
            if encoding != X509_ASN_ENCODING:
                continue
            if not is_trusted_for_server_auth(trust):
                skipped_untrusted += 1
                continue
            pem_blocks.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
    if skipped_untrusted:
        logger.info(
            "Skipped %d certificates not trusted for TLS server authentication",
            skipped_untrusted,
            extra={"event": "gpu_bootstrap_ca_skipped", "count": skipped_untrusted},
        )
    unique_blocks = list(dict.fromkeys(pem_blocks))
    if not unique_blocks:
        # An empty PEM wired into SSL_CERT_FILE would make every TLS handshake fail.
        logger.warning(
            "No server-auth CA certificates found; leaving %s untouched",
            output_path,
            extra={"event": "gpu_bootstrap_ca_export_empty"},
        )
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(unique_blocks), encoding="utf-8")
    logger.info(
        "Exported %d unique CA certificates to %s",
        len(unique_blocks),
        output_path,
        extra={"event": "gpu_bootstrap_ca_exported", "count": len(unique_blocks)},
    )
    return len(unique_blocks)


def venv_python_path(venv_dir: Path, *, platform: str = sys.platform) -> Path:
    """Return the interpreter path inside *venv_dir* for *platform*."""
    if platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def build_subproc_env(
    ca_bundle: Path | None,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build subprocess environment with truststore and CA bundle variables."""
    env = dict(os.environ if base_env is None else base_env)
    env["PIP_USE_FEATURE"] = "truststore"
    env["PYTHONIOENCODING"] = "utf-8"
    if ca_bundle is not None and ca_bundle.is_file():
        bundle_str = str(ca_bundle.resolve())
        env["SSL_CERT_FILE"] = bundle_str
        env["REQUESTS_CA_BUNDLE"] = bundle_str
        env["PIP_CERT"] = bundle_str
    return env


def build_install_plan(
    venv_dir: Path = DEFAULT_VENV_DIR,
    *,
    host_python: str = sys.executable,
    extras_spec: str = DEFAULT_EXTRAS_SPEC,
    contracts_path: str = DEFAULT_CONTRACTS_PATH,
    torch_spec: str = DEFAULT_TORCH_SPEC,
    torch_index_url: str = DEFAULT_TORCH_INDEX_URL,
    platform: str = sys.platform,
) -> list[list[str]]:
    """Build the ordered command plan; CUDA ``torch`` is always installed after extras."""
    vpy = str(venv_python_path(venv_dir, platform=platform))
    return [
        [host_python, "-m", "venv", str(venv_dir)],
        [vpy, "-m", "pip", "install", "--upgrade", "pip", "--use-feature=truststore"],
        [vpy, "-m", "pip", "install", "--use-feature=truststore", "-e", extras_spec],
        [vpy, "-m", "pip", "install", "--use-feature=truststore", "-e", contracts_path],
        [
            vpy,
            "-m",
            "pip",
            "install",
            "--use-feature=truststore",
            torch_spec,
            "--index-url",
            torch_index_url,
        ],
        [vpy, "-m", "pip", "check"],
        [vpy, "-c", _CUDA_VERIFY_CODE],
    ]


def _default_runner(
    argv: Sequence[str],
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        env=dict(env),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_bootstrap(
    *,
    venv_dir: Path = DEFAULT_VENV_DIR,
    ca_bundle: Path | None = None,
    extras_spec: str = DEFAULT_EXTRAS_SPEC,
    contracts_path: str = DEFAULT_CONTRACTS_PATH,
    torch_spec: str = DEFAULT_TORCH_SPEC,
    torch_index_url: str = DEFAULT_TORCH_INDEX_URL,
    export_ca_only: bool = False,
    dry_run: bool = False,
    runner: SubprocessRunner = _default_runner,
    cert_enumerator: CertEnumerator | None = None,
) -> int:
    """Export the Windows CA bundle and execute (or print) the `.venv-gpu` plan."""
    resolved_ca = default_ca_bundle_path() if ca_bundle is None else ca_bundle
    plan = build_install_plan(
        venv_dir,
        extras_spec=extras_spec,
        contracts_path=contracts_path,
        torch_spec=torch_spec,
        torch_index_url=torch_index_url,
    )
    if dry_run:
        print(f"# CA bundle target: {resolved_ca}")
        for cmd in plan:
            print(shlex.join(cmd))
        return EXIT_OK

    exported = export_windows_ca_bundle(resolved_ca, cert_enumerator=cert_enumerator)
    if export_ca_only:
        if exported == 0:
            logger.error(
                "No server-auth CA certificates were exported to %s "
                "(ssl.enum_certificates unavailable or the stores are empty)",
                resolved_ca,
                extra={"event": "gpu_bootstrap_ca_export_empty"},
            )
            return EXIT_ERROR
        print(f"Exported {exported} CA certificates: {resolved_ca}")
        return EXIT_OK

    # Only a bundle written by this run is wired in; a stale file from an
    # earlier run may predate a rotated inspection-proxy root.
    env = build_subproc_env(resolved_ca if exported else None)
    for step_index, cmd in enumerate(plan, start=1):
        logger.info("Step %d/%d: %s", step_index, len(plan), shlex.join(cmd))
        completed = runner(cmd, env)
        if completed.returncode != 0:
            logger.error(
                "Bootstrap step %d failed (exit %d): %s\n%s\n%s",
                step_index,
                completed.returncode,
                shlex.join(cmd),
                completed.stdout.strip(),
                completed.stderr.strip(),
            )
            return EXIT_ERROR
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Windows ROOT/CA bundle and bootstrap the CUDA .venv-gpu environment.",
    )
    parser.add_argument("--venv-dir", type=Path, default=DEFAULT_VENV_DIR)
    parser.add_argument("--ca-bundle", type=Path, default=None)
    parser.add_argument("--extras-spec", default=DEFAULT_EXTRAS_SPEC)
    parser.add_argument("--contracts-path", default=DEFAULT_CONTRACTS_PATH)
    parser.add_argument("--torch-spec", default=DEFAULT_TORCH_SPEC)
    parser.add_argument("--torch-index-url", default=DEFAULT_TORCH_INDEX_URL)
    parser.add_argument("--export-ca-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``python scripts/bootstrap_gpu_env.py``."""
    _stdio.configure_utf8_stdio()
    _logging.configure_script_logging()
    args = _build_parser().parse_args(argv)
    return run_bootstrap(
        venv_dir=args.venv_dir,
        ca_bundle=args.ca_bundle,
        extras_spec=args.extras_spec,
        contracts_path=args.contracts_path,
        torch_spec=args.torch_spec,
        torch_index_url=args.torch_index_url,
        export_ca_only=args.export_ca_only,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
