# Mango-Mas V2 — developer task runner.
#
# Every target wraps the exact command CI runs, so `make gate` locally is the
# same gate as `.github/workflows/ci.yml`. Paths and env-var names live in
# variables below rather than being repeated per-target — override any of them
# on the command line, e.g. `make test PYTEST_FLAGS=-x`.

PYTHON      ?= python
# Lint/type-check surface — matches ci.yml's `lint` job exactly.
CODE_PATHS  ?= src tests scripts eval_harness_bridge/src mango-integration-contracts/src
BRIDGE_SRC  ?= eval_harness_bridge/src
BRIDGE_TESTS ?= tests/eval_harness_bridge
BRIDGE_FLOOR ?= 100
CONTRACTS_SRC ?= mango-integration-contracts/src
CONTRACTS_TESTS ?= tests/mango_contracts
CONTRACTS_FLOOR ?= 100
PYTEST_FLAGS ?= -q
# Base ref for the protected-path governance gate (ADR-0021). This repo's
# working trunk is `feat/initial-release`, not `main` — see CLAUDE.md.
# Override for a one-off check against a different base: `make protected-paths
# BASE_REF=origin/main`.
BASE_REF    ?= origin/feat/initial-release
SCRIPTS_SRC  ?= scripts
SCRIPTS_TESTS ?= tests/test_lint_agent_frontmatter.py tests/test_harness_session_start.py \
                 tests/test_run_workflow_e2e.py tests/deploy/test_ci_make_parity.py \
                 tests/test_check_protected_paths.py tests/test_harness_config_audit.py \
                 tests/test_scripts_shared_helpers.py tests/test_check_coverage.py \
                 tests/harness
# Measured baseline (2026-08-22, spec-0023 R5): 94% total. check_coverage.py
# was the gate's own blind spot — 24%, imported only for its FLOORS/
# GLOBAL_FLOOR constants, with `_check`/`main` exercised by nothing. A defect
# there (an inverted returncode test, a missing sys.exit) would pass the whole
# per-package gate while measuring nothing, and no coverage number could
# reveal it. tests/test_check_coverage.py now drives both against a stubbed
# subprocess and the file sits at 100%, so the floor ratchets 84 -> 92.
# 2026-09-10: harness_config_audit.py is 100% in-process and the remaining
# session-start gap is the two module-level ImportError arms (subprocess-
# only). Measured 96%; floor 94 (same two-point margin as 94 vs 92).
SCRIPTS_FLOOR ?= 94
# Pinned once, here — ci.yml's secret-scan job no longer repeats this literal
# inline; it just calls `make secret-scan` like every other job calls its own
# target below. The `dir`/`git` subcommands the recipe relies on exist from
# v8.19.0, so a `?=` override below that silently breaks the recipe.
GITLEAKS_VERSION ?= 8.21.2
# SHA256 of gitleaks_$(GITLEAKS_VERSION)_linux_x64.tar.gz, pinned from the
# release's own checksums.txt. Update both together when bumping the version.
GITLEAKS_SHA256 ?= 5bc41815076e6ed6ef8fbecc9d9b75bcae31f39029ceb55da08086315316e3ba
# Explicit ruleset. Both passes must pass `-c`: gitleaks does NOT auto-discover
# a config at the repo root when a scan path is given, so omitting the flag
# silently reverts to the built-in rules and drops the connection-string rule.
GITLEAKS_CONFIG ?= .gitleaks.toml
# pip-audit is a scanner, not a project dependency, so — mirroring the
# gitleaks pattern above — its version is pinned here rather than in
# pyproject. Update deliberately; the pin is the review record.
PIP_AUDIT_VERSION ?= 2.10.1
# The constraints file the runtime image installs through (Dockerfile's `-c`).
# Named once so `pip-audit` and any future lockfile target cannot disagree about
# which file is authoritative; tests/deploy/test_lockfile_freshness.py and
# tests/deploy/test_ci_make_parity.py both read this contract.
RUNTIME_LOCKFILE ?= requirements.lock
# SHA256 of trivy_$(TRIVY_VERSION)_Linux-64bit.tar.gz, pinned from the
# release's own checksums.txt. Update both together when bumping the version.
TRIVY_VERSION ?= 0.74.0
TRIVY_SHA256 ?= 2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a

