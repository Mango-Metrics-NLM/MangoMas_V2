"""Deploy registry model, schema validation, and YAML parsing (ADR-0036)."""

from __future__ import annotations

import fnmatch
import logging
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

logger = logging.getLogger("deploy_environment")

EXIT_OK: Final[int] = 0
EXIT_CONFIG_ERROR: Final[int] = 2
EXIT_REFUSED: Final[int] = 3

SUPPORTED_SCHEMA_VERSION: Final[int] = 1
DEFAULT_REMOTE: Final[str] = "origin"
DEFAULT_LOG_LEVEL: Final[str] = "INFO"
LOG_LEVEL_CHOICES: Final[tuple[str, ...]] = ("DEBUG", "INFO", "WARNING", "ERROR")

BRANCH_REF_PREFIX: Final[str] = "refs/heads/"
TAG_REF_PREFIX: Final[str] = "refs/tags/"

# GitHub-provided context, read only as argparse defaults so tests and local
# runs pass explicit values instead.
GITHUB_REF_ENV: Final[str] = "GITHUB_REF"
GITHUB_EVENT_NAME_ENV: Final[str] = "GITHUB_EVENT_NAME"
GITHUB_OUTPUT_ENV: Final[str] = "GITHUB_OUTPUT"
GITHUB_ACTIONS_ENV: Final[str] = "GITHUB_ACTIONS"
RUNNER_DEBUG_ENV: Final[str] = "RUNNER_DEBUG"
REQUESTED_ENVIRONMENT_ENV: Final[str] = "DEPLOY_REQUESTED_ENVIRONMENT"
SCAN_ENABLED_ENV: Final[str] = "NIGHTLY_SCAN_ENVIRONMENT_BRANCHES"
# actions/checkout treats an empty `ref` as "the event's default ref" — for a
# scheduled run, the default branch.
DEFAULT_BRANCH_REF: Final[str] = ""
SCAN_REFS_OUTPUT_KEY: Final[str] = "refs"
_BOOLEAN_TOKENS: Final[dict[str, bool]] = {"true": True, "false": False, "": False}
GIT_REF_MISSING_RETURNCODE: Final[int] = 1

_OVERLAY_KEYS: Final[frozenset[str]] = frozenset(
    {"env", "secrets", "annotations", "service_account"}
)
_TRIGGER_KEYS: Final[frozenset[str]] = frozenset({"branch", "tag_pattern", "ancestor_branch"})
_ENVIRONMENT_KEYS: Final[frozenset[str]] = frozenset(
    {"trigger", "service", "repository", "overrides"}
)
_DEFAULTS_KEYS: Final[frozenset[str]] = frozenset(
    {"region", "repository", "image", "base_manifest"}
)
_REGISTRY_KEYS: Final[frozenset[str]] = frozenset({"schema_version", "defaults", "environments"})

_ENVIRONMENT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9-]*$")
# Cloud Run service names: lowercase, start with a letter, <= 63 chars, no
# trailing hyphen.
_SERVICE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?$")

GIT_NOT_ANCESTOR_RETURNCODE: Final[int] = 1

# Overlay env/secret keys must be valid environment-variable names.
_ENV_VAR_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Cloud Run sets these itself and rejects a manifest that sets them.
_RESERVED_ENV_VARS: Final[frozenset[str]] = frozenset(
    {"PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"}
)
# Annotation keys an overlay may set. Anything else (VPC egress, ingress,
# execution environment, ...) changes the service's security posture and
# belongs in the reviewed base manifest, not in a per-environment overlay.
ANNOTATION_KEY_PREFIXES: Final[tuple[str, ...]] = ("autoscaling.knative.dev/",)

# Stand-in for every placeholder during `validate`: rendering must succeed for
# any value, and a recognisable sentinel makes a leaked one obvious.
VALIDATE_PLACEHOLDER_SENTINEL: Final[str] = "validate-placeholder"
VALIDATE_IMAGE_SENTINEL: Final[str] = "validate.invalid/image@sha256:" + "0" * 64


class DeployConfigError(Exception):
    """The registry, the manifest or git cannot be used as-is."""


class DeployRefused(Exception):
    """The event is well-formed but must not deploy."""


@dataclass(frozen=True)
class Trigger:
    """When an environment deploys: a branch push, or a tag on a branch."""

    branch: str | None = None
    tag_pattern: str | None = None
    ancestor_branch: str | None = None

    def matches(self, ref: str) -> bool:
        """Whether a fully-qualified *ref* belongs to this trigger."""
        if self.branch is not None:
            return ref == f"{BRANCH_REF_PREFIX}{self.branch}"
        if self.tag_pattern is not None and ref.startswith(TAG_REF_PREFIX):
            return fnmatch.fnmatchcase(ref.removeprefix(TAG_REF_PREFIX), self.tag_pattern)
        return False


