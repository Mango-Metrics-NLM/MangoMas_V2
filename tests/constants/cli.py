"""CLI exit codes and the public command-tree contract."""

from __future__ import annotations

from mangomas.cli.exit_codes import EVAL_GATE_EXIT_CODE, EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR

# ── CLI public surface (tests/test_cli_surface.py) ────────────────────────────
# `mangomas` is a console script (`pyproject.toml` -> `mangomas.cli.main:app`),
# so its command tree and flags are a user-facing contract. Recorded from the
# live app and then reviewed — editing these tuples is the review record for a
# surface change, exactly like EXPECTED_AGENT_SLUGS is for the corpus.
EXPECTED_CLI_ROOT_COMMANDS: tuple[str, ...] = (
    "agents",
    "chat",
    "eval",
    "history",
    "rag",
    "workflow",
)
EXPECTED_CLI_COMMANDS: tuple[str, ...] = (
    "agents",
    "chat",
    "eval",
    "history",
    "rag",
    "rag ingest",
    "rag query",
    "workflow",
    "workflow run",
    "workflow validate",
)
# Long-form options plus positional arguments, per command. Short aliases (-a,
# -v) are deliberately excluded: they are conveniences, and pinning them would
# make the set churn without protecting anything a script depends on.
EXPECTED_CLI_PARAMS: dict[str, tuple[str, ...]] = {
    "agents": (),
    "chat": ("--agent", "--system", "--verbose", "<message>"),
    "eval": (
        "--agent",
        "--allow-new-failures",
        "--baseline",
        "--dataset",
        "--dataset-source",
        "--fail-fast",
        "--fail-on-error",
        "--gate",
        "--max-mean-score-drop",
        "--max-pass-rate-drop",
        "--min-mean-score",
        "--min-pass-rate",
        "--no-allow-new-failures",
        "--no-fail-fast",
        "--no-fail-on-error",
        "--no-gate",
        "--output-json",
        "--parallelism",
        "--scorer",
        "--target",
        "--verbose",
    ),
    "history": ("--limit", "--verbose"),
    "rag": (),
    "rag ingest": ("--verbose", "<path>"),
    "rag query": ("--top-k", "--verbose", "<text>"),
    "workflow": (),
    "workflow run": ("--definition", "--verbose", "<message>"),
    "workflow validate": ("--definition", "--verbose"),
}

# `--help` listing order, per group. Registration order, NOT alphabetical:
# the root is agents/chat/history/eval/rag/workflow and `workflow` is
# validate/run. Typer emits registered_commands before registered_groups, so
# sub-apps always follow root commands; the order within each bucket is a
# deliberate choice and a user-visible surface.
EXPECTED_CLI_HELP_ORDER: dict[str, tuple[str, ...]] = {
    "<root>": ("agents", "chat", "history", "eval", "rag", "workflow"),
    "rag": ("ingest", "query"),
    "workflow": ("validate", "run"),
}

__all__ = [
    "EVAL_GATE_EXIT_CODE",
    "EXIT_CONFIG_ERROR",
    "EXIT_RUNTIME_ERROR",
    "EXPECTED_CLI_COMMANDS",
    "EXPECTED_CLI_HELP_ORDER",
    "EXPECTED_CLI_PARAMS",
    "EXPECTED_CLI_ROOT_COMMANDS",
]
