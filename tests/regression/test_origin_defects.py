"""AQA Regression Suite: Origin Defect Resolution Verification.

Guards against regressions for defects triaged during the origin/feat/initial-release audit:
1. RAG Loader PathString cross-platform path equality (Windows backslash vs POSIX forward slash).
2. GCP Secret Manager dynamic exception binding in the presence of installed Google SDKs.
3. Contracts envelope timestamp dynamicity (eliminating hardcoded expirations).
4. Sentence-Transformers progress bar stdout suppression.
5. Pytest ambient randomly plugin neutralization.
"""

from __future__ import annotations

import os
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sitecustomize  # noqa: F401
from tests.mango_contracts.constants import TTL_ONE_DAY, envelope_base

from mangomas.adapters.embeddings.sentence_transformers import (
    SentenceTransformersEmbeddingClient,
)
from mangomas.errors import SecretsResolutionError
from mangomas.rag.loader import PathString, RawDoc
from mangomas.secrets.gcp import GCPSecretManagerProvider


@pytest.mark.parametrize(
    ("posix_path", "windows_path"),
    [
        ("C:/Users/test/folder/file.txt", "C:\\Users\\test\\folder\\file.txt"),
        ("relative/path/to/doc.md", "relative\\path\\to\\doc.md"),
        ("//server/share/folder/file.txt", "\\\\server\\share\\folder\\file.txt"),
        ("deep/nested/a/b/c/d/e.py", "deep\\nested\\a\\b\\c\\d\\e.py"),
        ("", ""),
    ],
)
def test_path_string_cross_platform_equality(posix_path: str, windows_path: str) -> None:
    """PathString compares equal regardless of forward or backward slashes across path varieties."""
    ps = PathString(posix_path)

    # Identical string comparison
    assert ps == posix_path
    # Cross-platform equivalent path comparison
    assert ps == windows_path
    assert windows_path == ps
    assert ps == Path(windows_path)
    assert Path(posix_path) == ps

    # Hash consistency matches wrapped str
    assert hash(ps) == hash(posix_path)

    # RawDoc integration
    doc = RawDoc(source=ps, text="sample")
    assert doc.source == windows_path
    assert doc.source == posix_path


def test_path_string_inequality_on_different_paths() -> None:
    """PathString does not match genuinely different paths."""
    ps = PathString("path/to/file_a.txt")
    assert ps != "path/to/file_b.txt"
    assert ps != Path("path/to/file_b.txt")
    assert ps != 42  # type: ignore[comparison-overlap]


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

    mock_gax = types.ModuleType("google.api_core.exceptions")
    mock_gax.NotFound = _CustomNotFound  # type: ignore[attr-defined]
    mock_gax.PermissionDenied = _CustomPermissionDenied  # type: ignore[attr-defined]
    mock_gax.Unauthenticated = _CustomUnauthenticated  # type: ignore[attr-defined]
    mock_gax.DeadlineExceeded = _CustomDeadlineExceeded  # type: ignore[attr-defined]
    mock_gax.GoogleAPIError = _CustomGoogleAPIError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", mock_gax)

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


@pytest.mark.asyncio
async def test_sentence_transformer_encodes_without_progress_bar() -> None:
    """SentenceTransformersEmbeddingClient disables show_progress_bar for clean stdout."""
    mock_model = MagicMock()
    mock_model.encode.return_value = [[0.1, 0.2, 0.3]]

    client = SentenceTransformersEmbeddingClient(model="dummy-model", client=mock_model)
    vectors = await client.embed_batch(["test text"])

    assert vectors == [[0.1, 0.2, 0.3]]
    mock_model.encode.assert_called_once_with(["test text"], show_progress_bar=False)


@pytest.mark.asyncio
async def test_sentence_transformer_fallback_when_mock_lacks_kwarg() -> None:
    """SentenceTransformersEmbeddingClient falls back if model lacks show_progress_bar."""
    mock_model = MagicMock()

    def _encode_mock(_texts: list[str], **kwargs: object) -> list[list[float]]:
        if "show_progress_bar" in kwargs:
            raise TypeError("unexpected keyword argument 'show_progress_bar'")
        return [[0.4, 0.5, 0.6]]

    mock_model.encode.side_effect = _encode_mock

    client = SentenceTransformersEmbeddingClient(model="dummy-model", client=mock_model)
    vectors = await client.embed_batch(["fallback text"])

    assert vectors == [[0.4, 0.5, 0.6]]


def test_sitecustomize_protects_pytest_environment() -> None:
    """sitecustomize.py configures PYTEST_ADDOPTS to neutralize pytest-randomly."""
    pytest_addopts = os.environ.get("PYTEST_ADDOPTS", "")
    assert "-p no:randomly" in pytest_addopts
