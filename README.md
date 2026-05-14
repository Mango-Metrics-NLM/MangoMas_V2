# Mango-Mas V2

Local-first, modular agent platform built on FastAPI and LM Studio. Designed
for local development today; architected for GCP swap-in without rewriting
core agent contracts.

> **License** Proprietary. All rights reserved.

---

## Quick start

### Windows (PowerShell)

```powershell
# 1. Create venv and install all dev dependencies
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2. Start LM Studio, load a model, and enable the local server on :1234

# 3. Copy and edit the example env file
Copy-Item .env.example .env
# Set MANGOMAS_LLM__MODEL to your loaded model id, e.g.:
#   MANGOMAS_LLM__MODEL=google/gemma-4-e4b

# 4. Run the API (factory pattern required)
uvicorn mangomas.api.app:create_app --factory --reload

# Or run the CLI
mangomas chat "hello"
```

### bash / macOS / Linux

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn mangomas.api.app:create_app --factory --reload
```

---

## LM Studio configuration

All LLM settings are env-driven via the `MANGOMAS_LLM__*` prefix:

| Variable | Default | Description |
|---|---|---|
| `MANGOMAS_LLM__BASE_URL` | `http://localhost:1234/v1` | LM Studio OpenAI-compatible base URL |
| `MANGOMAS_LLM__MODEL` | `local-model` | Model id as shown in LM Studio |
| `MANGOMAS_LLM__API_KEY` | `lm-studio` | API key (placeholder; LM Studio ignores it) |
| `MANGOMAS_LLM__TIMEOUT_SECONDS` | `60.0` | Per-request timeout |
| `MANGOMAS_LLM__TEMPERATURE` | `0.2` | Sampling temperature |

The application adapter uses the **OpenAI-compatible** endpoint
`/v1/chat/completions` and `/v1/models`. LM Studio also exposes a
beta REST API at `/api/v1/chat`; that path is **not** used by this
client.

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/health` | Alias for `/healthz` (backwards compatibility) |
| `GET` | `/readyz` | Readiness probe — checks LLM + DB connectivity |
| `GET` | `/ready` | Alias for `/readyz` (backwards compatibility) |
| `GET` | `/agents` | List registered agent names |
| `POST` | `/agents/{name}/invoke` | Invoke an agent (buffered response) |
| `POST` | `/agents/{name}/stream` | Stream agent response as Server-Sent Events |

### SSE streaming envelope

Each token is delivered as an SSE `data:` frame with a JSON payload:

```
data: {"event": "token", "data": {"content": "Hello"}, "content": "Hello"}

data: {"event": "done"}
```

The top-level `content` key is included for backwards compatibility with
consumers that have not yet adopted the `event`/`data` structure.

---

## Agent registry

Agents are registered via the module-level `agent_registry` in
`src/mangomas/composition.py`.  Adding a new agent requires:

1. Implement the `Agent` protocol in `src/mangomas/agents/`.
2. Register a factory in `composition.py`:

```python
from mangomas.composition import agent_registry

agent_registry.register("my-agent", lambda settings: MyAgent(settings=settings))
```

No changes to `Orchestrator`, `app.py`, or any existing agent are needed.

---

## Docker

```bash
# Build
docker build -t mangomas .

# Run (exposes port 8000; override with -e PORT=...)
docker run -p 8000:8000 --env-file .env mangomas
```

The image runs as a non-root user, honours `$PORT`, and includes a
HEALTHCHECK that polls `/healthz`.

---

## Quality gates

All gates must pass before merging:

```powershell
# Lint
python -m ruff check src tests scripts

# Format check
python -m ruff format --check src tests scripts

# Type check (strict)
python -m mypy --strict src tests scripts

# Tests + coverage (90% global floor)
python -m pytest

# Per-package coverage floors
python scripts/check_coverage.py
```

### Integration tests

```powershell
# Requires no external service — uses in-process ASGI transport
$env:RUN_INTEGRATION = '1'
python -m pytest tests/integration --no-cov
```

### LM Studio smoke tests

```powershell
# Requires a running LM Studio server (see configuration above)
$env:RUN_LMSTUDIO = '1'
$env:LMSTUDIO_MODEL = 'google/gemma-4-e4b'  # or your loaded model
python -m pytest tests/lmstudio --no-cov
```

---

## Project layout

```
src/mangomas/
  core/         Domain: agent protocol, orchestrator, tool models
  adapters/     llm/, storage/ — swappable for GCP (see ADR-001)
  agents/       Concrete agents: chat, summarize, tool_agent, planner, reviewer
  api/          FastAPI app factory, routes, middleware, health checks
  cli/          Typer CLI
  config.py     Pydantic-settings — all config is env-driven
  composition.py  Composition root — wires registries at startup
  telemetry.py  OpenTelemetry configuration

tests/
  unit/          (inline with src naming) — mocked dependencies
  integration/   ASGITransport-based; set RUN_INTEGRATION=1
  lmstudio/      Real-server tests; set RUN_LMSTUDIO=1

docs/
  adr/           Architecture Decision Records
  architecture/  C4 diagrams (Mermaid)
  testing/       Regression baseline and LM Studio scenario plan
```

---

## Further reading

- [ADR-001: Cloud Target Swap Matrix](docs/adr/0001-cloud-targets.md)
- [C4 Architecture Diagrams](docs/architecture/)
- [Regression Baseline](docs/testing/regression.md)
- [LM Studio E2E Scenario Plan](docs/testing/lmstudio-e2e.md)
- [Changelog](CHANGELOG.md)
- [Next Steps & Roadmap](NEXT_STEPS.md)
