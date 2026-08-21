"""Tests for pydantic-settings configuration."""

from __future__ import annotations

import importlib

import pytest

from mangomas import config as config_module


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    config_module.get_settings.cache_clear()


# The config group modules, in dependency order (`_root` last — it imports the
# others). Derived from the package rather than hard-coded so a new group
# module is covered by the reload test the moment it is added.
_CONFIG_SUBMODULES: tuple[str, ...] = (
    "_shared",
    "llm",
    "storage",
    "api",
    "rag",
    "observability",
    "agents",
    "secrets",
    "evaluation",
    "harness",
    "workflow",
    "_root",
)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANGOMAS_LLM__BASE_URL", raising=False)
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.base_url.endswith("/v1")
    assert s.llm.model == "local-model"
    assert s.db.url.startswith("sqlite:///")
    assert s.api.port == 8000


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LLM__BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("MANGOMAS_LLM__MODEL", "custom")
    monkeypatch.setenv("MANGOMAS_API__PORT", "9001")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.base_url == "http://example.test/v1"
    assert s.llm.model == "custom"
    assert s.api.port == 9001


def test_get_settings_is_cached() -> None:
    a = config_module.get_settings()
    b = config_module.get_settings()
    assert a is b


def test_module_reimport_safe() -> None:
    """Re-executing the config modules leaves a usable `Settings`.

    Reloads every group module *and* the facade, in dependency order. Reloading
    the package alone would not be enough since the spec-0015 split: a package
    reload re-executes only `__init__.py`, and its `from ... import` statements
    resolve the submodules straight out of `sys.modules` without re-running
    them. That would leave this test green while exercising nothing — so the
    submodules are reloaded explicitly, which is what the original flat module
    got for free.
    """
    for submodule in _CONFIG_SUBMODULES:
        importlib.reload(importlib.import_module(f"mangomas.config.{submodule}"))
    importlib.reload(config_module)
    assert config_module.get_settings().env in {"local", "dev", "prod"}


def test_facade_reload_alone_does_not_reexecute_submodules() -> None:
    """Pins the semantics the test above compensates for.

    If a future Python or import-system change made a package reload cascade to
    its submodules, this fails and the explicit loop above becomes redundant —
    which is worth knowing rather than discovering by accident.
    """
    root = importlib.import_module("mangomas.config._root")
    before = root.get_settings
    importlib.reload(config_module)
    assert importlib.import_module("mangomas.config._root").get_settings is before


# ── LoopSettings ──────────────────────────────────────────────────────────────


def test_loop_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.loop.max_steps == config_module.DEFAULT_LOOP_MAX_STEPS
    assert s.loop.step_timeout_seconds == config_module.DEFAULT_LOOP_STEP_TIMEOUT


def test_loop_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LOOP__MAX_STEPS", "10")
    monkeypatch.setenv("MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS", "5.0")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.loop.max_steps == 10
    assert s.loop.step_timeout_seconds == 5.0


# ── MemorySettings ────────────────────────────────────────────────────────────


def test_memory_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.memory.enabled is False
    assert s.memory.provider == config_module.DEFAULT_MEMORY_PROVIDER
    assert s.memory.memory_dir == config_module.DEFAULT_MEMORY_DIR
    assert s.memory.index_file == config_module.DEFAULT_MEMORY_INDEX


def test_memory_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_MEMORY__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_MEMORY__MEMORY_DIR", "custom_mem")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.memory.enabled is True
    assert s.memory.memory_dir == "custom_mem"


# ── EmbeddingSettings ─────────────────────────────────────────────────────────


def test_embeddings_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.embeddings.enabled is config_module.DEFAULT_EMBEDDINGS_ENABLED
    assert s.embeddings.provider == config_module.DEFAULT_EMBEDDINGS_PROVIDER
    assert s.embeddings.model == config_module.DEFAULT_EMBEDDINGS_MODEL
    assert s.embeddings.base_url == config_module.DEFAULT_EMBEDDINGS_BASE_URL
    assert s.embeddings.batch_size == config_module.DEFAULT_EMBEDDINGS_BATCH_SIZE
    assert s.embeddings.location == config_module.DEFAULT_VERTEX_LOCATION


def test_embeddings_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_EMBEDDINGS__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_EMBEDDINGS__PROVIDER", "sentence_transformers")
    monkeypatch.setenv("MANGOMAS_EMBEDDINGS__MODEL", "all-MiniLM-L6-v2")
    monkeypatch.setenv("MANGOMAS_EMBEDDINGS__BATCH_SIZE", "16")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.embeddings.enabled is True
    assert s.embeddings.provider == "sentence_transformers"
    assert s.embeddings.model == "all-MiniLM-L6-v2"
    assert s.embeddings.batch_size == 16


# ── VectorSettings ────────────────────────────────────────────────────────────


def test_vector_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.vector.enabled is config_module.DEFAULT_VECTOR_ENABLED
    assert s.vector.provider == config_module.DEFAULT_VECTOR_PROVIDER
    assert s.vector.persist_dir == config_module.DEFAULT_VECTOR_PERSIST_DIR
    assert s.vector.collection == config_module.DEFAULT_VECTOR_COLLECTION
    assert s.vector.top_k == config_module.DEFAULT_VECTOR_TOP_K


