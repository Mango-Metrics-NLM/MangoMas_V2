"""Shared reader for the repository ``Dockerfile`` — the sibling of ``_makefile.py``.

Two contracts read the image definition: the build-context suite (every
``COPY`` survives ``.dockerignore``; the runtime install is lockfile-constrained)
and the provider contract (the extras the image installs cover the providers
the manifest selects). Both need the default of a ``ARG NAME=value``
instruction, so the parser lives here once.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def text() -> str:
    """The Dockerfile's full text."""
    return DOCKERFILE.read_text(encoding="utf-8")


def build_arg_default(name: str, *, source: str | None = None) -> str:
    """Return the default of ``ARG <name>=<default>``; ``""`` for ``ARG <name>=``.

    An ``ARG <name>`` with no ``=`` (no default) is reported as a missing
    declaration: a build arg the runtime install expands but nobody sets is
    the empty string, so the distinction matters for the extras contract.
    *source* is injectable so mutation proofs can run against an edited copy.
    """
    body = text() if source is None else source
    match = re.search(rf"^\s*ARG\s+{re.escape(name)}=(?P<default>\S*)\s*$", body, re.MULTILINE)
    assert match is not None, f"Dockerfile does not declare `ARG {name}=<default>`"
    return match.group("default").strip("\"'")
