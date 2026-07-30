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
.PHONY: help install lint format format-check typecheck frontmatter test test-xml \
        coverage bridge-coverage gate precommit serve clean \
        integration lmstudio vertex postgres rag

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with dev extras
	$(PYTHON) -m pip install -e ".[dev]"

# ── Quality gate (mirrors .github/workflows/ci.yml) ──────────────────────────

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
	$(PYTHON) -m coverage run --source=$(BRIDGE_SRC) -m pytest \
	  $(BRIDGE_TESTS) -o addopts="" $(PYTEST_FLAGS)
	$(PYTHON) -m coverage report --show-missing --fail-under=$(BRIDGE_FLOOR)

gate: lint format-check typecheck frontmatter test coverage bridge-coverage ## Run the full pre-PR gate

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

# ── Misc ─────────────────────────────────────────────────────────────────────

serve: ## Run the API with reload (factory pattern required)
	$(PYTHON) -m uvicorn mangomas.api.app:create_app --factory --reload

clean: ## Remove caches and coverage artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov \
	       .coverage .coverage.* coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
