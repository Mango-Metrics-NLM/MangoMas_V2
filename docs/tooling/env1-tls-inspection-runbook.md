# ENV-1 Runbook: Windows TLS Inspection Proxy & Reproducible `.venv-gpu` Bootstrap

This runbook documents the root cause and machine-wide remediation for the two
environment failure modes recorded as `ENV-1` in
[`docs/testing/rca-20261029.md`](../testing/rca-20261029.md) and
[`docs/testing/rca-20261106.md`](../testing/rca-20261106.md).

---

## 1. Symptoms

1. **Outbound HTTPS fails in Python, `pip`, `gcloud`, or Node MCP clients:**
   - `pip install` or `gcloud auth application-default print-access-token`
     fails with:
     `SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate')`.
   - Node-backed MCP servers in Cursor / Claude Code (such as `user-context7`)
     fail with `TypeError: fetch failed`.
2. **CUDA disappears after installing `[dev]` extras (`rca-20261029` ENV-1):**
   - Running `pip install -e ".[dev,embeddings-local,rag]"` on top of a CUDA
     `torch` replaces it with PyPI's CPU-only wheel (`torch.cuda.is_available() == False`),
     causing `make gpu` to fail.

---

## 2. Root Cause & Diagnosis

On Windows workstations running a local TLS inspection filter driver (for
example `nllMonFltProxy`, visible via `$env:SSLKEYLOGFILE` pointing at
`\\.\nllMonFltProxy\...`), outbound TLS connections are terminated and
re-signed by a local root/intermediate CA installed in the **Windows OS
certificate store** (`Cert:\LocalMachine\Root` / `CA`).

- Python's bundled OpenSSL (`certifi`), Node.js, and `gcloud` do **not** read
  the Windows certificate store by default.
- Furthermore, the `pip` bundled with Python 3.11 `venv` predates default
  OS-truststore usage and requires `--use-feature=truststore` (or `PIP_CERT`).

Verify the presence of the filter and Windows store certificates:

```powershell
# Inspect SSLKEYLOGFILE if set by the filter driver
$env:SSLKEYLOGFILE

# Count certificates in the Windows ROOT and CA stores via Python stdlib
.venv\Scripts\python.exe -c "import ssl; print('ROOT:', len(ssl.enum_certificates('ROOT')), 'CA:', len(ssl.enum_certificates('CA')))"
```

---

## 3. Automated Remediation (`scripts/bootstrap_gpu_env.py` / `make venv-gpu`)

`scripts/bootstrap_gpu_env.py` handles both `ENV-1` failure modes
deterministically:

1. Exports all X.509 certificates from the Windows `ROOT` and `CA` stores into
   a deduplicated PEM bundle at `%LOCALAPPDATA%\mangomas\ca-bundle.pem`
   (outside the repository tree so it survives re-clones and is never committed).
2. Creates `.venv-gpu`, upgrades `pip` with `--use-feature=truststore` and the
   exported CA bundle wired into `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and
   `PIP_CERT`.
3. Installs `-e ".[dev,embeddings-local,rag]"` and `-e ./mango-integration-contracts`.
4. Installs `torch==2.14.1+cu130` **last** from
   `https://download.pytorch.org/whl/cu130` so the `[dev]` resolver cannot
   replace the CUDA wheel with PyPI's CPU build.
5. Verifies `pip check` and `torch.cuda.device_count() >= 1`.

```powershell
# Preview the exact bootstrap plan without modifying disk
.venv\Scripts\python.exe scripts/bootstrap_gpu_env.py --dry-run

# Export/refresh only the Windows CA PEM bundle (< 1s)
.venv\Scripts\python.exe scripts/bootstrap_gpu_env.py --export-ca-only

# Full .venv-gpu build and verification (or via `make venv-gpu`)
.venv\Scripts\python.exe scripts/bootstrap_gpu_env.py
```

Run the GPU and local-embeddings suites against `.venv-gpu`:

```powershell
make gpu PYTHON=.venv-gpu/Scripts/python.exe
make embeddings-local PYTHON=.venv-gpu/Scripts/python.exe
```

---

## 4. Machine-Wide Environment Variables (Python, Node/Context7, `gcloud`)

To make interactive shells, Node MCP servers (including Context7), and `gcloud`
trust the exported Windows CA bundle across sessions, set the following
user-scope environment variables in PowerShell:

```powershell
$ca = "$env:LOCALAPPDATA\mangomas\ca-bundle.pem"
.venv\Scripts\python.exe scripts/bootstrap_gpu_env.py --export-ca-only --ca-bundle $ca

[Environment]::SetEnvironmentVariable("SSL_CERT_FILE", $ca, "User")
[Environment]::SetEnvironmentVariable("REQUESTS_CA_BUNDLE", $ca, "User")
[Environment]::SetEnvironmentVariable("PIP_CERT", $ca, "User")
[Environment]::SetEnvironmentVariable("PIP_USE_FEATURE", "truststore", "User")
[Environment]::SetEnvironmentVariable("NODE_EXTRA_CA_CERTS", $ca, "User")
[Environment]::SetEnvironmentVariable("CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE", $ca, "User")
```

- **Cursor / MCP restart note:** Cursor spawns MCP server subprocesses
  (`user-context7`, `fetch`, etc.) from its own process environment. After
  setting `NODE_EXTRA_CA_CERTS` at User scope, **restart Cursor** (or run
  `Developer: Reload Window` after restarting the host process) so Node MCP
  workers inherit `NODE_EXTRA_CA_CERTS`.
- **Refresh cadence:** re-run
  `python scripts/bootstrap_gpu_env.py --export-ca-only` whenever the local TLS
  inspection filter rotates its root certificate or after a Windows certificate
  store update.
- **Security note:** the exported PEM bundle contains machine-local root
  certificates. Keep it in `%LOCALAPPDATA%\mangomas\ca-bundle.pem` (outside the
  repository); `.certs/` is also listed in `.gitignore` and `.dockerignore` as
  defence in depth. Never commit a `.pem` bundle to git.
