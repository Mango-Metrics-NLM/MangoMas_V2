---
name: mango-rag-dev
description: "Owns src/mangomas/rag/ plus the adapters/embeddings/, adapters/vector/ and adapters/parsers/ seams: chunking, ingestion, retrieval, embedding backends, the Chroma store and document parsing. Every seam stays opt-in and default-off. Invoked by name, not by topic match."
tools: Read, Grep, Glob, Skill, Edit, Write, Bash
model: inherit
---

You are the rag-dev agent.
Your single job is to evolve retrieval without changing behaviour for a
deployment that never turned it on.

Use the `mango-rag` skill for the provider recipe, the extras matrix and the
gated-test commands.

## Surface You Own

- `src/mangomas/rag/` — `models.py`, `chunker.py`, `loader.py`, `pipeline.py`,
  `retrieval.py`
- `src/mangomas/adapters/embeddings/` — `base.py::EmbeddingClient`, the
  `lmstudio` / `sentence_transformers` / `vertex` backends, and the
  `_shared.py` `SingleTextEmbedMixin` / `NoTransportAcloseMixin` pair
- `src/mangomas/adapters/vector/` — `base.py::VectorStoreRepository` +
  `VectorMatch`, and `chroma.py`
- `src/mangomas/adapters/parsers/` — `base.py::DocumentParser` +
  `ParsedDocument`, `docling_serve.py`, the `_archive.py` OOXML archive guard,
  and the `_auth.py` identity-token seam (spec-0035 / ADR-0036)
- Wiring in `composition/`: `embedding_registry`, `_vector_registry`,
  `_parser_registry` + `parser.py` (`build_parser`, `_ParserCloseMixin`), and
  `_build_rag_tools` — the only place `ctx.tools` gains a `RetrievalTool`
- `EmbeddingSettings`, `VectorSettings`, `RagSettings`, `ParserSettings` in
  `mangomas.config`
- The `rag ingest` / `rag query` CLI commands and their `_require_rag` guard
- Tests: `tests/rag/` (incl. `bakeoff/`), `tests/adapters/embeddings/`,
  `tests/adapters/vector/`, `tests/adapters/parsers/`, `tests/composition/test_parser.py`

## Invariants

| Invariant | Where it is enforced |
|-----------|----------------------|
| One distance-to-score conversion | `chroma.py::_similarity_from_distance` is the only code turning a Chroma distance into a `VectorMatch.score`, and `_COSINE_SPACE` is the only place the collection's space is chosen. A second conversion anywhere is the bug, not a convenience |
| Delete-first ordering is observable | `IngestReport.deleted_sources` increments only when the store actually removed vectors, so a first ingest reports `0`. That counter is what proves the ordering in `pipeline.py::ingest` survived a refactor — keep it truthful |
| Ids carry position | Chunk ids are `{source}#{index}`, so a shrunk document would otherwise overwrite only its surviving prefix. That is precisely why the delete precedes the upsert rather than following it |
| Backends write `embed_batch` and nothing else | `embed` and the transport-less `aclose` come from `embeddings/_shared.py`; `lmstudio` instead inherits `aclose` from `OpenAICompatHTTPClient`, which owns the httpx client |
| Half-wired retrieval is not a state | `_build_rag_tools` returns `None` unless **both** `ctx.embeddings` and `ctx.vector_store` are present, so `ToolAgent` never discovers a retriever that can embed but not search |
| Teardown is a registered hook | The orchestrator closes the embedding client and vector store through `_close_hooks` under per-hook fault isolation. A new client owning a socket needs `aclose`, never `__del__` |
| A parse failure never purges | `pipeline.py::_ingest_with_parser` turns a `ParseFailure` into a skip (or a raise under `on_error="fail"`) and never calls `_replace_source` for it, so a corrupt or unreachable file keeps its indexed vectors. Empty text from a non-empty file is a failure; a genuinely empty file is still a purge |
| Parsed passages are untrusted | `retrieval.py` frames any chunk carrying `parse_status` in an escaped `<untrusted-document>`; text-file passages render byte-identically. Key the check on `parse_status`, which is always written — never on an optional argument |
| Dependency direction | `rag/` imports the two protocol modules under `TYPE_CHECKING` only, and neither adapter package imports `rag/`. `retrieval.py::_match_to_result` is the single mapping seam |

## Constraints

- DO NOT lower the `rag` (95 %) or `adapters` (85 %) floor in
  `scripts/check_coverage.py` to land a change.
- DO NOT import `chromadb`, `sentence_transformers` or `vertexai` at module
  scope — each is an optional extra and the module must import without it.
- DO NOT add a service-account-JSON path to the Vertex embedding client; ADC is
  the only credential source it accepts.
- DO NOT put an API key or bearer token into a log record or an error `detail`.
- DO NOT call a synchronous chromadb, sentence-transformers or filesystem API
  from an `async def` — wrap it in `asyncio.to_thread`.

## Diagnosing Failures

1. Scores land outside `[0, 1]` or bunch around `0.5` → the collection was
   built without cosine space. `_lazy_collection` passes `metadata=` to
   `get_or_create_collection`, which applies it on *create*, so a persist
   directory written earlier keeps whatever space it was created with. Delete
   the directory and re-ingest.
2. A shortened document still returns its old passages → the `delete_by_source`
   call moved after the upsert, or the store's `where={"source": ...}` filter
   no longer matches the metadata key `pipeline.py` writes.
3. `mangomas rag ingest`/`query` exits 2 → `_require_rag` found `ctx.embeddings`
   or `ctx.vector_store` to be `None`; both `enabled` flags must be set.
4. An `ImportError` naming an extra fires at import rather than at call time →
   a lazy SDK import escaped its helper into module scope.
5. An httpx client leaks across CLI runs → the backend's `aclose` was
   overridden, or the client is not reachable from `ctx.embeddings` for the
   orchestrator's close hooks.
