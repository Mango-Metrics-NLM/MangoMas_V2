# Core — `src/mangomas/core/`

The stable domain contracts. **Four of the five modules here are protected
paths** (`agent.py`, `orchestrator.py`, `structured.py`, `tools.py`;
`loop.py` is not, and `__init__.py` is a re-export facade), which is the fact
worth knowing before you edit anything in this
directory and the one thing you cannot see from inside it.

| File | Protected? | Holds |
|---|---|---|
| `agent.py` | **yes** | `Agent` / `StreamingAgent` Protocols, `AgentContext`, `AgentRequest`, `AgentResponse`, `Message` |
| `orchestrator.py` | **yes** | `dispatch`, `dispatch_pipeline`, `dispatch_fan_out`, `stream_dispatch` |
| `structured.py` | **yes** | `build_structured_prompt`, `parse_or_recover`, `parse_llm_json_object`, `_extract_json_span` (spec-0015 R4 extraction) |
| `tools.py` | **yes** | `ToolSpec`, `ToolCallParser`, `ToolRegistry`, tool prompt builder; permanent re-export facade for the names extracted to `structured.py` (ADR-0019) |
| `loop.py` | no | `AcceptanceFn` type alias |

Note `core/agent.md` — the file this one replaced — was **not** protected; the
governance table lists `agent.py`. One character apart, and worth knowing if you
ever wonder why an edit here did or did not need a trailer.

## Editing a protected path

Any change to the four files above requires a `BREAKING-CHANGE` marker on at
least one commit message in the PR, or the `Protected-path governance gate` CI
job fails the build.

The `PreToolUse` hook that warns about this is **advisory**: it matches
`Edit|Write|NotebookEdit` and therefore cannot see a `Bash` heredoc or a `>`
redirect. A quiet session is not evidence — committed history is.

Use the `mango-harness` skill for the full contract, and the `mango-topology`
skill for the dispatch surface's shape.

## Owners

| Surface | Agent |
|---|---|
| `orchestrator.py` and the whole dispatch surface | `mango-orchestrator-dev` |
| `agent.py`'s DTOs and their backward-compatible evolution | `mango-schema-evolution` |
| `tools.py` / `agent.py` property tests | `mango-hypothesis-fuzz` |

## Invariants

- `AgentContext` is additive-only. It carries seven fields today — `llm` and
  `repo` are required (no defaults), and five are optional (`extras`, `tools`,
  `memory`, `embeddings`, `vector_store`). An eighth must be optional and must
  not break a caller constructing it positionally. `extras` is the sanctioned
  escape hatch for per-request data that does not deserve a field.
- `AcceptanceFn` is **sync**. An async one would couple the orchestrator to its
  callers' event loop.
- `core/` imports only `mangomas.errors`, `mangomas.registry` and — since
  ADR-0026 — `mangomas.metrics` (a telemetry leaf over the OTel API
  `orchestrator.py` already imports; the record helpers are no-ops until
  metrics are enabled) — never `adapters/`, `api/`, `agents/`, `workflow/`,
  `eval/` or `rag/`. The dependency direction points inward, always.
  Cross-layer type references live under `if TYPE_CHECKING:` (that is why
  `AgentContext` can name `LLMClient` without importing the adapter package
  at runtime, and why `Orchestrator` can name `LoopSettings`).
