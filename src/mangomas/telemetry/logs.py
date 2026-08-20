"""Logging ingredients: the JSON formatter and the trace-context filter.

A pure leaf — stdlib `logging` plus a read-only `trace.get_current_span()`,
no mutable state and no imports from elsewhere in the package, so it can be
imported on its own.

Named `logs` rather than `logging` so that a sibling doing `import logging`
is never a double-take, even though absolute imports make the shadowing
technically harmless.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opentelemetry import trace

_STANDARD_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter (GCP Cloud Logging compatible).

    Enabled when ``Settings.log.format == "json"``.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        log_dict: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.message,
        }
        if record.exc_info:
            log_dict["exception"] = self.formatException(record.exc_info)
        # Forward extra fields added via ``logger.info(..., extra={...})``.
        log_dict.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_ATTRS
            }
        )
        return json.dumps(log_dict, ensure_ascii=False, default=str)


class TraceContextFilter(logging.Filter):
    """Logging filter that injects the current OTel trace/span IDs into log records.

    Install once via ``logging.getLogger().addFilter(TraceContextFilter())``.
    When no active span exists the fields are set to empty strings.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        span_ctx = span.get_span_context()
        if span_ctx.is_valid:
            record.trace_id = format(span_ctx.trace_id, "032x")
            record.span_id = format(span_ctx.span_id, "016x")
        else:
            record.trace_id = ""
            record.span_id = ""
        return True
