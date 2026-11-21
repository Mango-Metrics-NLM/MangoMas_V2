---
name: mango-supply-chain
description: >
  Supply-chain, secret-scanning, lockfile, SBOM, and ignore-parity workflow
  for Mango-Mas V2. Use when: running two-pass gitleaks and non-vacuity
  selftests, auditing pip dependencies against requirements.lock, checking
  .gitignore / .dockerignore / .gitleaks.toml parity for secondary venvs or
  helper scripts, or reviewing .gitleaksignore fingerprints.
argument-hint: "Describe the supply-chain check or paste a failing gitleaks, trivy, or Docker build-context check"
---

# Mango-Mas Supply-Chain Hygiene Skill

## When to Use

- Auditing secret-scanning configuration (`.gitleaks.toml`) and `.gitleaksignore` fingerprints
- Regenerating or verifying `requirements.lock` constraints and Trivy SBOM scans (`make sbom-scan`)
- Updating `.gitignore`, `.dockerignore`, or `.gitleaks.toml` when adding helper modules (`scripts/_*.py`) or secondary virtualenvs (`.venv-*/`)

---

## Procedure

1. **Two-pass secret scan + non-vacuity proof.**
   - Always run both `dir` (working tree) and `git` (history) passes with `-c .gitleaks.toml` (`make secret-scan`); neither pass subsumes the other.
   - Prove the ruleset still catches planted leaks (`make gitleaks-selftest` and offline Python model checks in `tests/deploy/test_gitleaks_config.py`).
   - `.gitleaksignore` entries must remain single-finding `<commit>:<file>:<rule>:<line>` fingerprints — never bare filenames or rule IDs.
2. **Allowlist & ignore parity.**
   - Whenever adding local scanner binaries, caches, secondary virtualenvs (`.venv-*/`, `venv/`), `uv.lock`, `.uv/`, or extracted `scripts/_*.py` dev helpers, update `.gitignore`, `.dockerignore`, and (for local scan artifacts only) `.gitleaks.toml` + `tests/deploy/test_gitleaks_config.py` / `tests/deploy/test_docker_build_context.py` together.
   - Never exempt `tests/` or `src/` from `.gitleaks.toml`.
3. **Runtime lockfile & vulnerability audit.**
   - Confirm `Dockerfile` constrains runtime wheel installation via `-c /tmp/requirements.lock` and every pin in `requirements.lock` uses exact `==`.
   - Use `make pip-audit` (scans both installed environment and `-r requirements.lock`) and `make sbom-scan` on clean or Linux release flows; compile Linux pins via `make lock` (never unpinned `make lock-upgrade` without explicit dependency review).

---

## Verification

```powershell
python -m pytest tests/deploy/test_gitleaks_config.py tests/deploy/test_docker_build_context.py tests/deploy/test_ci_make_parity.py -q --no-cov
make secret-scan
make gitleaks-selftest
make sbom-scan
make pip-audit
make docker-smoke
```

Offline CI parity subset (no network/Docker needed): run `tests/deploy/test_gitleaks_config.py` + `tests/deploy/test_docker_build_context.py` and `make gate`.
