"""Shared logging configuration helpers for repository scripts (stdlib-only).

Centralizes repeated ``logging.basicConfig`` formatting and Claude Code hook
telemetry fallback logic across ``scripts/`` while preserving stdlib-only
import safety (optional ``mangomas.telemetry`` / ``mangomas.config`` imports
are deferred inside function scope).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, Final, TextIO

DEFAULT_LOG_FORMAT: Final[str] = "%(levelname)s %(name)s: %(message)s"


def configure_script_logging(
    *,
    level: int | str = logging.INFO,
    fmt: str = DEFAULT_LOG_FORMAT,
    force: bool = False,
    only_if_unconfigured: bool = False,
    module_logger: logging.Logger | None = None,
    stream: TextIO | None = None,
) -> None:
    """Configure root or module logging consistently across repository scripts.

    When *module_logger* is supplied, configures that logger directly and attaches
    a ``StreamHandler`` only when neither *module_logger* nor the root logger
    has handlers yet (preventing duplicate lines or clobbering pytest ``caplog``).
    Otherwise delegates to :func:`logging.basicConfig`.
    """
    if module_logger is not None:
        module_logger.setLevel(level)
        if not module_logger.handlers and not logging.getLogger().handlers:
            handler = logging.StreamHandler(sys.stderr if stream is None else stream)
            handler.setFormatter(logging.Formatter(fmt))
            module_logger.addHandler(handler)
        return

    if only_if_unconfigured and logging.getLogger().handlers:
        return
    logging.basicConfig(level=level, format=fmt, force=force)


def configure_harness_telemetry_logging(
    log: logging.Logger,
    *,
    cfg: Any | None = None,
    available: bool = True,
    fallback_message: str = "Structured telemetry setup failed; falling back to basicConfig",
    include_exc_info: bool = True,
    extra_on_error: bool = False,
    get_settings_fn: Callable[[], Any] | None = None,
    configure_telemetry_fn: Callable[..., Any] | None = None,
) -> None:
    """Configure structured telemetry logging for Claude Code hooks with safe fallback.

    Hook entry points must never abort a session due to missing dev extras or
    malformed settings; any exception degrades cleanly to ``basicConfig(INFO)``.
    """
    try:
        if not available:
            raise ImportError("mangomas is not installed — telemetry unavailable")
        if configure_telemetry_fn is None:
            from mangomas.telemetry import configure_telemetry as _configure_telemetry  # noqa: PLC0415

            configure_telemetry_fn = _configure_telemetry
        resolved_cfg = cfg
        if resolved_cfg is None:
            if get_settings_fn is None:
                from mangomas.config import get_settings as _get_settings  # noqa: PLC0415

                get_settings_fn = _get_settings
            resolved_cfg = get_settings_fn()
        configure_telemetry_fn(
            service_name=resolved_cfg.harness.metrics_namespace,
            log_level=resolved_cfg.harness.hook_log_level,
            log_format=resolved_cfg.log.format,
            exporter=resolved_cfg.telemetry.exporter,
        )
    except Exception as exc:
        logging.basicConfig(level=logging.INFO)
        if extra_on_error:
            log.warning(
                fallback_message,
                exc_info=include_exc_info,
                extra={"error": str(exc)},
            )
        else:
            log.warning(fallback_message, exc_info=include_exc_info)
