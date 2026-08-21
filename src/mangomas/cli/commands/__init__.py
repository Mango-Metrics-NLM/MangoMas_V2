"""Command modules for the `mangomas` CLI.

Deliberately empty of imports. Every module here defines commands but registers
nothing: assembly is `mangomas.cli._app`, which is the single place the root
`--help` listing order is decided. An import here would make that order depend
on import evaluation, which `ruff`'s isort is free to permute.
"""

from __future__ import annotations
