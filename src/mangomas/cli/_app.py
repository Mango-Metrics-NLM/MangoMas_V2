"""The assembly root: builds the Typer `app` the console script resolves.

The **only** place a command becomes part of the CLI. Every registration below
is an explicit statement, and their order is the order `mangomas --help` lists
them — `TyperGroup.list_commands` emits `registered_commands` before
`registered_groups`, and preserves registration order within each. That is a
user-facing contract (`tests/test_cli_surface.py` pins it), so it must not ride
on import evaluation: `ruff`'s isort is free to permute the import block, and
if registration were a side effect of these imports it would be free to permute
the rendered listing with it.

Every command is registered with an explicit `name=`. Three of the root
commands previously took their name implicitly from `__name__`, which made
renaming a function a silent rename of a CLI command.

Mirrors `config/_root.py`: the layer that assembles, importing every leaf and
imported in turn only by the facade.
"""

from __future__ import annotations

import typer

from mangomas.cli.commands import chat as chat_commands
from mangomas.cli.commands import eval as eval_commands
from mangomas.cli.commands import rag as rag_commands
from mangomas.cli.commands import workflow as workflow_commands

app = typer.Typer(help="Mango-Mas V2 CLI", no_args_is_help=True)

# Root commands, in `--help` order.
app.command(name="agents")(chat_commands.agents)
app.command(name="chat")(chat_commands.chat)
app.command(name="history")(chat_commands.history)
app.command(name="eval")(eval_commands.eval_cmd)

# Sub-apps. Typer lists these after every root command regardless of where
# these calls sit, but keeping them last matches the rendered order.
app.add_typer(rag_commands.rag_app, name="rag")
app.add_typer(workflow_commands.workflow_app, name="workflow")