.DEFAULT_GOAL := help
.PHONY: help install validate-config lint format format-check typecheck lint-imports frontmatter \
        protected-paths test test-xml \
        coverage bridge-coverage contracts-coverage scripts-coverage gate precommit serve clean gitleaks-selftest \
        integration lmstudio vertex postgres rag gcp-secrets gcp-trace langfuse \
        gated-suites embeddings-local secret-scan pip-audit sbom-scan

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install mangomas[dev] and sibling mango-integration-contracts
	$(PYTHON) -m pip install -e ".[dev]"
	$(PYTHON) -m pip install -e ./mango-integration-contracts

# ── Quality gate (mirrors .github/workflows/ci.yml) ──────────────────────────

validate-config: ## Validate .mcp.json / .claude/settings*.json JSON syntax
	$(PYTHON) -m json.tool .mcp.json > /dev/null
	$(PYTHON) -m json.tool .claude/settings.json > /dev/null
	$(PYTHON) -m json.tool .claude/settings.local.json.example > /dev/null

lint: ## ruff check
	$(PYTHON) -m ruff check $(CODE_PATHS)

format: ## ruff format (writes)
	$(PYTHON) -m ruff format $(CODE_PATHS)

format-check: ## ruff format --check
	$(PYTHON) -m ruff format --check $(CODE_PATHS)

typecheck: ## mypy --strict
	$(PYTHON) -m mypy --strict $(CODE_PATHS)

lint-imports: ## import-linter contracts (core ↛ outer; workflow/eval/rag/cognitive independence)
	# Invoked through $(PYTHON) so `make PYTHON=python3 lint-imports` cannot
	# pick a different interpreter's copy of the exact-pinned extra.
	$(PYTHON) -c "from importlinter.cli import lint_imports; raise SystemExit(lint_imports(no_logo=True))"

frontmatter: ## Validate .claude/agents + .claude/skills frontmatter
	$(PYTHON) scripts/lint_agent_frontmatter.py

protected-paths: ## Fail if a protected core contract changed without a BREAKING-CHANGE commit (ADR-0021)
	$(PYTHON) scripts/check_protected_paths.py --base-ref $(BASE_REF)

test: ## Full unit suite (addopts supply --cov and --cov-fail-under)
	$(PYTHON) -m pytest $(PYTEST_FLAGS)

test-xml: ## Full unit suite + coverage.xml (for codecov)
	$(PYTHON) -m pytest --cov-report=xml $(PYTEST_FLAGS)

coverage: ## Per-package coverage floors — the authoritative gate
	$(PYTHON) scripts/check_coverage.py

bridge-coverage: ## eval_harness_bridge isolated coverage gate
	# COVERAGE_FILE isolates this run to its own data file so it never
	# overwrites the default ``.coverage`` that ``make test``/``make coverage``
	# produced — running ``make gate`` (test → coverage → bridge-coverage) and
	# then a standalone ``make coverage`` afterward must still see the main
	# suite's data, not the bridge's.
	COVERAGE_FILE=.coverage.bridge $(PYTHON) -m coverage run --source=$(BRIDGE_SRC) -m pytest \
	  $(BRIDGE_TESTS) -o addopts="" $(PYTEST_FLAGS)
	COVERAGE_FILE=.coverage.bridge $(PYTHON) -m coverage report --show-missing --fail-under=$(BRIDGE_FLOOR)

contracts-coverage: ## mango-integration-contracts isolated coverage gate
	# Same isolation idiom as bridge-coverage: its own COVERAGE_FILE so it never
	# clobbers the main suite's `.coverage`, and -o addopts="" sheds the
	# inherited --cov=mangomas so this run measures only mango_contracts.
	COVERAGE_FILE=.coverage.contracts $(PYTHON) -m coverage run --source=$(CONTRACTS_SRC) -m pytest \
	  $(CONTRACTS_TESTS) -o addopts="" $(PYTEST_FLAGS)
	COVERAGE_FILE=.coverage.contracts $(PYTHON) -m coverage report --show-missing --fail-under=$(CONTRACTS_FLOOR)

