"""In-process embedding adapter backed by ``sentence-transformers``.

The heavy ``sentence_transformers`` import is deferred to ``__init__`` (guarded
with ``# noqa: PLC0415``) so this module is always importable without the
optional ``embeddings-local`` extra. A pre-built ``model`` may be injected for
tests, in which case no import is performed. Encoding is synchronous CPU/GPU
work, so it runs inside ``asyncio.to_thread`` per the project's async-I/O rule.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mangomas.adapters.embeddings._shared import NoTransportAcloseMixin, SingleTextEmbedMixin

logger = logging.getLogger(__name__)

_SDK_INSTALL_HINT = (
    "sentence-transformers is not installed. Install the optional extra: "
    "pip install 'mangomas[embeddings-local]'"
)


def _lazy_load_model(model_name: str, device: str | None = None) -> Any:  # pragma: no cover
    """Load a ``SentenceTransformer`` lazily, optionally pinned to *device*.

    Excluded from coverage because the success path requires the optional
    ``embeddings-local`` extra; the injected-model path (used by unit tests)
    bypasses this helper entirely, and the device-forwarding tests replace this
    function with a recorder.

    *device* is omitted from the call entirely when ``None`` rather than passed
    as ``device=None``. The two are equivalent in current
    ``sentence_transformers`` releases, but omitting it keeps the default path
    byte-identical to the pre-setting behaviour under any release.
    """
    try:
        # Lazy: heavy optional dependency; importing at module load would force
        # it on every user, defeating the optional-extra contract.
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(_SDK_INSTALL_HINT) from exc
    if device is None:
        return SentenceTransformer(model_name)
    return SentenceTransformer(model_name, device=device)


class SentenceTransformersEmbeddingClient(SingleTextEmbedMixin, NoTransportAcloseMixin):
    """Embedding client using an in-process ``SentenceTransformer`` model.

    *device* (spec-0029 R5) pins the torch device; ``None`` leaves the
    library's auto-detect untouched. Encoding runs off the event loop either
    way, so the device choice never changes the async contract.
    """

    def __init__(self, model: str, *, client: Any | None = None, device: str | None = None) -> None:
        self._model_name = model
        self._device = device
        self._model = client if client is not None else _lazy_load_model(model, device)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode ``texts`` off the event loop and return plain ``list[float]`` vectors."""
        return await asyncio.to_thread(self._encode, texts)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        result = self._model.encode(texts)
        return [[float(x) for x in row] for row in result]
