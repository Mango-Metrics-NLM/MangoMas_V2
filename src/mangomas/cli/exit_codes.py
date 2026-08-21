"""CLI exit codes.

A pure-constant leaf with no local imports, so every command module can depend
on it without creating a cycle.

The gate code is distinct from the other two so CI can react specifically to a
quality regression; all three are named because the comment that defined their
semantics used to sit above a single constant while the other two were repeated
as bare literals at nine call sites.
"""

from __future__ import annotations

from typing import Final

EXIT_RUNTIME_ERROR: Final[int] = 1
EXIT_CONFIG_ERROR: Final[int] = 2
EVAL_GATE_EXIT_CODE: Final[int] = 3
