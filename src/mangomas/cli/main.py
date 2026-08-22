"""Typer CLI entry point.

This module is a **permanent re-export facade** (ADR-0019). `main.py` was 682
lines holding four unrelated command groups plus the process- and
orchestrator-level plumbing they share; it is now a package cut by *dependency
layer*, the same cut as `config/` and `telemetry/`:

    exit_codes            three exit codes (pure leaf, no local imports)
    _runtime              _build / _close_orchestrator / configure_cli_logging
                          / win32 stdout (base)
    commands/chat         agents, chat, history
    commands/_eval_config flag-over-settings precedence for `eval`
    commands/eval         the eval run itself      -> _eval_config
    commands/rag          the `rag` sub-app
    commands/workflow     the `workflow` sub-app
    _app                  assembly: builds `app` -> every commands/ module

Cutting by command group alone would have orphaned `_build`,
`_close_orchestrator` and the exit codes — every group needs all of them — and
forced command modules to import each other.

`pyproject.toml`'s `[project.scripts]` resolves `mangomas.cli.main:app`, so
this module stays the entry point and every name importable from it before the
split still is. The facade additionally re-exports the **private** names that
tests reach for: `_build` and `_close_orchestrator` are the orchestrator patch
seam, and `_emit_sinks` is asserted directly by `tests/eval/test_cli_eval.py`.

A facade preserves object *identity*, not module-global name *binding*. Patch
the seam on `mangomas.cli._runtime`, never here — rebinding `main._build`
leaves every command still resolving the real one.
`tests/test_import_compat.py` asserts identity for both surfaces.
"""

from __future__ import annotations

from mangomas.cli import _app as _app
from mangomas.cli import _runtime as _runtime
from mangomas.cli import commands as commands
from mangomas.cli import exit_codes as exit_codes
from mangomas.cli._app import app as app
from mangomas.cli._runtime import VERBOSE_LOG_LEVEL as VERBOSE_LOG_LEVEL
from mangomas.cli._runtime import _build as _build
from mangomas.cli._runtime import _close_orchestrator as _close_orchestrator
from mangomas.cli._runtime import configure_cli_logging as configure_cli_logging
from mangomas.cli.commands._eval_config import _build_dataset_source as _build_dataset_source
from mangomas.cli.commands._eval_config import _build_sinks as _build_sinks
from mangomas.cli.commands._eval_config import _build_target as _build_target
from mangomas.cli.commands._eval_config import _resolve_gating as _resolve_gating
from mangomas.cli.commands._eval_config import _resolve_regression as _resolve_regression
from mangomas.cli.commands.chat import agents as agents
from mangomas.cli.commands.chat import chat as chat
from mangomas.cli.commands.chat import history as history
from mangomas.cli.commands.eval import _emit_sinks as _emit_sinks
from mangomas.cli.commands.eval import _evaluate_run_gates as _evaluate_run_gates
from mangomas.cli.commands.eval import _finish_eval as _finish_eval
from mangomas.cli.commands.eval import eval_cmd as eval_cmd
from mangomas.cli.commands.eval import logger as logger
from mangomas.cli.commands.rag import _require_rag as _require_rag
from mangomas.cli.commands.rag import rag_app as rag_app
from mangomas.cli.commands.rag import rag_ingest as rag_ingest
from mangomas.cli.commands.rag import rag_query as rag_query
from mangomas.cli.commands.workflow import _load_workflow_or_exit as _load_workflow_or_exit
from mangomas.cli.commands.workflow import _resolve_workflow_source as _resolve_workflow_source
from mangomas.cli.commands.workflow import workflow_app as workflow_app
from mangomas.cli.commands.workflow import workflow_run as workflow_run
from mangomas.cli.commands.workflow import workflow_validate as workflow_validate
from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE as EVAL_GATE_EXIT_CODE
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR as EXIT_CONFIG_ERROR
from mangomas.cli.exit_codes import EXIT_RUNTIME_ERROR as EXIT_RUNTIME_ERROR

__all__ = [
    "EVAL_GATE_EXIT_CODE",
    "EXIT_CONFIG_ERROR",
    "EXIT_RUNTIME_ERROR",
    "agents",
    "app",
    "chat",
    "eval_cmd",
    "history",
    "rag_app",
    "rag_ingest",
    "rag_query",
    "workflow_app",
    "workflow_run",
    "workflow_validate",
]


if __name__ == "__main__":  # pragma: no cover
    app()
