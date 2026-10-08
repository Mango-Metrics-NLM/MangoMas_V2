"""Contract tests tying ``deploy/environments.yaml`` to everything that consumes it.

The registry is the single source of per-environment deploy data (ADR-0036),
but three other artefacts restate parts of it because GitHub requires static
values: ``deploy.yml``'s push branches, its ``workflow_dispatch`` options, and
the ``MANGOMAS_ENV`` label each environment sets must be one the settings
model accepts. Each restatement is pinned here in both directions, and every
environment's *rendered* manifest is held to the same shape contract as the
base manifest — an overlay can only change the fields it is allowed to.
"""

from __future__ import annotations

import subprocess
import typing
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from mangomas.config import Settings
from tests._script_loader import load_script_module
from tests.constants.deploy import (
    ENV_LABEL_VAR,
    PROJECT_ID_PLACEHOLDER_VAR,
    TEST_IMAGE_REF,
    TEST_PROJECT_ID,
)
from tests.deploy import _makefile, _workflows

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _REPO_ROOT / _makefile.variable("DEPLOY_REGISTRY")
_DEPLOY_WORKFLOW = "deploy.yml"
_ENV_LABEL_VAR = ENV_LABEL_VAR
_TEST_IMAGE = TEST_IMAGE_REF
_RENDER_VARIABLES = {PROJECT_ID_PLACEHOLDER_VAR: TEST_PROJECT_ID}

_REGISTRY = deploy_env.load_registry(_REGISTRY_PATH)
_ENVIRONMENT_NAMES = list(_REGISTRY.environments)

# Paths (dotted, list indices as ints) an overlay is allowed to change. Any
# other difference between a rendered environment and the base manifest is a
# rendering bug or an overlay smuggling in configuration nobody reviewed.
_IMAGE_PATH = ("spec", "template", "spec", "containers", 0, "image")
_ALLOWED_DIFF_PREFIXES: tuple[tuple[Any, ...], ...] = (
    ("metadata", "name"),
    ("metadata", "labels", "app"),
    ("spec", "template", "metadata", "annotations"),
    ("spec", "template", "spec", "serviceAccountName"),
    ("spec", "template", "spec", "containers", 0, "env"),
    _IMAGE_PATH,
)


def _base_manifest() -> dict[str, Any]:
    path = _REPO_ROOT / _REGISTRY.base_manifest
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _rendered(name: str) -> dict[str, Any]:
    result: dict[str, Any] = deploy_env.render_manifest(
        _base_manifest(), _REGISTRY.environments[name], _TEST_IMAGE, _RENDER_VARIABLES
    )
    return result


