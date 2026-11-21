#!/usr/bin/env python3
"""Resolve and render a per-environment Cloud Run deploy (ADR-0036).

``deploy/environments.yaml`` is the single source of per-environment data; this
script is the only code that reads it. Four subcommands, driven by
``.github/workflows/deploy.yml`` and ``nightly.yml`` through ``make`` targets:

``plan``
    Decide which environment a workflow event deploys to, from ``GITHUB_REF``
    (``refs/heads/<branch>`` or ``refs/tags/<tag>``) and an optional requested
    environment (the ``workflow_dispatch`` input). A tag trigger also proves the
    tagged commit is reachable from ``<remote>/<ancestor_branch>``, so a release
    tag cut from a side branch is refused rather than shipped. Writes the plan
    (environment, service, region, repository, content-addressed image tag) to
    ``$GITHUB_OUTPUT``.

``validate``
    Offline, credential-free check of the whole registry: parse it, then
    render *every* environment against the base manifest with a sentinel for
    each ``${NAME}`` placeholder. Wired into ``make validate-config`` so a
    registry edit that would only fail at deploy time fails in CI instead.

``scan-refs``
    The branches the nightly security scans check out: every environment
    branch the registry declares (branch triggers, then production's
    ``ancestor_branch``), filtered to those that exist on the remote, as a JSON
    list on ``$GITHUB_OUTPUT``. Off unless ``NIGHTLY_SCAN_ENVIRONMENT_BRANCHES``
    is ``true``; while off it emits ``[""]`` (the default branch only).

``render``
    Apply one environment's overlay to the base manifest and set the image,
    then re-read the written file to prove the image landed.

Exit codes: ``0`` success, ``2`` configuration error (malformed registry or
manifest, git or file-write failure — and, from argparse itself, a usage
error), ``3`` refused.

Debugging: ``--log-level DEBUG`` logs every resolution step and every overlay
field applied; ``RUNNER_DEBUG=1`` turns that on without editing the workflow.
Under ``GITHUB_ACTIONS=true`` a failure is also emitted as an ``::error::``
annotation so it surfaces on the run summary.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import _deploy_registry
import _deploy_render
import _logging
import yaml

BRANCH_REF_PREFIX = _deploy_registry.BRANCH_REF_PREFIX
DEFAULT_BRANCH_REF = _deploy_registry.DEFAULT_BRANCH_REF
DEFAULT_LOG_LEVEL = _deploy_registry.DEFAULT_LOG_LEVEL
DEFAULT_REGISTRY_PATH: Final[Path] = Path("deploy/environments.yaml")
DEFAULT_REMOTE = _deploy_registry.DEFAULT_REMOTE
EXIT_CONFIG_ERROR = _deploy_registry.EXIT_CONFIG_ERROR
EXIT_OK = _deploy_registry.EXIT_OK
EXIT_REFUSED = _deploy_registry.EXIT_REFUSED
GITHUB_ACTIONS_ENV = _deploy_registry.GITHUB_ACTIONS_ENV
GITHUB_EVENT_NAME_ENV = _deploy_registry.GITHUB_EVENT_NAME_ENV
GITHUB_OUTPUT_ENV = _deploy_registry.GITHUB_OUTPUT_ENV
GITHUB_REF_ENV = _deploy_registry.GITHUB_REF_ENV
LOG_LEVEL_CHOICES = _deploy_registry.LOG_LEVEL_CHOICES
REQUESTED_ENVIRONMENT_ENV = _deploy_registry.REQUESTED_ENVIRONMENT_ENV
RUNNER_DEBUG_ENV = _deploy_registry.RUNNER_DEBUG_ENV
SCAN_ENABLED_ENV = _deploy_registry.SCAN_ENABLED_ENV
SCAN_REFS_OUTPUT_KEY = _deploy_registry.SCAN_REFS_OUTPUT_KEY
SUPPORTED_SCHEMA_VERSION = _deploy_registry.SUPPORTED_SCHEMA_VERSION
TAG_REF_PREFIX = _deploy_registry.TAG_REF_PREFIX
VALIDATE_IMAGE_SENTINEL = _deploy_registry.VALIDATE_IMAGE_SENTINEL
VALIDATE_PLACEHOLDER_SENTINEL = _deploy_registry.VALIDATE_PLACEHOLDER_SENTINEL
DeployConfigError = _deploy_registry.DeployConfigError
DeployRefused = _deploy_registry.DeployRefused
Environment = _deploy_registry.Environment
Overrides = _deploy_registry.Overrides
Registry = _deploy_registry.Registry
Trigger = _deploy_registry.Trigger
# Docker tag grammar (OCI distribution spec): a release name becomes an alias
# tag, so one that cannot be a tag is refused at plan time, not mid-deploy.
_DOCKER_TAG_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_GIT_NOT_ANCESTOR_RETURNCODE = _deploy_registry.GIT_NOT_ANCESTOR_RETURNCODE
_GIT_REF_MISSING_RETURNCODE = _deploy_registry.GIT_REF_MISSING_RETURNCODE
# A $GITHUB_OUTPUT line is `key=value`; a newline in a value would forge keys.
_OUTPUT_KEY_RE: Final[re.Pattern[str]] = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_load_yaml = _deploy_registry.load_yaml
environment_branches = _deploy_registry.environment_branches
load_registry = _deploy_registry.load_registry
parse_flag = _deploy_registry.parse_flag
parse_registry = _deploy_registry.parse_registry
placeholder_names = _deploy_registry.placeholder_names

_variables_from_env = _deploy_render.variables_from_env
render_manifest = _deploy_render.render_manifest
validate_registry = _deploy_render.validate_registry
verify_rendered = _deploy_render.verify_rendered

_COMPAT_RENAMES: Final[dict[str, object]] = {
    "_ANNOTATION_KEY_PREFIXES": _deploy_registry.ANNOTATION_KEY_PREFIXES,
    "_require_mapping": _deploy_registry.require_mapping,
    "_require_str": _deploy_registry.require_str,
    "_single_container": _deploy_render.single_container,
    "_substitute": _deploy_render.substitute,
}
_COMPAT_MODULES: Final[tuple[Any, ...]] = (_deploy_registry, _deploy_render)


def __getattr__(name: str) -> Any:
    """Resolve backwards-compatible private facade attributes on demand."""
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in _COMPAT_RENAMES:
        return _COMPAT_RENAMES[name]
    for module in _COMPAT_MODULES:
        namespace = vars(module)
        if name in namespace:
            return namespace[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger("deploy_environment")


def resolve_environment(registry: Registry, ref: str, requested: str | None) -> Environment:
    """Return the environment that owns *ref*, honouring a *requested* one.

    A requested environment (``workflow_dispatch``) never widens what may
    deploy: it must be the environment the ref already resolves to, so a
    manual run cannot push a feature branch to production.
    """
    matches = [env for env in registry.environments.values() if env.trigger.matches(ref)]
    logger.debug("Ref %s matches environment(s) %s", ref, [e.name for e in matches])
    # Ambiguity is a registry defect whatever was requested; check it first so
    # a manual run cannot silently pick one of two overlapping environments.
    if len(matches) > 1:
        raise DeployConfigError(f"{ref} matches several environments: {[e.name for e in matches]}")
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
    # Fully qualified so a same-named tag or local branch cannot shadow it.
    target = f"refs/remotes/{remote}/{ancestor}"
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
    if release_tag and not _DOCKER_TAG_RE.match(release_tag):
        raise DeployRefused(
            f"release name {release_tag!r} is not a valid image tag "
            f"({_DOCKER_TAG_RE.pattern}); it would fail after the image was pushed"
        )
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
    try:
        with destination.open("a", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as exc:
        raise DeployConfigError(f"cannot write outputs to {destination}: {exc}") from exc
    logger.debug("Wrote %d output(s) to %s", len(lines), destination)


def _configure_logging(level_name: str) -> None:
    level = logging.DEBUG if os.environ.get(RUNNER_DEBUG_ENV) == "1" else level_name
    _logging.configure_script_logging(level=level, force=True)


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
    try:
        args.output.write_text(yaml.safe_dump(rendered, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        raise DeployConfigError(f"cannot write {args.output}: {exc}") from exc
    verify_rendered(args.output, args.image)
    logger.info("Wrote rendered manifest to %s", args.output)
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    base_path = args.base_manifest or registry.base_manifest
    checked = validate_registry(registry, _load_yaml(base_path))
    logger.info("Registry %s valid: %d environment(s) %s", args.registry, len(checked), checked)
    return EXIT_OK


def scan_refs(
    registry: Registry, *, enabled: bool, remote: str, run_git: GitRunner = _run_git
) -> list[str]:
    """The refs the nightly security scans should check out."""
    if not enabled:
        logger.info("%s is off; scanning the default branch only", SCAN_ENABLED_ENV)
        return [DEFAULT_BRANCH_REF]
    present = []
    for branch in environment_branches(registry):
        ref = f"refs/remotes/{remote}/{branch}"
        result = run_git(["rev-parse", "--verify", "--quiet", ref])
        if result.returncode == 0:
            present.append(branch)
        elif result.returncode == _GIT_REF_MISSING_RETURNCODE:
            logger.warning("Environment branch %s not found at %s; not scanning it", branch, ref)
        else:
            detail = result.stderr.strip() or "(no stderr)"
            raise DeployConfigError(f"cannot resolve {ref}: {detail}")
    if not present:
        raise DeployConfigError(
            f"{SCAN_ENABLED_ENV} is on but none of {environment_branches(registry)} exists "
            f"under refs/remotes/{remote}/ — was the checkout shallow?"
        )
    logger.info("Nightly scans will check out %s", present)
    return present


def _cmd_scan_refs(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    refs = scan_refs(
        registry, enabled=parse_flag(args.enabled, SCAN_ENABLED_ENV), remote=args.remote
    )
    write_outputs({SCAN_REFS_OUTPUT_KEY: json.dumps(refs)}, args.github_output)
    return EXIT_OK


def _optional_path(value: str) -> Path | None:
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for deploy planning, rendering, and validation."""
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

    validate = sub.add_parser(
        "validate", help="Offline check: parse the registry and render every environment."
    )
    validate.add_argument(
        "--base-manifest",
        type=_optional_path,
        default=None,
        help="Override the registry's defaults.base_manifest.",
    )
    validate.set_defaults(handler=_cmd_validate)

    scan = sub.add_parser(
        "scan-refs", help="Resolve the branches the nightly security scans check out."
    )
    scan.add_argument(
        "--enabled",
        default=os.environ.get(SCAN_ENABLED_ENV, ""),
        help=f"true/false (default: ${SCAN_ENABLED_ENV}, else false).",
    )
    scan.add_argument("--remote", default=DEFAULT_REMOTE)
    scan.add_argument(
        "--github-output",
        type=_optional_path,
        default=_optional_path(os.environ.get(GITHUB_OUTPUT_ENV, "")),
        help="File to append the refs output to (default: $GITHUB_OUTPUT, else stdout).",
    )
    scan.set_defaults(handler=_cmd_scan_refs)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for Cloud Run deploy environment resolution and rendering."""
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
