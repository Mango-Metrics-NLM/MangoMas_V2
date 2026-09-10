# C4 — Code Architecture (Core Domain & Implementation Contracts)

This document specifies the **Level 4 (Code)** architecture of the Mango-Mas V2 platform. It formalizes the concrete Python abstractions, runtime protocols, dependency inversion boundaries, and data contracts that realize the containers and components defined in [c1-context.md](c1-context.md), [c2-container.md](c2-container.md), and [c3-component.md](c3-component.md).

---

## 1. Architectural Style & Design Principles

Mango-Mas V2 follows **Hexagonal Architecture (Ports and Adapters)** with strict Layered Invariants:

1. **Core Domain Independence**: `src/mangomas/core/` defines runtime protocols (`Agent`, `Tool`, `Orchestrator`) and immutable domain entities (`AgentRequest`, `AgentResponse`, `Turn`, `Message`). It possesses zero dependencies on third-party frameworks, cloud SDKs, or database drivers.
2. **Dependency Inversion**: Outer layers (CLI, FastAPI routes, persistence adapters, cloud SDKs) depend inwards on Core protocols. Components bind via `Registry[T]` and the composition root (`src/mangomas/composition/`).
3. **Protected Core Contracts (ADR-0021)**: Core contracts are locked under `[tool.mangomas.governance]`. Changes to protected files require explicit breaking-change commit trailers verified by `scripts/check_protected_paths.py`.
4. **Resilient Failure Encapsulation**: Cloud and external service exceptions (e.g. GCP Secret Manager, Vertex AI, LM Studio HTTP) are caught at the adapter perimeter and mapped to the typed `MangomasError` hierarchy.

```mermaid
classDiagram
    direction TB
    
    class Agent {
        <<Protocol>>
        +name: str
        +handle(ctx: AgentContext, request: AgentRequest) AgentResponse
    }
    
    class StreamingAgent {
        <<Protocol>>
        +stream(ctx: AgentContext, request: AgentRequest) AsyncIterator[str]
    }
    
    class LLMClient {
        <<Protocol>>
        +complete(messages: list[Message]) str
    }
    
    class StreamingLLMClient {
        <<Protocol>>
        +stream(messages: list[Message]) AsyncIterator[str]
    }
    
    class EmbeddingClient {
        <<Protocol>>
        +embed(text: str) list[float]
        +embed_batch(texts: list[str]) list[list[float]]
        +aclose() None
    }
    
    class TurnRepository {
        <<Protocol>>
        +save_turn(turn: Turn) None
        +get_turns(limit: int) list[Turn]
    }
    
    class VectorStoreRepository {
        <<Protocol>>
        +upsert(chunks: list[Chunk], embeddings: list[list[float]]) None
        +search(embedding: list[float], top_k: int) list[VectorMatch]
    }
    
    class Orchestrator {
        -registry: Registry[Agent]
        -ctx: AgentContext
        +dispatch(name: str, request: AgentRequest) AgentResponse
        +stream_dispatch(name: str, request: AgentRequest) AsyncIterator[str]
        +dispatch_pipeline(pipeline: list[str], input: str) AgentResponse
        +dispatch_fan_out(names: list[str], input: str) list[AgentResponse]
    }
    
    class AgentContext {
        +llm: LLMClient
        +repo: TurnRepository
        +memory: MemoryRepository
        +tools: ToolRegistry
        +extras: dict[str, Any]
    }
    
    Orchestrator --> AgentContext : manages
    Orchestrator --> Agent : resolves & invokes
    Agent <|.. ChatAgent : implements
    Agent <|.. ToolAgent : implements
    Agent <|.. SummarizeAgent : implements
    StreamingAgent <|.. ChatAgent : implements
    AgentContext --> LLMClient : provides
    AgentContext --> TurnRepository : provides
    LLMClient <|.. LMStudioClient : implements
    LLMClient <|.. VertexClient : implements
    EmbeddingClient <|.. SentenceTransformersEmbeddingClient : implements
    EmbeddingClient <|.. LMStudioEmbeddingClient : implements
    TurnRepository <|.. SQLiteRepository : implements
    TurnRepository <|.. PostgresTurnRepository : implements
```

---

## 2. Core Domain Abstractions (`src/mangomas/core/`)

### 2.1 The Agent Protocol & Context Model

