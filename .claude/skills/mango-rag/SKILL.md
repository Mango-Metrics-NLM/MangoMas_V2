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
  the composition/ wiring that attaches ctx.embeddings / ctx.vector_store,
  and PDF/Office ingestion through the DocumentParser seam (docling-serve).
argument-hint: "Describe the RAG change (e.g. 'add Cohere embedding provider', 'tune chunk size') or paste a failing retrieval test"
---

# Mango-Mas RAG Skill

## When to Use

- Add a new `EmbeddingClient` under `src/mangomas/adapters/embeddings/`
- Add a new `VectorStoreRepository` under `src/mangomas/adapters/vector/`
- Add or configure a `DocumentParser` under `src/mangomas/adapters/parsers/`
- Change chunking (`rag/chunker.py`), ingestion (`rag/pipeline.py`), or
  retrieval (`rag/retrieval.py`)
- Wire a new provider into `composition/` (`embedding_registry`,
  `_vector_registry`, `parser_registry`)
- Debug cosine scores, stale-chunk re-ingest, or the `EmbeddingScorer`

---

## Architecture (three protocol seams + one domain package)

```
adapters/embeddings/   EmbeddingClient protocol; lmstudio / sentence_transformers / vertex
adapters/embeddings/_shared.py  SingleTextEmbedMixin + NoTransportAcloseMixin
adapters/vector/       VectorStoreRepository protocol + VectorMatch; chroma
adapters/parsers/      DocumentParser protocol; docling_serve, _archive.py, _auth.py
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
- **`DocumentParser`** (`adapters/parsers/base.py`): `parse(doc: RawDoc) -> ParsedDocument` /
  `aclose()`. Non-text document parsing (.pdf, .docx, .pptx, .xlsx) via Docling Serve.
  Protected by `_archive.py` against OOXML zip bombs.
- **`rag/`** imports only the protocol surfaces + its own `models` + `core`.
  `rag/retrieval` maps `VectorMatch` → `SearchResult`.

---

## Rules (do not regress)

| Rule | Detail |
|------|--------|
| Opt-in | `embeddings.enabled` / `vector.enabled` / `parser.enabled` default `False`. Disabled = no `ctx.embeddings` / `ctx.vector_store`, no behaviour change. |
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

## Document parsing (spec-0035 / ADR-0036)

`MANGOMAS_PARSER__ENABLED=true` makes `rag ingest` send `.pdf/.docx/.pptx/.xlsx`
to docling-serve through `DocumentParser`. Rules: limits are enforced before
upload; a `ParseFailure` is skipped (counted) or raised, **never** purged;
parsed text is chunked by `chunk_lines` (whole lines); parsed chunks carry
`parser`/`parse_status`/`chunker`/`chunk_words`/`embedding_model` and are
framed as `<untrusted-document>` on retrieval.

**Add a parser provider:** implement `DocumentParser` in
`adapters/parsers/<name>.py` (primitives only, no `rag/` import, typed errors —
`DocumentParseError` for document problems, `ConfigError` for credentials);
register it in `composition/parser.py`; add a `Fake`-driven conformance test,
respx/fixture tests for every status, a canary test proving no document text
or secret reaches logs/spans, and a row in `adapters/CLAUDE.md`.

---

## CLI

```powershell
mangomas rag ingest <path>     # load *.md/*.txt (+ PDF/Office when the parser is on) → chunk → embed → upsert; prints counts (+ skipped=N)
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

# Gated parser bake-off (needs docling-serve + a real embedder)
$env:RUN_DOCLING='1' ; make docling-bakeoff
```

---

## Constraints

- DO NOT lower the `rag` (95%) or `adapters` (85%) coverage floor to land a change.
- DO NOT import `rag/` from `adapters/vector/` or `adapters/embeddings/`.
- DO NOT ship `1 - distance` scoring or skip the `delete_by_source` re-ingest step.
- DO NOT add the heavy extras to the default install — they stay optional + lazy.
- DO NOT let a parse failure reach `_replace_source`, or send a file to the parser before the size/archive limits pass.