@dataclass(frozen=True)
class Overrides:
    """The closed set of fields an environment may change on the base manifest."""

    env: Mapping[str, str] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)
    service_account: str | None = None


@dataclass(frozen=True)
class Environment:
    name: str
    service: str
    trigger: Trigger
    overrides: Overrides
    # Artifact Registry repository this environment's images live in; None
    # inherits defaults.repository. Separate repositories keep one
    # environment's deployer from writing an image another environment runs.
    repository: str | None = None


@dataclass(frozen=True)
class Registry:
    region: str
    repository: str
    image: str
    base_manifest: Path
    environments: Mapping[str, Environment]

    def repository_for(self, environment: Environment) -> str:
        """The image repository *environment* deploys from."""
        return environment.repository or self.repository

    def branch_triggers(self) -> list[str]:
        """Every branch some environment deploys from, in registry order."""
        return [e.trigger.branch for e in self.environments.values() if e.trigger.branch]


def require_mapping(value: Any, where: str) -> dict[str, Any]:
    """Validate that *value* is a dict mapping or raise :class:`DeployConfigError`."""
    if not isinstance(value, dict):
        raise DeployConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def require_str(value: Any, where: str) -> str:
    """Validate that *value* is a non-empty string or raise :class:`DeployConfigError`."""
    if not isinstance(value, str) or not value:
        # YAML 1.1 turns unquoted true/no/0/on into bool/int; say how to fix it.
        hint = " (quote it in YAML)" if isinstance(value, bool | int | float) else ""
        raise DeployConfigError(f"{where}: expected a non-empty string, got {value!r}{hint}")
    return value


