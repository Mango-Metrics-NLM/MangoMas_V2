"""UTF-8 stdio for scripts that print characters cp1252 cannot encode."""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Reconfigure stdout and stderr to UTF-8 on Windows.

    The console default (cp1252) raises ``UnicodeEncodeError`` on the ellipsis,
    arrow, and parallel marks these scripts print. Other platforms are left
    alone. A stream without ``reconfigure`` (a captured buffer, a closed
    handle) is skipped rather than failing the script.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(encoding="utf-8", errors="replace")
