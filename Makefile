# Mango-Mas V2 — developer task runner.
#
# Every target wraps the exact command CI runs, so `make gate` locally is the
# same gate as `.github/workflows/ci.yml`. Paths and env-var names live in
# variables below rather than being repeated per-target — override any of them
# on the command line, e.g. `make test PYTEST_FLAGS=-x`.

PYTHON      ?= python
# Lint/type-check surface — matches ci.yml's `lint` job exactly.
CODE_PATHS  ?= src tests scripts eval_harness_bridge/src
BRIDGE_SRC  ?= eval_harness_bridge/src
BRIDGE_TESTS ?= tests/eval_harness_bridge
BRIDGE_FLOOR ?= 100
PYTEST_FLAGS ?= -q

.DEFAULT_GOAL := help
.PHONY: help install validate-config lint format format-check typecheck frontmatter test test-xml \
        coverage bridge-coverage gate precommit serve clean \
        integration lmstudio vertex postgres rag gcp-secrets gcp-trace langfuse

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with dev extras
	$(PYTHON) -m pip install -e ".[dev]"

# ── Quality gate (mirrors .github/workflows/ci.yml) ──────────────────────────

validate-config: ## Validate .mcp.json / .claude/settings.json JSON syntax
	$(PYTHON) -m json.tool .mcp.json > /dev/null
	$(PYTHON) -m json.tool .claude/settings.json > /dev/null

lint: ## ruff check
	$(PYTHON) -m ruff check $(CODE_PATHS)

format: ## ruff format (writes)
	$(PYTHON) -m ruff format $(CODE_PATHS)

format-check: ## ruff format --check
	$(PYTHON) -m ruff format --check $(CODE_PATHS)

typecheck: ## mypy --strict
	$(PYTHON) -m mypy --strict $(CODE_PATHS)

frontmatter: ## Validate .agent.md / SKILL.md frontmatter
	$(PYTHON) scripts/lint_agent_frontmatter.py

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

gate: validate-config lint format-check typecheck frontmatter test coverage bridge-coverage ## Run the full pre-PR gate

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

rag: ## RAG + local-embedding suites (needs the rag/embeddings-local extras)
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

# ── Misc ─────────────────────────────────────────────────────────────────────

serve: ## Run the API with reload (factory pattern required)
	$(PYTHON) -m uvicorn mangomas.api.app:create_app --factory --reload

clean: ## Remove caches and coverage artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov \
	       .coverage .coverage.* coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