scripts-coverage: ## scripts/ isolated coverage gate (measured floor — see SCRIPTS_FLOOR above)
	# Same isolation idiom as bridge-coverage: its own COVERAGE_FILE so it never
	# clobbers the main suite's `.coverage`, and -o addopts="" sheds the
	# inherited --cov=mangomas so this run measures only scripts/.
	COVERAGE_FILE=.coverage.scripts $(PYTHON) -m coverage run --source=$(SCRIPTS_SRC) -m pytest \
	  $(SCRIPTS_TESTS) -o addopts="" $(PYTEST_FLAGS)
	COVERAGE_FILE=.coverage.scripts $(PYTHON) -m coverage report --show-missing --fail-under=$(SCRIPTS_FLOOR)

gate: validate-config lint format-check typecheck lint-imports frontmatter protected-paths test coverage bridge-coverage contracts-coverage scripts-coverage ## Run the full pre-PR gate

precommit: ## Run every pre-commit hook over the whole tree
	pre-commit run --all-files

# ── Opt-in suites (off by default; each needs its own backing service) ───────

integration: ## Integration suite (RUN_INTEGRATION=1)
	RUN_INTEGRATION=1 $(PYTHON) -m pytest tests/integration --no-cov $(PYTEST_FLAGS)

lmstudio: ## LM Studio E2E (needs a local LM Studio server)
	RUN_LMSTUDIO=1 $(PYTHON) -m pytest tests/lmstudio --no-cov $(PYTEST_FLAGS)

vertex: ## Vertex AI E2E (needs ADC)
	RUN_VERTEX=1 $(PYTHON) -m pytest tests/vertex --no-cov $(PYTEST_FLAGS)

postgres: ## Postgres suite (needs Docker for testcontainers)
	RUN_POSTGRES=1 $(PYTHON) -m pytest tests/postgres --no-cov $(PYTEST_FLAGS)

rag: ## RAG domain suite (fakes only — no extras, no network; CI-safe)
	RUN_RAG=1 $(PYTHON) -m pytest tests/rag --no-cov $(PYTEST_FLAGS)

gated-suites: integration rag ## Gated suites needing no service, extra or network (CI runs this)

embeddings-local: ## Local sentence-transformers suite (needs the embeddings-local extra)
	RUN_EMBEDDINGS_LOCAL=1 RUN_RAG=1 $(PYTHON) -m pytest tests/rag --no-cov $(PYTEST_FLAGS)

# The next three suites are gated by marker (not directory — the tests live
# alongside their unit-test siblings), so each target selects with `-m`
# rather than pointing at a subdirectory.

gcp-secrets: ## Live GCP Secret Manager suite (needs ADC + project)
	RUN_GCP_SECRETS=1 $(PYTHON) -m pytest tests/integration -m gcp_secrets --no-cov $(PYTEST_FLAGS)

gcp-trace: ## Cloud Trace exporter suite (needs the gcp extra installed)
	RUN_GCP_TRACE=1 $(PYTHON) -m pytest tests/test_telemetry.py -m gcp_trace --no-cov $(PYTEST_FLAGS)

langfuse: ## Langfuse sink/source suite (needs the langfuse extra installed)
	RUN_LANGFUSE=1 $(PYTHON) -m pytest tests/eval -m langfuse --no-cov $(PYTEST_FLAGS)

gitleaks-selftest: ## Prove `secret-scan` can still fail (needs the binary `secret-scan` downloads)
	RUN_GITLEAKS=1 $(PYTHON) -m pytest tests/deploy/test_gitleaks_config.py --no-cov $(PYTEST_FLAGS)

# ── Opt-in tooling (off by default; needs the network) ───────────────────────
#
# Not part of `gate`: every step in that chain runs fully offline today, and
# downloading a release binary is the one thing here that doesn't.

