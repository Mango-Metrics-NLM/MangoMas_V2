"""Manifest rendering, placeholder substitution, and offline verification (ADR-0036)."""

from __future__ import annotations

import copy
import logging
import os
import string
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from _deploy_registry import (
    VALIDATE_IMAGE_SENTINEL,
    VALIDATE_PLACEHOLDER_SENTINEL,
    DeployConfigError,
    Environment,
    Overrides,
    Registry,
    load_yaml,
    placeholder_names,
    require_mapping,
    require_str,
)

logger = logging.getLogger("deploy_environment")


def single_container(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the single container mapping from a Cloud Run service *manifest*."""
    try:
        containers = manifest["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError) as exc:
        raise DeployConfigError("manifest has no spec.template.spec.containers") from exc
    if not isinstance(containers, list) or len(containers) != 1:
        raise DeployConfigError("manifest must declare exactly one container")
    container = require_mapping(containers[0], "container")
    env = container.get("env")
    if env is None:
        container["env"] = env = []
    if not isinstance(env, list):
        raise DeployConfigError(f"container env must be a list, got {type(env).__name__}")
    names = [require_mapping(entry, "container env entry").get("name") for entry in env]
    duplicates = sorted({str(n) for n in names if names.count(n) > 1})
    if duplicates:
        # An override would only change the first copy and leave the other live.
        raise DeployConfigError(f"container env declares {duplicates} more than once")
    return container


def _env_entry(container: dict[str, Any], name: str) -> dict[str, Any] | None:
    # single_container has already proved env is a list of unique mappings.
    for entry in container["env"]:
        if entry.get("name") == name:
            return entry  # type: ignore[no-any-return]
    return None


def _apply_env(container: dict[str, Any], values: Mapping[str, str], env_name: str) -> None:
    for name, value in values.items():
        entry = _env_entry(container, name)
        if entry is None:
            container["env"].append({"name": name, "value": value})
            logger.debug("[%s] env %s added = %r", env_name, name, value)
        elif "valueFrom" in entry:
            raise DeployConfigError(
                f"[{env_name}] env {name} is a secretKeyRef in the base; override it under "
                "'secrets', never as a plain value"
            )
        else:
            logger.debug("[%s] env %s: %r -> %r", env_name, name, entry.get("value"), value)
            entry["value"] = value


def _apply_secrets(container: dict[str, Any], secrets: Mapping[str, str], env_name: str) -> None:
    for name, secret in secrets.items():
        entry = _env_entry(container, name)
        value_from = None if entry is None else entry.get("valueFrom")
        ref = value_from.get("secretKeyRef") if isinstance(value_from, dict) else None
        if not isinstance(ref, dict):
            raise DeployConfigError(
                f"[{env_name}] secret override {name}: the base manifest has no secretKeyRef "
                "for it (secrets may only re-point an existing reference)"
            )
        logger.debug("[%s] secret %s: %r -> %r", env_name, name, ref.get("name"), secret)
        ref["name"] = secret


def substitute(value: str, variables: Mapping[str, str], where: str) -> str:
    """Fill ``${NAME}`` placeholders in *value*; ``$$`` is a literal ``$``."""
    try:
        return string.Template(value).substitute(variables)
    except KeyError as exc:
        raise DeployConfigError(
            f"{where}: placeholder ${{{exc.args[0]}}} has no value; pass it with "
            "--substitute-from-env"
        ) from exc
    except ValueError as exc:
        raise DeployConfigError(f"{where}: malformed placeholder in {value!r}") from exc


def _substitute_overrides(
    overrides: Overrides, variables: Mapping[str, str], env_name: str
) -> Overrides:
    def fill(values: Mapping[str, str], kind: str) -> dict[str, str]:
        return {k: substitute(v, variables, f"[{env_name}] {kind} {k}") for k, v in values.items()}

    account = overrides.service_account
    return Overrides(
        env=fill(overrides.env, "env"),
        secrets=fill(overrides.secrets, "secret"),
        annotations=fill(overrides.annotations, "annotation"),
        service_account=None
        if account is None
        else substitute(account, variables, f"[{env_name}] service_account"),
    )


def _child_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` as a mapping, creating it when absent or null."""
    if parent.get(key) is None:
        parent[key] = {}
    return require_mapping(parent[key], key)


def render_manifest(
    base: Any,
    environment: Environment,
    image: str,
    variables: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a new manifest: *base* with *environment*'s overlay and *image*."""
    manifest = copy.deepcopy(require_mapping(base, "manifest"))
    container = single_container(manifest)
    overrides = _substitute_overrides(environment.overrides, variables or {}, environment.name)

    metadata = _child_mapping(manifest, "metadata")
    metadata["name"] = environment.service
    _child_mapping(metadata, "labels")["app"] = environment.service

    template = manifest["spec"]["template"]
    annotations = _child_mapping(_child_mapping(template, "metadata"), "annotations")
    for key, value in overrides.annotations.items():
        logger.debug(
            "[%s] annotation %s: %r -> %r", environment.name, key, annotations.get(key), value
        )
        annotations[key] = value

    if overrides.service_account is not None:
        template["spec"]["serviceAccountName"] = overrides.service_account
        logger.debug("[%s] serviceAccountName = %s", environment.name, overrides.service_account)

    _apply_env(container, overrides.env, environment.name)
    _apply_secrets(container, overrides.secrets, environment.name)
    container["image"] = require_str(image, "image")
    logger.info(
        "Rendered %s for environment %s (image %s)", environment.service, environment.name, image
    )
    return manifest


def variables_from_env(names: Sequence[str]) -> dict[str, str]:
    """Read each named placeholder value from the process environment.

    Values are read here rather than passed as argv so a value never appears
    in a process listing or a make recipe; only the names are logged.
    """
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise DeployConfigError(f"--substitute-from-env: unset environment variable(s) {missing}")
    logger.debug("Substituting placeholders from environment: %s", list(names))
    return {name: os.environ[name] for name in names}


def verify_rendered(path: Path, image: str) -> None:
    """Re-read *path* and prove its single container runs *image*.

    Guards the write itself: a future change to rendering that dropped or
    rewrote the image must fail here, before ``services replace`` applies it.
    """
    container = single_container(require_mapping(load_yaml(path), f"rendered {path}"))
    if container.get("image") != image:
        raise DeployConfigError(
            f"rendered {path} runs {container.get('image')!r}, expected {image!r}"
        )


def validate_registry(registry: Registry, base: Any) -> list[str]:
    """Render every environment offline; return the environment names checked."""
    variables = dict.fromkeys(placeholder_names(registry), VALIDATE_PLACEHOLDER_SENTINEL)
    logger.debug("Validating with placeholder sentinels for %s", sorted(variables))
    checked = []
    for name, environment in registry.environments.items():
        render_manifest(base, environment, VALIDATE_IMAGE_SENTINEL, variables)
        logger.info(
            "Environment %s OK: service=%s repository=%s trigger=%s",
            name,
            environment.service,
            registry.repository_for(environment),
            environment.trigger,
        )
        checked.append(name)
    return checked