def _diff_paths(a: Any, b: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    """Yield every leaf path where *a* and *b* differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a.keys() | b.keys():
            yield from _diff_paths(a.get(key), b.get(key), (*path, key))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            yield from _diff_paths(x, y, (*path, index))
    elif a != b:
        yield path


def _env_vars(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    return {entry["name"]: entry for entry in container["env"]}


# ── registry <-> workflow ──────────────────────────────────────────────────


def test_push_branches_equal_the_registry_branch_triggers() -> None:
    """GitHub needs static push branches; they must be exactly the registry's."""
    push = _workflows.triggers(_DEPLOY_WORKFLOW)["push"]
    assert sorted(push["branches"]) == sorted(_REGISTRY.branch_triggers())


def test_dispatch_options_equal_the_registry_environments() -> None:
    dispatch = _workflows.triggers(_DEPLOY_WORKFLOW)["workflow_dispatch"]
    options = dispatch["inputs"]["environment"]["options"]
    assert sorted(options) == sorted(_ENVIRONMENT_NAMES)


def _production() -> Any:
    """The single tag-triggered environment — production by construction."""
    production = [e for e in _REGISTRY.environments.values() if e.trigger.tag_pattern]
    assert len(production) == 1, "expected exactly one tag-triggered (production) environment"
    return production[0]


def test_production_deploys_on_published_releases() -> None:
    """The tag-triggered environment only deploys if the workflow listens for releases."""
    assert _production().trigger.ancestor_branch, "production must name an ancestor branch"
    release = _workflows.triggers(_DEPLOY_WORKFLOW)["release"]
    assert release["types"] == ["published"]


def test_deploy_job_targets_the_planned_environment() -> None:
    jobs = _workflows.jobs(_DEPLOY_WORKFLOW)
    assert jobs["deploy"]["environment"] == "${{ needs.plan.outputs.environment }}"
    assert jobs["deploy"]["concurrency"]["cancel-in-progress"] is False


def test_plan_outputs_cover_every_value_the_deploy_job_reads() -> None:
    """Each ``needs.plan.outputs.X`` the deploy job reads must be a declared output."""
    jobs = _workflows.jobs(_DEPLOY_WORKFLOW)
    declared = set(jobs["plan"]["outputs"])
    raw = yaml.safe_dump(jobs["deploy"])
    read = {
        token.split("needs.plan.outputs.", 1)[1].split()[0].rstrip("}")
        for token in raw.split("${{")
        if "needs.plan.outputs." in token
    }
    assert read, "deploy job reads nothing from plan — has the wiring been removed?"
    assert read <= declared, f"undeclared plan outputs read: {sorted(read - declared)}"


def test_declared_plan_outputs_are_what_the_script_emits() -> None:
    """The other link: every job output maps to a key ``build_plan`` writes."""
    plan_job = _workflows.jobs(_DEPLOY_WORKFLOW)["plan"]
    emitted = deploy_env.build_plan(
        _REGISTRY,
        ref=f"{deploy_env.BRANCH_REF_PREFIX}{_REGISTRY.branch_triggers()[0]}",
        requested=None,
        remote=deploy_env.DEFAULT_REMOTE,
        run_git=lambda args: subprocess.CompletedProcess(args, 0, "tree", ""),
    )
    assert set(plan_job["outputs"]) == set(emitted)
    for name, expression in plan_job["outputs"].items():
        assert expression == f"${{{{ steps.plan.outputs.{name} }}}}"


def test_plan_job_runs_the_make_targets() -> None:
    """The one-place rule: deploy tooling is invoked through make, like CI."""
    plan_runs = [s["run"] for s in _workflows.jobs(_DEPLOY_WORKFLOW)["plan"]["steps"] if "run" in s]
    assert plan_runs == ["make install-deploy-tools", "make deploy-plan"]


def test_make_targets_drive_the_script() -> None:
    script = _makefile.variable("DEPLOY_SCRIPT")
    assert (_REPO_ROOT / script).is_file()
    assert "$(DEPLOY_SCRIPT)" in _makefile.target_body("deploy-plan")
    assert "$(DEPLOY_SCRIPT)" in _makefile.target_body("deploy-render")
    assert "$(RUNTIME_LOCKFILE)" in _makefile.target_body("install-deploy-tools"), (
        "deploy tooling must be pinned by the runtime lockfile, not a floating install"
    )


# ── registry <-> settings ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_environment_label_is_a_valid_settings_value(name: str) -> None:
    """``MANGOMAS_ENV`` is a Literal; a label outside it crashes the service at boot."""
    label = _env_vars(_rendered(name))[_ENV_LABEL_VAR]["value"]
    assert label in typing.get_args(Settings.model_fields["env"].annotation)


def test_environment_labels_are_distinct() -> None:
    labels = [_env_vars(_rendered(name))[_ENV_LABEL_VAR]["value"] for name in _ENVIRONMENT_NAMES]
    assert len(labels) == len(set(labels))


# ── rendered manifests keep the base contract ──────────────────────────────


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_overlay_changes_only_allowed_fields(name: str) -> None:
    offenders = [
        path
        for path in _diff_paths(_base_manifest(), _rendered(name))
        if not any(path[: len(prefix)] == prefix for prefix in _ALLOWED_DIFF_PREFIXES)
    ]
    assert offenders == [], f"{name} overlay changed fields outside the allow-list: {offenders}"


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_rendered_manifest_keeps_probes_and_single_container(name: str) -> None:
    base_spec = _base_manifest()["spec"]["template"]["spec"]
    spec = _rendered(name)["spec"]["template"]["spec"]
    assert len(spec["containers"]) == 1
    for probe in ("livenessProbe", "readinessProbe"):
        assert spec["containers"][0][probe] == base_spec["containers"][0][probe]


@pytest.mark.parametrize("name", _ENVIRONMENT_NAMES)
def test_rendered_secrets_stay_secret_refs(name: str) -> None:
    """Every variable that is a secretKeyRef in the base stays one in every environment."""
    base_secrets = {n for n, e in _env_vars(_base_manifest()).items() if "valueFrom" in e}
    rendered = _env_vars(_rendered(name))
    for var in base_secrets:
        assert "value" not in rendered[var]
        assert rendered[var]["valueFrom"]["secretKeyRef"]["name"]


def test_non_production_environments_use_their_own_secrets() -> None:
    """Sharing prod's secret with dev would let a dev revision read production credentials."""
    production = [_production()]
    base_secrets = {
        n: e["valueFrom"]["secretKeyRef"]["name"]
        for n, e in _env_vars(_base_manifest()).items()
        if "valueFrom" in e
    }
    for env in _REGISTRY.environments.values():
        if env in production:
            continue
        rendered = _env_vars(_rendered(env.name))
        for var, prod_secret in base_secrets.items():
            assert rendered[var]["valueFrom"]["secretKeyRef"]["name"] != prod_secret, (
                f"{env.name} reuses production secret {prod_secret!r} for {var}"
            )


def test_production_render_is_the_base_manifest() -> None:
    """Backwards compatibility: the release deploy is unchanged apart from the image."""
    diff = list(_diff_paths(_base_manifest(), _rendered(_production().name)))
    assert diff == [_IMAGE_PATH]


def test_each_environment_has_its_own_image_repository() -> None:
    """Security review finding: a shared repository lets a dev deployer write prod's image."""
    repositories = [_REGISTRY.repository_for(e) for e in _REGISTRY.environments.values()]
    assert len(repositories) == len(set(repositories)), repositories


def test_non_production_environments_have_their_own_runtime_identity() -> None:
    """Architect review finding: without one, dev/qa run as production's default identity."""
    accounts = []
    for env in _REGISTRY.environments.values():
        if env == _production():
            continue
        account = _rendered(env.name)["spec"]["template"]["spec"].get("serviceAccountName")
        assert account, f"{env.name} sets no runtime service_account"
        assert TEST_PROJECT_ID in account, f"{env.name}: project id must be a placeholder"
        accounts.append(account)
    assert len(accounts) == len(set(accounts)), accounts


def test_registry_hard_codes_no_project_id() -> None:
    """Project ids reach overlays only through ${PROJECT_ID}, never as literals."""
    raw = _REGISTRY_PATH.read_text(encoding="utf-8")
    for env in _REGISTRY.environments.values():
        account = env.overrides.service_account
        if account is not None:
            assert f"${{{PROJECT_ID_PLACEHOLDER_VAR}}}" in account
    assert TEST_PROJECT_ID not in raw


def test_render_placeholders_are_the_ones_make_supplies() -> None:
    """Every ${NAME} the registry uses must be in DEPLOY_RENDER_ENV_VARS, or render fails."""
    supplied = set(_makefile.variable("DEPLOY_RENDER_ENV_VARS").split())
    assert PROJECT_ID_PLACEHOLDER_VAR in supplied
    for env in _REGISTRY.environments.values():
        deploy_env.render_manifest(_base_manifest(), env, _TEST_IMAGE, dict.fromkeys(supplied, "x"))


def test_allow_list_detects_a_disallowed_change() -> None:
    """Guard the guard: a change outside the allow-list must be reported."""
    rendered = _rendered(_ENVIRONMENT_NAMES[0])
    rendered["spec"]["template"]["spec"]["timeoutSeconds"] = 1
    offenders = [
        path
        for path in _diff_paths(_base_manifest(), rendered)
        if not any(path[: len(prefix)] == prefix for prefix in _ALLOWED_DIFF_PREFIXES)
    ]
    assert offenders == [("spec", "template", "spec", "timeoutSeconds")]
