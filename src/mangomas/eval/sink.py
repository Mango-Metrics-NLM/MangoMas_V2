"""Sink protocol — a destination for a finished :class:`EvalReport`.

Sinks decouple *producing* an evaluation report from *emitting* it (stdout,
JSON file, Langfuse, ...). The CLI builds one or more sinks from
``EvalSettings.sinks`` and emits to each under fault isolation, so one failing
sink never costs the others their output.

``emit`` is async because real sinks perform I/O (file writes, SDK calls) which
must run off the event loop via ``asyncio.to_thread`` per project convention.
The optional ``gate_result`` is the only channel for quality-gate context —
:class:`~mangomas.eval.runner.EvalReport` stays a frozen, unchanged contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.eval.gate import GateResult
    from mangomas.eval.runner import EvalReport

JSON_FILE_SINK_NAME: Final[str] = "json_file"
JSON_FILE_PATH_OPTION: Final[str] = "path"
DEFAULT_JSON_REPORT_FILENAME: Final[str] = "report.json"

SQLITE_RESULTS_SINK_NAME: Final[str] = "sqlite_results"
SQLITE_RESULTS_DB_PATH_OPTION: Final[str] = "db_path"
DEFAULT_SQLITE_RESULTS_FILENAME: Final[str] = "results.db"

# Default artifact filenames resolved under ``MANGOMAS_EVAL__OUTPUT_DIR`` when
# a file/database sink omits its explicit path option in ``sink_options``.
DEFAULT_SINK_ARTIFACTS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        JSON_FILE_SINK_NAME: (JSON_FILE_PATH_OPTION, DEFAULT_JSON_REPORT_FILENAME),
        SQLITE_RESULTS_SINK_NAME: (SQLITE_RESULTS_DB_PATH_OPTION, DEFAULT_SQLITE_RESULTS_FILENAME),
    }
)


@runtime_checkable
class Sink(Protocol):
    """A named destination for an evaluation report."""

    name: str

    async def emit(
        self,
        report: EvalReport,
        *,
        gate_result: GateResult | None = None,
    ) -> None:
        """Emit *report* (and optional *gate_result*) to this sink."""
        ...
