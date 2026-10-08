#!/usr/bin/env python3
"""Resolve and render a per-environment Cloud Run deploy (ADR-0036).

``deploy/environments.yaml`` is the single source of per-environment data; this
script is the only code that reads it. Two subcommands, both driven by
``.github/workflows/deploy.yml`` through ``make`` targets:

``plan``
    Decide which environment a workflow event deploys to, from ``GITHUB_REF``
    (``refs/heads/<branch>`` or ``refs/tags/<tag>``) and an optional requested
    environment (the ``workflow_dispatch`` input). A tag trigger also proves the
    tagged commit is reachable from ``<remote>/<ancestor_branch>``, so a release
    tag cut from a side branch is refused rather than shipped. Writes the plan
    (environment, service, region, repository, content-addressed image tag) to
    ``$GITHUB_OUTPUT``.

``render``
    Apply one environment's overlay to the base manifest and set the image,
    then re-read the written file to prove the image landed. The overlay schema
    is closed: an unknown key, a plain value aimed at a secret, or a secret
    name aimed at a plain variable fails loudly instead of deploying something
    nobody wrote. Overlay strings may carry ``${NAME}`` placeholders (e.g. the
    project id inside a service-account e-mail), filled only from environment
    variables named with ``--substitute-from-env`` — so the registry never
    hard-codes a project, and a missing value is an error, not a blank.

These checks catch mistakes; they are not the security boundary. On a release
or dispatch the workflow, this script and the registry are read from the ref
being deployed, so the authoritative controls are the GitHub Environment
protection rules and the Workload Identity Federation conditions
(deploy/README.md).

Exit codes: ``0`` success, ``2`` configuration error (malformed registry or
manifest, git failure), ``3`` refused (the ref maps to no environment, the
requested environment does not own the ref, or the ancestry check failed).

Debugging: ``--log-level DEBUG`` logs every resolution step and every overlay
field applied; ``RUNNER_DEBUG=1`` (set by GitHub when a run is re-run with
debug logging) turns that on without editing the workflow. Under
``GITHUB_ACTIONS=true`` a failure is also emitted as an ``::error::``
annotation so it surfaces on the run summary.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import logging
import os
import re
import string
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

logger = logging.getLogger("deploy_environment")

EXIT_OK: Final[int] = 0
EXIT_CONFIG_ERROR: Final[int] = 2
EXIT_REFUSED: Final[int] = 3

SUPPORTED_SCHEMA_VERSION: Final[int] = 1
DEFAULT_REGISTRY_PATH: Final[Path] = Path("deploy/environments.yaml")
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
# A $GITHUB_OUTPUT line is `key=value`; a newline in a value would forge keys.
_OUTPUT_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

_GIT_NOT_ANCESTOR_RETURNCODE: Final[int] = 1


class DeployConfigError(Exception):
    """The registry, the manifest or git cannot be used as-is."""


class DeployRefused(Exception):
    """The event is well-formed but must not deploy."""


# ── Registry model ─────────────────────────────────────────────────────────


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


# ── Validation helpers ─────────────────────────────────────────────────────


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeployConfigError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeployConfigError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _reject_unknown_keys(mapping: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise DeployConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _str_mapping(value: Any, where: str) -> dict[str, str]:
    raw = _require_mapping(value, where)
    return {
        _require_str(k, f"{where} key"): _require_str(v, f"{where}.{k}") for k, v in raw.items()
    }


def _parse_trigger(value: Any, where: str) -> Trigger:
    raw = _require_mapping(value, where)
    _reject_unknown_keys(raw, _TRIGGER_KEYS, where)
    branch = raw.get("branch")
    tag_pattern = raw.get("tag_pattern")
    ancestor = raw.get("ancestor_branch")
    if (branch is None) == (tag_pattern is None):
        raise DeployConfigError(f"{where}: set exactly one of 'branch' or 'tag_pattern'")
    if branch is not None and ancestor is not None:
        raise DeployConfigError(f"{where}: 'ancestor_branch' applies only to 'tag_pattern'")
    return Trigger(
        branch=None if branch is None else _require_str(branch, f"{where}.branch"),
        tag_pattern=None if tag_pattern is None else _require_str(tag_pattern, f"{where}.tag"),
        ancestor_branch=None if ancestor is None else _require_str(ancestor, f"{where}.ancestor"),
    )


def _parse_overrides(value: Any, where: str) -> Overrides:
    raw = _require_mapping(value if value is not None else {}, where)
    _reject_unknown_keys(raw, _OVERLAY_KEYS, where)
    account = raw.get("service_account")
    return Overrides(
        env=_str_mapping(raw.get("env", {}), f"{where}.env"),
        secrets=_str_mapping(raw.get("secrets", {}), f"{where}.secrets"),
        annotations=_str_mapping(raw.get("annotations", {}), f"{where}.annotations"),
        service_account=None if account is None else _require_str(account, f"{where}.sa"),
    )


def _parse_environment(name: Any, value: Any) -> Environment:
    where = f"environments.{name}"
    if not isinstance(name, str) or not _ENVIRONMENT_NAME_RE.match(name):
        raise DeployConfigError(f"{where}: name must match {_ENVIRONMENT_NAME_RE.pattern}")
    raw = _require_mapping(value, where)
    _reject_unknown_keys(raw, _ENVIRONMENT_KEYS, where)
    service = _require_str(raw.get("service"), f"{where}.service")
    if not _SERVICE_NAME_RE.match(service):
        raise DeployConfigError(f"{where}.service: {service!r} is not a valid Cloud Run name")
    repository = raw.get("repository")
    return Environment(
        name=name,
        service=service,
        trigger=_parse_trigger(raw.get("trigger"), f"{where}.trigger"),
        overrides=_parse_overrides(raw.get("overrides"), f"{where}.overrides"),
        repository=None if repository is None else _require_str(repository, f"{where}.repo"),
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
    raw = _require_mapping(document, "registry")
    _reject_unknown_keys(raw, _REGISTRY_KEYS, "registry")
    version = raw.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise DeployConfigError(
            f"registry: schema_version {version!r} unsupported "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )
    defaults = _require_mapping(raw.get("defaults"), "defaults")
    _reject_unknown_keys(defaults, _DEFAULTS_KEYS, "defaults")
    envs_raw = _require_mapping(raw.get("environments"), "environments")
    if not envs_raw:
        raise DeployConfigError("environments: at least one environment is required")
    environments = {name: _parse_environment(name, value) for name, value in envs_raw.items()}
    _check_uniqueness(environments)
    return Registry(
        region=_require_str(defaults.get("region"), "defaults.region"),
        repository=_require_str(defaults.get("repository"), "defaults.repository"),
        image=_require_str(defaults.get("image"), "defaults.image"),
        base_manifest=Path(_require_str(defaults.get("base_manifest"), "defaults.base_manifest")),
        environments=environments,
    )


def _load_yaml(path: Path) -> Any:
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
    registry = parse_registry(_load_yaml(path))
    logger.debug(
        "Loaded registry %s: environments=%s region=%s repository=%s",
        path,
        list(registry.environments),
        registry.region,
        registry.repository,
    )
    return registry


# ── Plan: event -> environment ─────────────────────────────────────────────


def resolve_environment(registry: Registry, ref: str, requested: str | None) -> Environment:
    """Return the environment that owns *ref*, honouring a *requested* one.

    A requested environment (``workflow_dispatch``) never widens what may
    deploy: it must be the environment the ref already resolves to, so a
    manual run cannot push a feature branch to production.
    """
    matches = [env for env in registry.environments.values() if env.trigger.matches(ref)]
    logger.debug("Ref %s matches environment(s) %s", ref, [e.name for e in matches])
    if requested:
        if requested not in registry.environments:
            raise DeployRefused(
                f"requested environment {requested!r} is not one of {list(registry.environments)}"
            )
        if registry.environments[requested] not in matches:
            raise DeployRefused(
                f"environment {requested!r} does not deploy from {ref}; "
                f"its trigger is {registry.environments[requested].trigger}"
            )
        return registry.environments[requested]
    if not matches:
        raise DeployRefused(f"no environment deploys from {ref}")
    if len(matches) > 1:
        raise DeployConfigError(f"{ref} matches several environments: {[e.name for e in matches]}")
    return matches[0]


GitRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    logger.debug("Running git %s", " ".join(args))
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DeployConfigError(f"git {' '.join(args)} failed to start: {exc}") from exc


def _git_stdout(run_git: GitRunner, args: Sequence[str]) -> str:
    result = run_git(args)
    if result.returncode != 0:
        detail = result.stderr.strip() or "(no stderr)"
        raise DeployConfigError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def verify_ancestry(
    environment: Environment, *, remote: str, commit: str = "HEAD", run_git: GitRunner = _run_git
) -> None:
    """Refuse a tag trigger whose commit is not on its ancestor branch."""
    ancestor = environment.trigger.ancestor_branch
    if ancestor is None:
        logger.debug("Environment %s has no ancestor_branch; skipping check", environment.name)
        return
    target = f"{remote}/{ancestor}"
    result = run_git(["merge-base", "--is-ancestor", commit, target])
    if result.returncode == _GIT_NOT_ANCESTOR_RETURNCODE:
        raise DeployRefused(f"{commit} is not reachable from {target}; refusing to deploy")
    if result.returncode != 0:
        detail = result.stderr.strip() or "(no stderr)"
        raise DeployConfigError(f"ancestry check against {target} failed: {detail}")
    logger.info("Verified %s is reachable from %s", commit, target)


def content_image_tag(run_git: GitRunner = _run_git) -> str:
    """The git *tree* hash of HEAD.

    A promotion merge commit has a new SHA but the same tree as the branch it
    promotes, so every environment tags the build of identical source with the
    same name, and a re-deploy of an unchanged tree reuses that environment's
    own image instead of rebuilding.
    """
    return _git_stdout(run_git, ["rev-parse", "HEAD^{tree}"])


def build_plan(
    registry: Registry,
    *,
    ref: str,
    requested: str | None,
    remote: str,
    run_git: GitRunner = _run_git,
) -> dict[str, str]:
    """Resolve, verify and describe one deploy as ``$GITHUB_OUTPUT`` pairs."""
    environment = resolve_environment(registry, ref, requested)
    verify_ancestry(environment, remote=remote, run_git=run_git)
    release_tag = ref.removeprefix(TAG_REF_PREFIX) if ref.startswith(TAG_REF_PREFIX) else ""
    plan = {
        "environment": environment.name,
        "service": environment.service,
        "region": registry.region,
        "repository": registry.repository_for(environment),
        "image": registry.image,
        "image_tag": content_image_tag(run_git),
        "release_tag": release_tag,
    }
    logger.info("Deploy plan: %s", plan)
    return plan


def write_outputs(outputs: Mapping[str, str], destination: Path | None) -> None:
    """Append ``key=value`` lines to *destination*, or print them if unset."""
    lines = []
    for key, value in outputs.items():
        if not _OUTPUT_KEY_RE.match(key) or "\n" in value or "\r" in value:
            raise DeployConfigError(f"refusing to write unsafe output {key!r}={value!r}")
        lines.append(f"{key}={value}\n")
    if destination is None:
        sys.stdout.write("".join(lines))
        return
    with destination.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)
    logger.debug("Wrote %d output(s) to %s", len(lines), destination)


# ── Render: base manifest + overlay ────────────────────────────────────────


def _single_container(manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        containers = manifest["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError) as exc:
        raise DeployConfigError("manifest has no spec.template.spec.containers") from exc
    if not isinstance(containers, list) or len(containers) != 1:
        raise DeployConfigError("manifest must declare exactly one container")
    return _require_mapping(containers[0], "container")


def _env_entry(container: dict[str, Any], name: str) -> dict[str, Any] | None:
    for entry in container.setdefault("env", []):
        if entry.get("name") == name:
            return _require_mapping(entry, f"env {name}")
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


def _substitute(value: str, variables: Mapping[str, str], where: str) -> str:
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
        return {k: _substitute(v, variables, f"[{env_name}] {kind} {k}") for k, v in values.items()}

    account = overrides.service_account
    return Overrides(
        env=fill(overrides.env, "env"),
        secrets=fill(overrides.secrets, "secret"),
        annotations=fill(overrides.annotations, "annotation"),
        service_account=None
        if account is None
        else _substitute(account, variables, f"[{env_name}] service_account"),
    )


def render_manifest(
    base: Any,
    environment: Environment,
    image: str,
    variables: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a new manifest: *base* with *environment*'s overlay and *image*."""
    manifest = copy.deepcopy(_require_mapping(base, "manifest"))
    container = _single_container(manifest)
    overrides = _substitute_overrides(environment.overrides, variables or {}, environment.name)

    metadata = manifest.setdefault("metadata", {})
    metadata["name"] = environment.service
    metadata.setdefault("labels", {})["app"] = environment.service

    template = manifest["spec"]["template"]
    annotations = template.setdefault("metadata", {}).setdefault("annotations", {})
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
    container["image"] = _require_str(image, "image")
    logger.info(
        "Rendered %s for environment %s (image %s)", environment.service, environment.name, image
    )
    return manifest


