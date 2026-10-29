"""Build-time probe for the ``mango_contracts`` envelope package.

The producer (``producer.py``) imports ``mango_contracts`` at module level and
is itself imported lazily — on the first planner/reviewer ``handle`` with a
sink attached — so flag-off dispatch never touches the package. The cost of
that laziness was that an operator who set ``MANGOMAS_SIGNAL__ENABLED=true``
without the sibling checkout installed got a raw ``ImportError`` on the first
request (a 500) rather than a refusal at build time. The composition root
calls :func:`require_contracts` when the flag is on, so the failure is a typed
``ConfigError`` where every other provider's missing extra is reported too.

Lives here rather than in ``composition/`` because only ``cognitive/`` may
import ``mango_contracts`` (``tests/cognitive/test_cognitive_layering.py``);
the import below is inside the function so this module stays flag-off safe.
"""

from __future__ import annotations

from mangomas.errors import ConfigError

_CONTRACTS_INSTALL_HINT = (
    "MANGOMAS_SIGNAL__ENABLED=true requires the mango-integration-contracts package "
    "(the CognitiveSignal 1.1.0 envelope), which is a sibling checkout rather than a "
    "PyPI dependency: pip install ./mango-integration-contracts"
)


def require_contracts() -> None:
    """Raise :class:`ConfigError` when ``mango_contracts`` is not importable."""
    try:
        import mango_contracts  # noqa: F401, PLC0415
    except ImportError as exc:
        raise ConfigError(_CONTRACTS_INSTALL_HINT) from exc