- **`Agent`**: Minimal protocol requiring `name: str` and `async def handle(ctx: AgentContext, request: AgentRequest) -> AgentResponse`.
- **`StreamingAgent`**: Extended protocol requiring `async def stream(ctx: AgentContext, request: AgentRequest) -> AsyncIterator[str]`. If an agent does not implement `StreamingAgent`, the `Orchestrator` falls back to buffering `handle()` and yielding a single chunk.
- **`AgentContext`**: Runtime state carrier housing the injected `LLMClient`, `TurnRepository`, optional `MemoryRepository`, `ToolRegistry`, and extensibility `extras: dict[str, Any]` (e.g. cognitive sink hooks, tracing bags).
- **`AgentRequest` / `AgentResponse`**: Frozen dataclasses carrying the prompt, session identifier, correlation tokens, metadata, and final output.

### 2.2 Orchestration & Topologies

- **`Orchestrator`**: Dispatches execution requests against a thread-safe `Registry[Agent]`.
- **Iterative Control Loop**: Supports `AcceptanceFn` predicates to drive multi-turn refinement loops with bounded step limits (`max_steps`).
- **Pipeline & Fan-Out Topologies**:
  - `dispatch_pipeline`: Sequences execution across agent stages $A \to B \to C$, piping prior stage output to the next request.
  - `dispatch_fan_out`: Executes concurrent dispatches across multiple agents via `asyncio.gather` and collates results.
- **Harness Extension**: `_HarnessOrchestrator` wraps execution in OpenTelemetry parent spans when `MANGOMAS_HARNESS__ENABLED=true`, attributing span events with topology and message metadata without altering domain logic.

---

## 3. Adapters & Hexagonal Boundaries (`src/mangomas/adapters/`)

### 3.1 LLM Adapters (`adapters/llm/`)

- **`OpenAICompatHTTPClient`**: Shared base class for OpenAI-compatible REST endpoints (`LMStudioClient`). Handles `httpx` lifecycle, request execution, timeouts, connection pooling, and error translation.
- **`VertexClient`**: Native Google Cloud Vertex AI client using the `google-cloud-aiplatform` SDK. Implements lazy SDK importing (`# noqa: PLC0415`) so that the core package remains functional when the optional `[vertex]` extra is not installed.
- **Shared Translation**: `_http_errors.py` and `_vertex_errors.py` map raw network and API exceptions into domain-level `LLMError`, `LLMUnavailableError`, `LLMTimeoutError`, and `LLMRateLimitError`.

### 3.2 Embedding Adapters (`adapters/embeddings/`)

- **`SingleTextEmbedMixin`**: Implements `embed(text)` by delegating to `embed_batch([text])` and extracting the head vector.
- **`NoTransportAcloseMixin`**: Provides a no-op `aclose()` for embedded in-process models.
- **`SentenceTransformersEmbeddingClient`**: Local execution adapter backed by `sentence-transformers`.
  - Runs compute-bound inference in `asyncio.to_thread`.
  - Suppresses progress bar output (`show_progress_bar=False`) to prevent stdout corruption and pipe deadlocks in headless CLI/API runtimes.
  - Implements defensive exception handling (`try ... except TypeError`) allowing both production HuggingFace models and simplified unit test mocks to function seamlessly.

### 3.3 Persistence & Vector Storage (`adapters/storage/`, `adapters/vector/`)

- **`SQLiteRepository`**: Embedded SQLite implementation for single-node local execution, supporting WAL mode and atomic turn insertion.
- **`PostgresTurnRepository`**: Enterprise multi-tenant relational persistence backed by `asyncpg`. Automatically isolates turn records by `tenant_id`.
- **`ChromaVectorStore`**: Embedded vector database adapter implementing `VectorStoreRepository` for dense document chunk retrieval.

---

## 4. Pure-Domain RAG Subsystem (`src/mangomas/rag/`)

```mermaid
flowchart LR
    DocDir[File / Directory] --> Loader[loader.py: load_documents]
    Loader --> RawDocs[RawDoc list with PathString]
    RawDocs --> Chunker[chunker.py: chunk_document]
    Chunker --> Chunks[Chunk list]
    Chunks --> Pipeline[pipeline.py: IngestionPipeline]
    Pipeline --> Embedder[EmbeddingClient: embed_batch]
    Embedder --> VectorStore[VectorStoreRepository: upsert]
    
    Query[User Query] --> Retriever[retrieval.py: Retriever]
    Retriever --> EmbedQuery[EmbeddingClient: embed]
    EmbedQuery --> Search[VectorStoreRepository: search]
    Search --> SearchResults[SearchResult list]
```

