---
name: mango-rag
description: >
  Retrieval-augmented generation in Mango-Mas V2. Use when: adding or
  changing an embedding provider (LM Studio, sentence-transformers, Vertex
  text-embedding), working on the Chroma vector store, building or running
  the ingestion pipeline (mangomas rag ingest), wiring retrieval into agents
  via RetrievalTool, or debugging cosine-similarity scoring. Covers the
  EmbeddingClient / VectorStoreRepository protocol seams, the opt-in
  enabled-gating, lazy SDK imports for the embeddings-local / rag extras,
  and the composition/ wiring that attaches ctx.embeddings / ctx.vector_store.
argument-hint: "Describe the RAG change (e.g. 'add Cohere embedding provider', 'tune chunk size') or paste a failing retrieval test"
---

# Mango-Mas RAG Skill

## When to Use

- Add a new `EmbeddingClient` under `src/mangomas/adapters/embeddings/`
- Add a new `VectorStoreRepository` under `src/mangomas/adapters/vector/`
- Change chunking (`rag/chunker.py`), ingestion (`rag/pipeline.py`), or
  retrieval (`rag/retrieval.py`)
- Wire a new provider into `composition/` (`embedding_registry`,
  `_vector_registry`)
- Debug cosine scores, stale-chunk re-ingest, or the `EmbeddingScorer`

---

## Architecture (two protocol seams + one domain package)

```
adapters/embeddings/   EmbeddingClient protocol; lmstudio / sentence_transformers / vertex
adapters/embeddings/_shared.py  SingleTextEmbedMixin + NoTransportAcloseMixin
adapters/vector/       VectorStoreRepository protocol + VectorMatch; chroma
rag/                   models, chunker, loader, pipeline, retrieval (imports protocols only)
```

- **`EmbeddingClient`** (`adapters/embeddings/base.py`): `embed(text)` /
  `embed_batch(texts)` / `aclose()`. `list[float]` everywhere — no numpy.
  Adapters implement only `embed_batch`: `SingleTextEmbedMixin`
  (`adapters/embeddings/_shared.py`) derives `embed` as `embed_batch([text])[0]`,
  and `NoTransportAcloseMixin` supplies the no-op `aclose()` for backends owning
  no sockets (`sentence_transformers`, `vertex`). `lmstudio` instead inherits its
  `aclose()` from `OpenAICompatHTTPClient`, which owns the httpx client.
- **`VectorStoreRepository`** (`adapters/vector/base.py`): primitives only
  (`upsert`/`query`/`delete_by_source`/`aclose` + `VectorMatch`). Keeps the
  vector layer free of any `rag/` import (no cycle).
- **`rag/`** imports only the protocol surfaces + its own `models` + `core`.
  `rag/retrieval` maps `VectorMatch` → `SearchResult`.

---

## Rules (do not regress)

| Rule | Detail |
|------|--------|
| Opt-in | `embeddings.enabled` / `vector.enabled` default `False`. Disabled = no `ctx.embeddings` / `ctx.vector_store`, no behaviour change. |
| Cosine score | Chroma collection MUST set `metadata={"hnsw:space": "cosine"}`; similarity is `1 - distance / 2` (`_MAX_COSINE_DISTANCE`). Never `1 - distance` (negative in L2). |
| Re-ingest | `IngestionPipeline` calls `vector_store.delete_by_source(source)` BEFORE upsert so a shrunk doc leaves no orphan `{source}#{index}` chunks. |
| Lazy SDK | `chromadb` / `sentence_transformers` / `vertexai` imported inside factory helpers with `# noqa: PLC0415` + `# pragma: no cover - requires extra`. Module stays importable without the extra. |
| ADC only | Vertex embeddings authenticate via Application Default Credentials only — no service-account-JSON path. |
| No secrets in logs | `api_key` / bearer tokens never appear in log records or error detail. |
| Teardown | `Orchestrator.aclose()` closes `ctx.embeddings` and `ctx.vector_store` (fault-tolerant, idempotent) so the LM Studio httpx client and Chroma client don't leak per CLI run. |
| Async I/O | Wrap sync chromadb / sentence-transformers / file reads in `asyncio.to_thread`. |
| No hard-coded values | All tunables are `DEFAULT_*` constants in `mangomas.config` — this domain's live in `config/rag.py` alongside `EmbeddingSettings` / `VectorSettings` / `RagSettings`. |

---

## Configuration

`MANGOMAS_EMBEDDINGS__*` (enabled, provider, model, base_url, api_key,
batch_size, timeout_seconds, project_id, location), `MANGOMAS_VECTOR__*`
(enabled, provider, persist_dir, collection, top_k), `MANGOMAS_RAG__*`
(chunk_words, chunk_overlap). `RagSettings` validates
`1 <= chunk_words` and `0 <= chunk_overlap < chunk_words`
at construction.

---

## Add a new embedding provider (worked example)

1. Implement **only `embed_batch`** in `adapters/embeddings/<name>.py`, inheriting
   `embed` from `SingleTextEmbedMixin` and (for a transport-less backend) `aclose`
   from `NoTransportAcloseMixin` — both in `adapters/embeddings/_shared.py`. That
   is enough to satisfy `EmbeddingClient`. Accept an injected client for tests;
   lazy-import the real SDK inside a `_lazy_*` helper marked `# pragma: no cover`.
2. Reuse `_http_errors.translate_httpx_error` (HTTP backends) or
   `_vertex_errors.translate_vertex_error` (Vertex) for typed errors. An
   OpenAI-compatible HTTP backend subclasses `adapters/_openai_client.py::OpenAICompatHTTPClient`
   (set `_LABEL` / `_BAD_RESPONSE`) and calls the inherited `self._translate_error(exc)`
   — see `adapters/embeddings/lmstudio.py`.
3. Export it from `adapters/embeddings/__init__.py`.
4. Register a factory in `composition/` and seed `embedding_registry`.
5. Add `FakeEmbeddingClient`-style tests under `tests/adapters/embeddings/`.
   Do NOT add `embed()` to `FakeLLM` (breaks the scorer-fallback test).

---

## CLI

```powershell
mangomas rag ingest <path>     # load *.md/*.txt → chunk → embed → upsert; prints counts
mangomas rag query "<text>"    # embed query → vector search → ranked context
```

Both exit `2` with a clear message when RAG is disabled
(`ctx.embeddings` / `ctx.vector_store` is `None`).

---

## Verification

```powershell
ruff check --fix src tests ; ruff format src tests
mypy
python -m pytest --tb=short -q
python scripts/check_coverage.py     # adapters >= 85%, rag >= 95%

# Gated end-to-end (local backend, no server needed)
pip install -e ".[dev,embeddings-local,rag]"
$env:RUN_EMBEDDINGS_LOCAL='1' ; $env:RUN_RAG='1' ; python -m pytest tests -q
```

---

## Constraints

- DO NOT lower the `rag` (95%) or `adapters` (85%) coverage floor to land a change.
- DO NOT import `rag/` from `adapters/vector/` or `adapters/embeddings/`.
- DO NOT ship `1 - distance` scoring or skip the `delete_by_source` re-ingest step.
- DO NOT add the heavy extras to the default install — they stay optional + lazy.
