# API Layer — `src/mangomas/api/`

The HTTP surface of Mango-Mas V2, built on **FastAPI 0.115+**.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/agents/{name}/invoke` | Single-turn agent invocation; returns `AgentResponse` JSON |
| `POST` | `/agents/{name}/stream` | Streaming SSE; emits `event: token` frames then `event: done` |
| `GET`  | `/health` | Liveness probe; returns `{"status": "ok"}` |

## Architecture Rules

- **No business logic in route handlers** — delegate everything to `Orchestrator`.
- **No concrete adapter imports** — access the `Orchestrator` via `request.app.state`.
- **Error mapping** lives exclusively in `_ERROR_STATUS`:
  ```python
  _ERROR_STATUS: dict[type[MangomasError], int] = {
      AgentNotFound: 404,
      LLMBadResponse: 422,
      LLMError: 502,
      MaxStepsExceeded: 422,
      ToolNotFound: 400,
      ToolExecutionError: 502,
  }
  ```
- **Lifespan** (`@asynccontextmanager`) wires `build_orchestrator(settings)` on startup and closes `memory` on shutdown.

## Request/Response Envelope

```python
# Invoke request body
{
  "messages": [{"role": "user", "content": "..."}],
  "metadata": {},
  "max_steps": 1
}

# Invoke response
{
  "content": "...",
  "agent": "chat",
  "metadata": {"loop": {"steps_taken": 1, "accepted": false}}
}

# Error envelope (any MangomasError)
{
  "error": "agent_not_found",
  "detail": "No agent registered under 'ghost'"
}
```

## Streaming SSE Format

```
event: token
data: Hello

event: token
data:  world

event: done
data: {}
```

## Adding a New Endpoint

1. Add the route function in `app.py` — keep it thin (validate → delegate → return).
2. If a new `MangomasError` is raised, add it to `_ERROR_STATUS`.
3. Add/update `tests/test_api.py` using `httpx.AsyncClient`.
4. Document the endpoint in the API reference (`docs/api/`).
