"""Document-parser factory, wiring and teardown (spec-0035 R10 / ADR-0036 §10).

Opt-in: with ``MANGOMAS_PARSER__ENABLED=false`` :func:`build_parser` returns
``None`` and constructs nothing — no parser object, no HTTP client. When
enabled, the builder attaches the parser to ``AgentContext.extras`` under
:data:`~mangomas.adapters.parsers.PARSER_EXTRAS_KEY`, where the operator
``rag ingest`` path picks it up, and :class:`_ParserCloseMixin` closes it from
``Orchestrator.aclose()`` on the same fault-isolated, idempotent path as every
other adapter — for every composed entry point, not only the CLI.

The ``docling_serve`` factory registers at import, like every other provider.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from mangomas.adapters.parsers import PARSER_EXTRAS_KEY, DoclingServeParser
from mangomas.composition._registries import _parser_registry
from mangomas.config import ParserSettings

if TYPE_CHECKING:  # pragma: no cover
    from mangomas.adapters.parsers import DocumentParser
    from mangomas.secrets import SecretsProvider

logger = logging.getLogger(__name__)


def _docling_serve_parser_factory(
    settings: ParserSettings, *, api_key: str | None
) -> DoclingServeParser:
    """Build a :class:`DoclingServeParser` with the already-resolved ``api_key``."""
    logger.debug(
        "Building DoclingServeParser",
        extra={"auth_mode": settings.auth_mode, "has_api_key": api_key is not None},
    )
    return DoclingServeParser(settings, api_key=api_key)


def _resolve_parser_api_key(
    settings: ParserSettings, secrets: SecretsProvider | None
) -> str | None:
    """Return the parser API key: ``secret_ref`` via *secrets* overrides ``api_key``.

    Mirrors :func:`~mangomas.composition.secrets._resolve_llm_secrets`: an unset
    ``secret_ref`` keeps the inline key, and a ref the provider cannot resolve
    (``None``) also keeps it, so local development works without a vault.
    """
    if not settings.secret_ref or secrets is None:
        return settings.api_key
    resolved = secrets.get(settings.secret_ref)
    if resolved is None:
        logger.debug(
            "parser secret_ref %r not resolved; keeping inline api_key", settings.secret_ref
        )
        return settings.api_key
    return resolved


def _parser_secrets_provider(
    settings: ParserSettings, secrets_provider_name: str
) -> SecretsProvider | None:
    """Look up the secrets provider only when an enabled parser names a ``secret_ref``."""
    if not settings.enabled or not settings.secret_ref:
        return None
    import mangomas.composition as composition_module  # noqa: PLC0415

    provider: SecretsProvider = composition_module.secrets_registry.get(secrets_provider_name)
    return provider


def build_parser(
    settings: ParserSettings, secrets: SecretsProvider | None
) -> DocumentParser | None:
    """Build the configured document parser, or ``None`` when parsing is disabled.

    Disabled → returns before touching the registry or the secrets provider, so
    nothing is constructed. Enabled → resolves the API key (``secret_ref``
    through *secrets* overrides ``api_key``) and calls the provider factory.

    Raises:
        UnknownProvider: ``settings.provider`` is not registered.
        ConfigError: the provider rejects its configuration (e.g. ``api_key``
            auth mode with no resolvable key).
    """
    if not settings.enabled:
        return None
    api_key = _resolve_parser_api_key(settings, secrets)
    parser: DocumentParser = _parser_registry.get(settings.provider)(settings, api_key=api_key)
    return parser


class _ParserCloseMixin:
    """Extends ``Orchestrator._close_hooks`` to close the attached document parser.

    The same subclass-to-extend seam as
    :class:`~mangomas.composition.llm._AgentLLMOverrideCloseMixin`: overriding
    ``_close_hooks`` from the composition layer, without editing the protected
    ``core/orchestrator/``, puts the parser on ``aclose()``'s per-hook fault
    isolation and its ``_closed`` latch (ADR-0028 precedent).
    """

    def _close_hooks(self) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
        hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = super()._close_hooks()  # type: ignore[misc]
        parser = self.context.extras.get(PARSER_EXTRAS_KEY)  # type: ignore[attr-defined]
        if parser is not None and hasattr(parser, "aclose"):
            hooks.append(("parser", parser.aclose))
        return hooks


__all__ = [
    "_ParserCloseMixin",
    "_docling_serve_parser_factory",
    "_parser_secrets_provider",
    "_resolve_parser_api_key",
    "build_parser",
]