secret-scan: ## Gitleaks secret scan (downloads a pinned, checksum-verified release binary; needs network, not part of gate)
	curl -fsSL -o gitleaks.tar.gz \
	  "https://github.com/gitleaks/gitleaks/releases/download/v$(GITLEAKS_VERSION)/gitleaks_$(GITLEAKS_VERSION)_linux_x64.tar.gz"
	echo "$(GITLEAKS_SHA256)  gitleaks.tar.gz" | sha256sum -c -
	tar -xzf gitleaks.tar.gz gitleaks
	chmod +x gitleaks
	rm -f gitleaks.tar.gz
	./gitleaks version
	# We invoke the open-source binary directly rather than the
	# gitleaks/gitleaks-action@v2 wrapper, which requires a paid licence for
	# organisation accounts. The binary itself is MIT-licensed and free.
	# Two passes: `dir` scans the working tree (an uncommitted .env with a
	# real key), `git` scans committed history. Neither subsumes the other.
	# `-c .gitleaks.toml` is not optional: without it gitleaks silently falls
	# back to its built-in ruleset, dropping the connection-string rule that
	# catches a password in MANGOMAS_DB__URL (which no built-in rule sees).
	./gitleaks dir --no-banner --redact --exit-code 1 -c $(GITLEAKS_CONFIG) .
	./gitleaks git --no-banner --redact --exit-code 1 -c $(GITLEAKS_CONFIG) .

pip-audit: ## Audit the installed env AND the runtime lockfile for known CVEs (downloads the advisory DB; needs network, not part of gate)
	$(PYTHON) -m pip install --quiet "pip-audit==$(PIP_AUDIT_VERSION)"
	# Two surfaces, neither subsuming the other — the same shape as secret-scan.
	#
	# 1. The *installed environment* (CI runs this after `pip install -e
	#    ".[dev]"`). This is what CI actually tests, and it covers the dev pins
	#    and extras a runtime-only lockfile audit would never see.
	#    --skip-editable excludes the local editable mangomas checkout itself,
	#    which is not on PyPI and would otherwise fail resolution.
	$(PYTHON) -m pip_audit --skip-editable
	# 2. The *runtime lockfile*. An earlier revision of this comment claimed the
	#    installed environment was "what the runtime wheel resolves against" and
	#    audited it alone. That was wrong: the Dockerfile passes
	#    `-c requirements.lock` (see tests/deploy/test_docker_build_context.py),
	#    so the lock's pins are exactly what the production image installs — the
	#    one artefact that reaches production was the one surface nothing
	#    scanned. tests/deploy/test_lockfile_freshness.py keeps the lock in sync
	#    with pyproject; this keeps it free of known CVEs.
	$(PYTHON) -m pip_audit -r $(RUNTIME_LOCKFILE)

sbom-scan: ## CycloneDX SBOM + Trivy fs scan (downloads a pinned binary; needs network, not part of gate)
	# Same download-and-verify pattern as secret-scan. Baseline scan: findings
	# do not fail (`--exit-code 0`); ratchet to 1 once the report has an owner.
	# Not in `make gate` or PR CI — first scan is nightly-only.
	curl -fsSL -o trivy.tar.gz \
	  "https://github.com/aquasecurity/trivy/releases/download/v$(TRIVY_VERSION)/trivy_$(TRIVY_VERSION)_Linux-64bit.tar.gz"
	echo "$(TRIVY_SHA256)  trivy.tar.gz" | sha256sum -c -
	tar -xzf trivy.tar.gz trivy
	chmod +x trivy
	rm -f trivy.tar.gz
	./trivy --version
	./trivy fs --scanners vuln --severity HIGH,CRITICAL --exit-code 0 --format table .
	./trivy fs --format cyclonedx --output sbom.cdx.json .

# ── Misc ─────────────────────────────────────────────────────────────────────

serve: ## Run the API with reload (factory pattern required)
	$(PYTHON) -m uvicorn mangomas.api.app:create_app --factory --reload

clean: ## Remove caches and coverage artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .import_linter_cache .hypothesis htmlcov \
	       .coverage .coverage.* coverage.xml gitleaks gitleaks.tar.gz \
	       trivy trivy.tar.gz sbom.cdx.json
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
