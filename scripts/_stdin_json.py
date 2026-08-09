"""Shared stdlib-only stdin-JSON reader for Claude Code hook scripts.

``scripts/lint_agent_frontmatter.py``'s ``--hook`` modes and
``scripts/harness_config_audit.py``'s ``ConfigChange`` hook both read
Claude Code's hook payload as JSON on stdin and must degrade to "nothing
to report" rather than raising on empty/malformed input — a hook must
never crash a session over its own plumbing.
"""

from __future__ import annotations

import json
import logging
from typing import IO


def read_json_payload(
    stream: IO[str], *, logger: logging.Logger | None = None
) -> dict[str, object]:
    """Return *stream*'s JSON payload as a ``dict``, or ``{}`` on any parse failure.

    Pass *logger* to emit a warning when non-empty stdin fails to parse as
    JSON, or parses to something other than a JSON object; omit it for a
    fully silent degrade.
    """
    raw = stream.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        if logger is not None:
            logger.warning("Hook received malformed JSON on stdin; ignoring")
        return {}
    if not isinstance(payload, dict):
        if logger is not None:
            logger.warning(
                "Hook received valid JSON that is not an object on stdin; ignoring",
                extra={"payload_type": type(payload).__name__},
            )
        return {}
    return payload
