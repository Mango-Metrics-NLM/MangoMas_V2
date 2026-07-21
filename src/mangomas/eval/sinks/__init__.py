"""Built-in eval sinks.

Importing this package registers every built-in sink in
:data:`~mangomas.eval.sink_registry.sink_registry`. External plugins follow the
same pattern (and may be auto-discovered via :mod:`mangomas.eval.discovery`).
"""

from __future__ import annotations

from mangomas.eval.sinks.console import ConsoleSink
from mangomas.eval.sinks.json_file import JsonFileSink
from mangomas.eval.sinks.langfuse import LangfuseSink
from mangomas.eval.sinks.sqlite_results import SqliteResultsSink
from mangomas.eval.sinks.webhook import WebhookSink

__all__ = [
    "ConsoleSink",
    "JsonFileSink",
    "LangfuseSink",
    "SqliteResultsSink",
    "WebhookSink",
]
