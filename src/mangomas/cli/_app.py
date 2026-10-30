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

import logging
import re
from typing import Any

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError
from typer.core import TyperGroup

from mangomas.cli.commands import chat as chat_commands
from mangomas.cli.commands import eval as eval_commands
from mangomas.cli.commands import rag as rag_commands
from mangomas.cli.commands import workflow as workflow_commands
from mangomas.cli.exit_codes import EXIT_CONFIG_ERROR
from mangomas.config import Settings

logger = logging.getLogger(__name__)

# pydantic-settings names the field inside quotes; the rest of the message
# carries the raw JSON the operator typed. Keep the name, drop the value.
_SETTINGS_FIELD_RE = re.compile(r'field "([A-Za-z0-9_]+)"')
_REDACTED_LITERAL = "'<redacted>'"


def _settings_env_prefix() -> str:
    return str(Settings.model_config.get("env_prefix") or "")


def format_settings_error(exc: ValidationError) -> str:
    """Render a settings ``ValidationError`` as env-var names plus reasons.

    The offending *value* is never included (``include_input=False``): the
    field may hold a credential, and pydantic's default rendering echoes it.
    Single-quoted literals inside a custom validator message are scrubbed
    the same way — ``LLMSettings`` names the rejected model id in quotes,
    and that id can be a secret an operator pasted into the wrong field.
    An empty ``loc`` (a model-level error) must not print ``MANGOMAS_:``.
    """
    prefix = _settings_env_prefix()
    delimiter = str(Settings.model_config.get("env_nested_delimiter") or "__")
    lines = ["Configuration error: invalid setting(s)"]
    for error in exc.errors(include_input=False, include_url=False):
        loc = error["loc"]
        if loc:
            env_var = prefix + delimiter.join(str(part) for part in loc).upper()
        else:
            env_var = prefix.rstrip("_") or "MANGOMAS"
        message = re.sub(r"'[^']*'", _REDACTED_LITERAL, str(error["msg"]))
        lines.append(f"  {env_var}: {message}")
    return "\n".join(lines)


def _format_settings_source_error(exc: SettingsError) -> str:
    """Name the field of a source-parse error without echoing its JSON value."""
    match = _SETTINGS_FIELD_RE.search(str(exc))
    field = match.group(1) if match is not None else "setting"
    return f"  {_settings_env_prefix()}{field.upper()}: invalid JSON value"


def _exit_for_settings_error(message: str) -> None:
    """Log a structured config failure and leave with the config exit code."""
    logger.error("CLI settings validation failed", extra={"event": "cli_settings_error"})
    typer.echo(message, err=True)
    raise typer.Exit(code=EXIT_CONFIG_ERROR) from None


class SettingsErrorGroup(TyperGroup):
    """Root group that maps an invalid ``MANGOMAS_*`` value to ``EXIT_CONFIG_ERROR``.

    Every command reads settings first (``configure_cli_logging``), and
    ``get_settings()`` raises pydantic's ``ValidationError`` rather than a
    ``MangomasError``, so ``build_or_exit``'s mapping never saw it: each command
    died with a traceback and exit 1 (spec-0039 D-6). Only an error raised for
    :class:`Settings` itself is a configuration error; any other model's
    validation failure is a genuine crash and still propagates.
    """

    # `Any`: the supertype takes Typer's vendored Click `Context`, which is only
    # importable from the private `typer._click`.
    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except ValidationError as exc:
            if exc.title != Settings.__name__:
                raise
            _exit_for_settings_error(format_settings_error(exc))
        except SettingsError as exc:
            # pydantic-settings raises SettingsError (a ValueError subclass, not
            # ValidationError) when parsing a complex JSON env var fails before
            # model validation. Never echo str(exc): its JSONDecodeError carries
            # the raw env value. Name the field so the operator knows which
            # variable to fix.
            header = "Configuration error: invalid setting(s)"
            _exit_for_settings_error(f"{header}\n{_format_settings_source_error(exc)}")


app = typer.Typer(help="Mango-Mas V2 CLI", no_args_is_help=True, cls=SettingsErrorGroup)

# Root commands, in `--help` order.
app.command(name="agents")(chat_commands.agents)
app.command(name="chat")(chat_commands.chat)
app.command(name="history")(chat_commands.history)
app.command(name="eval")(eval_commands.eval_cmd)

# Sub-apps. Typer lists these after every root command regardless of where
# these calls sit, but keeping them last matches the rendered order.
app.add_typer(rag_commands.rag_app, name="rag")
app.add_typer(workflow_commands.workflow_app, name="workflow")
