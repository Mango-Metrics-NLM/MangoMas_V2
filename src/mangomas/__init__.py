"""Mango-Mas V2 package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

# The installed distribution whose metadata carries the project version.
# Named once here — never restate the string at a call site.
_DIST_NAME = "mangomas"

# Fallback version when the distribution is not installed (e.g. a raw source
# checkout without ``pip install -e``). PEP 440 local-version syntax makes the
# "unknown" provenance explicit rather than masquerading as a release.
DEFAULT_PACKAGE_VERSION = "0.0.0+unknown"


def package_version() -> str:
    """Resolve the version from installed package metadata.

    ``pyproject.toml`` is the single source of truth for the project version;
    deriving both ``mangomas.__version__`` and the OpenAPI ``info.version``
    from it here keeps them from drifting behind releases (each previously
    hardcoded its own copy and both had gone stale).
    """
    try:
        return _distribution_version(_DIST_NAME)
    except PackageNotFoundError:
        return DEFAULT_PACKAGE_VERSION


__version__ = package_version()
