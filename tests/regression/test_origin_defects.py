"""AQA regression suite for the origin-defect fixes."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tests.mango_contracts.constants import TTL_ONE_DAY, envelope_base

from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from mangomas.errors import SecretsResolutionError
from mangomas.rag.loader import RawDoc, load_documents
from mangomas.secrets.gcp import GCPSecretManagerProvider

_REPO_ROOT = Path(__file__).resolve().parents[2]

async def test_rag_loader_emits_canonical_posix_sources(tmp_path: Path) -> None:
    """Document sources stay canonical via ``Path.as_posix()``."""
    nested = tmp_path / "nested" / "doc.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("sample", encoding="utf-8")

    docs = await load_documents(str(tmp_path))

    assert docs == [RawDoc(source="nested/doc.md", text="sample")]


@pytest.mark.parametrize(
    ("exc_name", "expect_raise_in_strict"),
    [
        ("NotFound", False),
        ("PermissionDenied", True),
        ("Unauthenticated", True),
        ("DeadlineExceeded", True),
        ("GoogleAPIError", True),
    ],
)
def test_gcp_secrets_dynamic_exception_resolution(
    monkeypatch: pytest.MonkeyPatch,
    exc_name: str,
    expect_raise_in_strict: bool,
) -> None:
    """GCPSecretManagerProvider binds to synthetic exceptions in sys.modules.

    Even when a real or mock google.api_core is imported, the provider
    retrieves the active exceptions from sys.modules to honor test stand-ins,
    supporting NotFound, PermissionDenied, Unauthenticated, DeadlineExceeded, and GoogleAPIError.
    """

    class _CustomNotFound(Exception):
        pass

    class _CustomPermissionDenied(Exception):
        pass

    class _CustomUnauthenticated(Exception):
        pass

    class _CustomDeadlineExceeded(Exception):
        pass

    class _CustomGoogleAPIError(Exception):
        pass

    class _CustomDefaultCredentialsError(Exception):
        pass

    mock_gax = types.ModuleType("google.api_core.exceptions")
    mock_gax.NotFound = _CustomNotFound  # type: ignore[attr-defined]
    mock_gax.PermissionDenied = _CustomPermissionDenied  # type: ignore[attr-defined]
    mock_gax.Unauthenticated = _CustomUnauthenticated  # type: ignore[attr-defined]
    mock_gax.DeadlineExceeded = _CustomDeadlineExceeded  # type: ignore[attr-defined]
    mock_gax.GoogleAPIError = _CustomGoogleAPIError  # type: ignore[attr-defined]
    mock_gauth_exc = types.ModuleType("google.auth.exceptions")
    mock_gauth_exc.DefaultCredentialsError = _CustomDefaultCredentialsError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", mock_gax)
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", mock_gauth_exc)

    exc_class = getattr(mock_gax, exc_name)
    mock_client = MagicMock()
    mock_client.access_secret_version.side_effect = exc_class("test exception")

    # In strict=False mode, all failures return None gracefully
    lenient_provider = GCPSecretManagerProvider(
        project_id="test-proj",
        client=mock_client,
        strict=False,
        timeout_seconds=5.0,
        default_version="latest",
    )
    assert lenient_provider.get("test-secret") is None

    # In strict=True mode, NotFound still returns None, others raise SecretsResolutionError
    strict_provider = GCPSecretManagerProvider(
        project_id="test-proj",
        client=mock_client,
        strict=True,
        timeout_seconds=5.0,
        default_version="latest",
    )
    if expect_raise_in_strict:
        with pytest.raises(SecretsResolutionError) as exc_info:
            strict_provider.get("test-secret")
        assert exc_info.value.ref == "test-secret"
        assert exc_info.value.provider == "gcp"
    else:
        assert strict_provider.get("test-secret") is None


def test_contracts_envelope_timestamps_are_dynamically_valid() -> None:
    """envelope_base generates timestamps in the future, avoiding stale hardcoded dates."""
    env = envelope_base()
    now = datetime.now(UTC)

    created_at = datetime.fromisoformat(env["created_at"])
    expires_at = datetime.fromisoformat(env["expires_at"])

    # Created time must be recent (within last 60 seconds)
    assert abs((now - created_at).total_seconds()) < 60

    # Expiry must be in the future, approximately TTL_ONE_DAY ahead
    assert expires_at > now
    diff = (expires_at - created_at).total_seconds()
    assert abs(diff - TTL_ONE_DAY) < 5


async def test_sentence_transformer_encodes_without_progress_bar() -> None:
    """SentenceTransformersEmbeddingClient disables show_progress_bar for clean stdout."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3]]

    client = SentenceTransformersEmbeddingClient(model="dummy-model", client=mock_model)
    vectors = await client.embed_batch(["test text"])

    assert vectors == [[0.1, 0.2, 0.3]]
    mock_model.encode.assert_called_once_with(["test text"], show_progress_bar=False)


async def test_sentence_transformer_injected_fake_accepts_progress_bar_kwarg() -> None:
    """Injected fakes can match the supported encode API."""

    class _FakeModel:
        def encode(
            self, texts: list[str], *, show_progress_bar: bool = False
        ) -> list[list[float]]:
            assert show_progress_bar is False
            return [[float(len(texts[0])), 0.5, 0.6]]

    client = SentenceTransformersEmbeddingClient(model="dummy-model", client=_FakeModel())
    vectors = await client.embed_batch(["fallback text"])

    assert vectors == [[13.0, 0.5, 0.6]]


def test_sitecustomize_protects_pytest_environment() -> None:
    """A clean child process auto-loads ``sitecustomize.py`` from the repo root."""
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('PYTEST_ADDOPTS', ''))"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "-p no:randomly" in result.stdout.strip()