# ── CLI ────────────────────────────────────────────────────────────────────


def _configure_logging(level_name: str) -> None:
    level = logging.DEBUG if os.environ.get(RUNNER_DEBUG_ENV) == "1" else level_name
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)


def _report(exc: Exception, title: str) -> None:
    logger.error("%s", exc)
    if os.environ.get(GITHUB_ACTIONS_ENV) == "true":
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        sys.stdout.write(f"::error title={title}::{message}\n")


def _cmd_plan(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    logger.info("Planning deploy for event=%s ref=%s", args.event_name or "(unset)", args.ref)
    plan = build_plan(
        registry, ref=args.ref, requested=args.environment or None, remote=args.remote
    )
    write_outputs(plan, args.github_output)
    return EXIT_OK


def _cmd_render(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    if args.environment not in registry.environments:
        raise DeployConfigError(
            f"unknown environment {args.environment!r}; known: {list(registry.environments)}"
        )
    base_path = args.base_manifest or registry.base_manifest
    rendered = render_manifest(
        _load_yaml(base_path),
        registry.environments[args.environment],
        args.image,
        _variables_from_env(args.substitute_from_env),
    )
    args.output.write_text(yaml.safe_dump(rendered, sort_keys=False), encoding="utf-8")
    verify_rendered(args.output, args.image)
    logger.info("Wrote rendered manifest to %s", args.output)
    return EXIT_OK


def _variables_from_env(names: Sequence[str]) -> dict[str, str]:
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
    container = _single_container(_require_mapping(_load_yaml(path), f"rendered {path}"))
    if container.get("image") != image:
        raise DeployConfigError(
            f"rendered {path} runs {container.get('image')!r}, expected {image!r}"
        )


def _optional_path(value: str) -> Path | None:
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--log-level", choices=LOG_LEVEL_CHOICES, default=DEFAULT_LOG_LEVEL)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Resolve the environment a workflow event deploys to.")
    plan.add_argument("--ref", default=os.environ.get(GITHUB_REF_ENV, ""))
    plan.add_argument("--event-name", default=os.environ.get(GITHUB_EVENT_NAME_ENV, ""))
    plan.add_argument("--environment", default=os.environ.get(REQUESTED_ENVIRONMENT_ENV, ""))
    plan.add_argument("--remote", default=DEFAULT_REMOTE)
    plan.add_argument(
        "--github-output",
        type=_optional_path,
        default=_optional_path(os.environ.get(GITHUB_OUTPUT_ENV, "")),
        help="File to append key=value outputs to (default: $GITHUB_OUTPUT, else stdout).",
    )
    plan.set_defaults(handler=_cmd_plan)

    render = sub.add_parser("render", help="Render one environment's Cloud Run manifest.")
    render.add_argument("--environment", required=True)
    render.add_argument("--image", required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument(
        "--substitute-from-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Fill ${NAME} overlay placeholders from this environment variable (repeatable).",
    )
    render.add_argument(
        "--base-manifest",
        type=_optional_path,
        default=None,
        help="Override the registry's defaults.base_manifest.",
    )
    render.set_defaults(handler=_cmd_render)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.log_level)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        if args.command == "plan" and not args.ref:
            raise DeployConfigError(f"--ref is required (or set {GITHUB_REF_ENV})")
        return handler(args)
    except DeployRefused as exc:
        _report(exc, "Deploy refused")
        return EXIT_REFUSED
    except DeployConfigError as exc:
        _report(exc, "Deploy configuration error")
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
