# Core — `src/mangomas/core/`

The stable domain contracts. **Three of the four files here are protected
paths**, which is the fact worth knowing before you edit anything in this
directory and the one thing you cannot see from inside it.

| File | Protected? | Holds |
|---|---|---|
| `agent.py` | **yes** | `Agent` / `StreamingAgent` Protocols, `AgentContext`, `AgentRequest`, `AgentResponse`, `Message` |
| `orchestrator.py` | **yes** | `dispatch`, `dispatch_pipeline`, `dispatch_fan_out`, `stream_dispatch` |
| `tools.py` | **yes** | `ToolSpec`, `ToolCallParser`, `ToolRegistry`, prompt builders |
| `loop.py` | no | `AcceptanceFn` type alias |

Note `core/agent.md` — the file this one replaced — was **not** protected; the
governance table lists `agent.py`. One character apart, and worth knowing if you
ever wonder why an edit here did or did not need a trailer.

## Editing a protected path

Any change to the three files above requires a `BREAKING-CHANGE` marker on at
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

- `AgentContext` is additive-only. It carries seven optional fields today
  (`llm`, `repo`, `extras`, `tools`, `memory`, `embeddings`, `vector_store`);
  adding an eighth must not break a caller constructing it positionally.
- `AcceptanceFn` is **sync**. An async one would couple the orchestrator to its
  callers' event loop.
- `core/` imports only `mangomas.errors` and `mangomas.registry` — never
  `adapters/`, `api/`, `agents/`, `workflow/`, `eval/` or `rag/`. The dependency
  direction points inward, always. Cross-layer type references live under
  `if TYPE_CHECKING:` (that is why `AgentContext` can name `LLMClient` without
  importing the adapter package at runtime).