- **Cross-Platform Path Representation (`PathString`)**: A specialized `str` subclass in `rag/loader.py` that normalizes Windows backslash (`\`) and POSIX forward slash (`/`) path formats, ensuring that document IDs remain identical regardless of host operating system.
- **Chunker**: Pure word-window chunking algorithm splitting text by token boundaries with configurable overlap.
- **`IngestionPipeline`**: End-to-end ingestion service that orchestrates loading, chunking, batch embedding generation, and vector store upsertion.
- **`Retriever` & `RetrievalTool`**: Encapsulates semantic vector search into an invocable `ToolSpec`, allowing `ToolAgent` to query ingested corporate knowledge within standard agent control loops.

---

## 5. Declarative Workflow Graph (`src/mangomas/workflow/`)

The workflow engine executes arbitrary directed acyclic and cyclic agent topologies defined via JSON or YAML schemas:

- **`WorkflowGraph`**: Immutable DAG specification comprising `WorkflowNode` nodes and directed edges.
- **`WorkflowNode` Hierarchy**:
  - `AgentNode`: Invokes a registered domain agent.
  - `SequenceNode`: Chains nodes in serial execution.
  - `FanOutNode`: Dispatches parallel branch evaluation.
  - `LoopNode`: Iterates execution until an `AcceptanceFn` returns true or step limits are reached.
  - `BranchNode`: Evaluates conditional branching based on compiled `PredicateSpec` rules.
- **Predicate Compilation (`predicate.py`)**: Compiles JSON predicate expressions (e.g. `contains`, `regex_match`, `json_keys`) into synchronous acceptance predicates executed over node responses.

---

## 6. Cognitive Plane & Governance (`src/mangomas/cognitive/`, `src/mangomas/harness/`)

### 6.1 CognitiveSignal Contracts

- Interoperates with `mango-integration-contracts` to emit machine-readable telemetry on agent performance, risk, and PII detection.
- **Non-Interference Invariants**: Enforces strict policy decoupling; cognitive signals are observational artifacts and cannot alter deterministic policy input shapes or authorization paths.

### 6.2 Governance & Protected Core Paths

`scripts/check_protected_paths.py` enforces commit-level governance:

```toml
[tool.mangomas.governance]
protected_paths = [
    "src/mangomas/core/agent.py",
    "src/mangomas/core/orchestrator.py",
    "src/mangomas/core/structured.py",
    "src/mangomas/core/tools.py",
    "src/mangomas/errors.py",
    "src/mangomas/registry.py",
]
breaking_change_marker_aliases = ["BREAKING-CHANGE", "# approved-breaking-change"]
```

---

## 7. Error Taxonomy & Resilience Mechanics

All system exceptions derive from `MangomasError`:

```mermaid
classDiagram
    class Exception
    class MangomasError
    class ConfigurationError
    class AgentNotFoundError
    class MaxStepsExceededError
    class LLMError
    class LLMUnavailableError
    class LLMTimeoutError
    class StorageError
    class SecretsResolutionError
    class WorkflowError

    Exception <|-- MangomasError
    MangomasError <|-- ConfigurationError
    MangomasError <|-- AgentNotFoundError
    MangomasError <|-- MaxStepsExceededError
    MangomasError <|-- LLMError
    LLMError <|-- LLMUnavailableError
    LLMError <|-- LLMTimeoutError
    MangomasError <|-- StorageError
    MangomasError <|-- SecretsResolutionError
    MangomasError <|-- WorkflowError
```

- **HTTP Status Mapping**: The FastAPI exception handler walks the exception MRO to yield canonical HTTP status codes (`ConfigError` $\to$ 400, `AgentNotFound` $\to$ 404, `MaxStepsExceeded` $\to$ 422, `LLMUnavailable` $\to$ 503, `LLMTimeout` $\to$ 504).
- **Correlation Propagation**: Every error envelope carries `error`, `message`, `correlation_id`, and ISO-8601 `timestamp`.