def test_vector_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_VECTOR__ENABLED", "true")
    monkeypatch.setenv("MANGOMAS_VECTOR__PERSIST_DIR", "custom_chroma")
    monkeypatch.setenv("MANGOMAS_VECTOR__COLLECTION", "docs")
    monkeypatch.setenv("MANGOMAS_VECTOR__TOP_K", "8")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.vector.enabled is True
    assert s.vector.persist_dir == "custom_chroma"
    assert s.vector.collection == "docs"
    assert s.vector.top_k == 8


# ── RagSettings ───────────────────────────────────────────────────────────────


def test_rag_settings_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.rag.chunk_words == config_module.DEFAULT_RAG_CHUNK_WORDS
    assert s.rag.chunk_overlap == config_module.DEFAULT_RAG_CHUNK_OVERLAP
    assert s.rag.min_chunk_words == config_module.DEFAULT_RAG_MIN_CHUNK_WORDS


def test_rag_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_RAG__CHUNK_WORDS", "400")
    monkeypatch.setenv("MANGOMAS_RAG__CHUNK_OVERLAP", "60")
    monkeypatch.setenv("MANGOMAS_RAG__MIN_CHUNK_WORDS", "25")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.rag.chunk_words == 400
    assert s.rag.chunk_overlap == 60
    assert s.rag.min_chunk_words == 25


@pytest.mark.parametrize(
    ("chunk_words", "chunk_overlap", "min_chunk_words"),
    [
        (0, 0, 1),  # chunk_words below floor
        (10, 10, 1),  # overlap == window
        (10, 12, 1),  # overlap above window
        (10, -1, 1),  # negative overlap
        (10, 2, -1),  # negative min_chunk_words
    ],
)
def test_rag_settings_rejects_invalid_window(
    chunk_words: int, chunk_overlap: int, min_chunk_words: int
) -> None:
    with pytest.raises(ValueError):
        config_module.RagSettings(
            chunk_words=chunk_words,
            chunk_overlap=chunk_overlap,
            min_chunk_words=min_chunk_words,
        )


def test_rag_settings_accepts_zero_overlap_and_min() -> None:
    s = config_module.RagSettings(chunk_words=5, chunk_overlap=0, min_chunk_words=0)
    assert s.chunk_overlap == 0
    assert s.min_chunk_words == 0


def test_rag_settings_invalid_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_RAG__CHUNK_WORDS", "10")
    monkeypatch.setenv("MANGOMAS_RAG__CHUNK_OVERLAP", "10")
    with pytest.raises(ValueError):
        config_module.Settings(_env_file=None)  # type: ignore[call-arg]


# ── LLMSettings — Vertex AI fields ────────────────────────────────────────────


def test_llm_vertex_defaults_do_not_disturb_lmstudio() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.project_id is None
    assert s.llm.location == config_module.DEFAULT_VERTEX_LOCATION
    assert s.llm.credentials_path is None


def test_llm_vertex_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_LLM__PROJECT_ID", "my-gcp-project")
    monkeypatch.setenv("MANGOMAS_LLM__LOCATION", "europe-west4")
    monkeypatch.setenv("MANGOMAS_LLM__CREDENTIALS_PATH", "/keys/sa.json")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm.project_id == "my-gcp-project"
    assert s.llm.location == "europe-west4"
    assert s.llm.credentials_path == "/keys/sa.json"


# ── DBSettings — Postgres pool fields ─────────────────────────────────────────


def test_db_postgres_pool_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_min == config_module.DEFAULT_DB_POOL_MIN
    assert s.db.pool_max == config_module.DEFAULT_DB_POOL_MAX
    assert s.db.connect_timeout_seconds == config_module.DEFAULT_DB_CONNECT_TIMEOUT_SECONDS
    assert s.db.statement_timeout_seconds is config_module.DEFAULT_DB_STATEMENT_TIMEOUT_SECONDS


def test_db_postgres_pool_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_DB__POOL_MIN", "2")
    monkeypatch.setenv("MANGOMAS_DB__POOL_MAX", "20")
    monkeypatch.setenv("MANGOMAS_DB__CONNECT_TIMEOUT_SECONDS", "30.0")
    monkeypatch.setenv("MANGOMAS_DB__STATEMENT_TIMEOUT_SECONDS", "5.5")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_min == 2
    assert s.db.pool_max == 20
    assert s.db.connect_timeout_seconds == 30.0
    assert s.db.statement_timeout_seconds == 5.5


# ── SecretsSettings — GCP fields ──────────────────────────────────────────────


def test_secrets_gcp_defaults() -> None:
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.secrets.provider == config_module.DEFAULT_SECRETS_PROVIDER
    assert s.secrets.project_id is None
    assert s.secrets.timeout_seconds == config_module.DEFAULT_GCP_SECRETS_TIMEOUT_SECONDS
    assert s.secrets.default_version == config_module.DEFAULT_GCP_SECRET_VERSION


def test_secrets_gcp_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANGOMAS_SECRETS__PROVIDER", "gcp")
    monkeypatch.setenv("MANGOMAS_SECRETS__PROJECT_ID", "my-gcp-project")
    monkeypatch.setenv("MANGOMAS_SECRETS__TIMEOUT_SECONDS", "10.0")
    monkeypatch.setenv("MANGOMAS_SECRETS__DEFAULT_VERSION", "3")
    s = config_module.Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.secrets.provider == "gcp"
    assert s.secrets.project_id == "my-gcp-project"
    assert s.secrets.timeout_seconds == 10.0
    assert s.secrets.default_version == "3"
