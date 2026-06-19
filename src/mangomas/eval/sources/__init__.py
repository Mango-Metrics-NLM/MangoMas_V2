"""Built-in eval dataset sources.

Importing this package registers every built-in source in
:data:`~mangomas.eval.dataset_source.dataset_source_registry`. External plugins
follow the same pattern (and may be auto-discovered via
:mod:`mangomas.eval.discovery`).
"""

from __future__ import annotations

from mangomas.eval.sources.inline import InlineSource
from mangomas.eval.sources.jsonl import JsonlSource
from mangomas.eval.sources.langfuse import LangfuseDatasetSource

__all__ = ["InlineSource", "JsonlSource", "LangfuseDatasetSource"]
