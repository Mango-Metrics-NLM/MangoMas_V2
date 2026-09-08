"""Wire the cognitive sink onto AgentContext.extras when enabled."""

from __future__ import annotations

from typing import Any

from mangomas.config.signal import SignalSettings


def _attach_cognitive_extras(extras: dict[str, Any], settings: SignalSettings) -> None:
    """Attach sink + settings when enabled; no-op (and no contracts import) otherwise."""
    if not settings.enabled:
        return
    from mangomas.cognitive.constants import (  # noqa: PLC0415
        COGNITIVE_SETTINGS_EXTRAS_KEY,
        COGNITIVE_SINK_EXTRAS_KEY,
    )
    from mangomas.cognitive.sink import build_sink  # noqa: PLC0415

    extras[COGNITIVE_SINK_EXTRAS_KEY] = build_sink(settings)
    extras[COGNITIVE_SETTINGS_EXTRAS_KEY] = settings
