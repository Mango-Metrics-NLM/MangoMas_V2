"""Shared reader for the Cloud Run base manifest — sibling of ``_workflows.py``/``_makefile.py``.

Three suites (``test_deploy_contract.py``, ``test_deploy_environments.py`` and
``tests/test_deploy_environment.py``) load ``deploy/service.yaml``, reach its
single container's env list, and compare rendered manifests against it. Each
had its own copy of those few lines; one copy here keeps their notion of "the
base manifest" identical.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import yaml

SERVICE_YAML = Path(__file__).resolve().parents[2] / "deploy" / "service.yaml"


def base_manifest(path: Path = SERVICE_YAML) -> dict[str, Any]:
    """A fresh parse of the base manifest (callers may mutate it freely)."""
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def container(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest's single container."""
    containers = manifest["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "expected exactly one container"
    return cast("dict[str, Any]", containers[0])


def env_vars(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The container's env entries keyed by name."""
    return {entry["name"]: entry for entry in container(manifest).get("env") or []}


def diff_paths(a: Any, b: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    """Yield every leaf path (dict keys / list indices) where *a* and *b* differ.

    Lists of different lengths are reported at the list itself rather than
    element by element, so an appended env var shows up as its list's path.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a.keys() | b.keys():
            yield from diff_paths(a.get(key), b.get(key), (*path, key))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            yield from diff_paths(x, y, (*path, index))
    elif a != b:
        yield path