def _reject_unknown_keys(mapping: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise DeployConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _str_mapping(value: Any, where: str) -> dict[str, str]:
    # An empty section (`env:` with nothing under it) parses as None; treat it
    # like an omitted one, the same way `overrides:` itself is treated.
    raw = require_mapping(value if value is not None else {}, where)
    return {require_str(k, f"{where} key"): require_str(v, f"{where}.{k}") for k, v in raw.items()}


def _parse_trigger(value: Any, where: str) -> Trigger:
    raw = require_mapping(value, where)
    _reject_unknown_keys(raw, _TRIGGER_KEYS, where)
    branch = raw.get("branch")
    tag_pattern = raw.get("tag_pattern")
    ancestor = raw.get("ancestor_branch")
    if (branch is None) == (tag_pattern is None):
        raise DeployConfigError(f"{where}: set exactly one of 'branch' or 'tag_pattern'")
    if branch is not None and ancestor is not None:
        raise DeployConfigError(f"{where}: 'ancestor_branch' applies only to 'tag_pattern'")
    return Trigger(
        branch=None if branch is None else require_str(branch, f"{where}.branch"),
        tag_pattern=None if tag_pattern is None else require_str(tag_pattern, f"{where}.tag"),
        ancestor_branch=None if ancestor is None else require_str(ancestor, f"{where}.ancestor"),
    )


def _check_env_names(names: Mapping[str, str], where: str) -> None:
    for name in names:
        if not _ENV_VAR_NAME_RE.match(name):
            raise DeployConfigError(f"{where}: {name!r} is not a valid environment variable name")
        if name in _RESERVED_ENV_VARS:
            raise DeployConfigError(f"{where}: {name} is reserved by Cloud Run")


def _check_annotation_keys(keys: Mapping[str, str], where: str) -> None:
    for key in keys:
        if not key.startswith(ANNOTATION_KEY_PREFIXES):
            raise DeployConfigError(
                f"{where}: annotation {key!r} is not overridable per environment; allowed "
                f"prefixes: {list(ANNOTATION_KEY_PREFIXES)} (change the base manifest instead)"
            )


def _parse_overrides(value: Any, where: str) -> Overrides:
    raw = require_mapping(value if value is not None else {}, where)
    _reject_unknown_keys(raw, _OVERLAY_KEYS, where)
    account = raw.get("service_account")
    env = _str_mapping(raw.get("env"), f"{where}.env")
    secrets = _str_mapping(raw.get("secrets"), f"{where}.secrets")
    annotations = _str_mapping(raw.get("annotations"), f"{where}.annotations")
    _check_env_names(env, f"{where}.env")
    _check_env_names(secrets, f"{where}.secrets")
    _check_annotation_keys(annotations, f"{where}.annotations")
    return Overrides(
        env=env,
        secrets=secrets,
        annotations=annotations,
        service_account=None if account is None else require_str(account, f"{where}.sa"),
    )


def _parse_environment(name: Any, value: Any) -> Environment:
    where = f"environments.{name}"
    if not isinstance(name, str) or not _ENVIRONMENT_NAME_RE.match(name):
        raise DeployConfigError(f"{where}: name must match {_ENVIRONMENT_NAME_RE.pattern}")
    raw = require_mapping(value, where)
    _reject_unknown_keys(raw, _ENVIRONMENT_KEYS, where)
    service = require_str(raw.get("service"), f"{where}.service")
    if not _SERVICE_NAME_RE.match(service):
        raise DeployConfigError(f"{where}.service: {service!r} is not a valid Cloud Run name")
    repository = raw.get("repository")
    return Environment(
        name=name,
        service=service,
        trigger=_parse_trigger(raw.get("trigger"), f"{where}.trigger"),
        overrides=_parse_overrides(raw.get("overrides"), f"{where}.overrides"),
        repository=None if repository is None else require_str(repository, f"{where}.repo"),
    )


def _check_uniqueness(environments: Mapping[str, Environment]) -> None:
    services = [e.service for e in environments.values()]
    if len(services) != len(set(services)):
        raise DeployConfigError(f"environments: service names must be unique: {services}")
    branches = [e.trigger.branch for e in environments.values() if e.trigger.branch]
    if len(branches) != len(set(branches)):
        raise DeployConfigError(f"environments: branch triggers must be unique: {branches}")


def parse_registry(document: Any) -> Registry:
    """Validate a parsed ``environments.yaml`` document into a :class:`Registry`."""
    raw = require_mapping(document, "registry")
    _reject_unknown_keys(raw, _REGISTRY_KEYS, "registry")
    version = raw.get("schema_version")
    if isinstance(version, bool) or version != SUPPORTED_SCHEMA_VERSION:
        logger.debug(
            "Rejected unsupported deploy registry schema_version: %r",
            version,
            extra={"event": "deploy_invalid_schema_version"},
        )
        raise DeployConfigError(
            f"registry: schema_version {version!r} unsupported "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )
    defaults = require_mapping(raw.get("defaults"), "defaults")
    _reject_unknown_keys(defaults, _DEFAULTS_KEYS, "defaults")
    envs_raw = require_mapping(raw.get("environments"), "environments")
    if not envs_raw:
        raise DeployConfigError("environments: at least one environment is required")
    environments = {name: _parse_environment(name, value) for name, value in envs_raw.items()}
    _check_uniqueness(environments)
    return Registry(
        region=require_str(defaults.get("region"), "defaults.region"),
        repository=require_str(defaults.get("repository"), "defaults.repository"),
        image=require_str(defaults.get("image"), "defaults.image"),
        base_manifest=Path(require_str(defaults.get("base_manifest"), "defaults.base_manifest")),
        environments=environments,
    )


def load_yaml(path: Path) -> Any:
    """Read and safely parse YAML from *path*, raising :class:`DeployConfigError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeployConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DeployConfigError(f"malformed YAML in {path}: {exc}") from exc


def load_registry(path: Path) -> Registry:
    """Read and validate the registry at *path*."""
    registry = parse_registry(load_yaml(path))
    logger.debug(
        "Loaded registry %s: environments=%s region=%s repository=%s",
        path,
        list(registry.environments),
        registry.region,
        registry.repository,
    )
    return registry


def placeholder_names(registry: Registry) -> set[str]:
    """Every ``${NAME}`` identifier used anywhere in the registry's overlays."""
    names: set[str] = set()
    for env in registry.environments.values():
        overrides = env.overrides
        values = [
            *overrides.env.values(),
            *overrides.secrets.values(),
            *overrides.annotations.values(),
            *([overrides.service_account] if overrides.service_account else []),
        ]
        for value in values:
            names.update(string.Template(value).get_identifiers())
    return names


def environment_branches(registry: Registry) -> list[str]:
    """Every branch an environment lives on, in registry order, without repeats.

    Branch triggers (``dev``, ``qa``) first, then the branches release tags must
    be reachable from (production's ``main``).
    """
    branches = registry.branch_triggers() + [
        env.trigger.ancestor_branch
        for env in registry.environments.values()
        if env.trigger.ancestor_branch
    ]
    return list(dict.fromkeys(branches))


def parse_flag(value: str, where: str) -> bool:
    """Strict boolean: a typo must fail loudly, not silently mean "off"."""
    try:
        return _BOOLEAN_TOKENS[value.strip().lower()]
    except KeyError as exc:
        raise DeployConfigError(f"{where}: expected true/false, got {value!r}") from exc
