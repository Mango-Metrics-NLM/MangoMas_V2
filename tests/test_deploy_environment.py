"""Unit tests for ``scripts/deploy_environment.py`` (ADR-0036).

The script decides which Cloud Run service a workflow event deploys to and
renders that service's manifest, so its two failure directions are both
costly: refusing a legitimate deploy stalls a release, and accepting an
illegitimate one ships unreviewed code. Each refusal path is exercised here
against a real throwaway git repository where git semantics matter (tag
ancestry), and against a fake runner only for failures git cannot be coaxed
into producing on demand.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._script_loader import load_script_module
from tests.constants.deploy import (
    ENV_LABEL_VAR,
    LLM_API_KEY_VAR,
    TEST_IMAGE_REF,
    TEST_PROJECT_ID,
)
from tests.deploy import _manifest

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_REGISTRY = _REPO_ROOT / "deploy" / "environments.yaml"
_REAL_BASE_MANIFEST = _manifest.SERVICE_YAML

_TEST_IMAGE = TEST_IMAGE_REF
_TEST_REMOTE = deploy_env.DEFAULT_REMOTE
_TEST_BRANCH = "integration"
_TEST_TAG_BRANCH = "release-line"
_TEST_SERVICE_ACCOUNT = "runtime@test-project.iam.gserviceaccount.com"
_SECRET_VAR = LLM_API_KEY_VAR
_PLAIN_VAR = ENV_LABEL_VAR
_PLACEHOLDER_VAR = "TEST_DEPLOY_PLACEHOLDER"
_PLACEHOLDER = "${" + _PLACEHOLDER_VAR + "}"
# An annotation key an overlay is allowed to set (the scaling prefix).
_SCALE_KEY = deploy_env._ANNOTATION_KEY_PREFIXES[0] + "maxScale"

# Process state main() reads or mutates; cleared/restored around every test so
# no test inherits another's GitHub context or root-logger configuration.
_SCRIPT_ENV_VARS = (
    deploy_env.GITHUB_REF_ENV,
    deploy_env.GITHUB_EVENT_NAME_ENV,
    deploy_env.GITHUB_OUTPUT_ENV,
    deploy_env.GITHUB_ACTIONS_ENV,
    deploy_env.RUNNER_DEBUG_ENV,
    deploy_env.REQUESTED_ENVIRONMENT_ENV,
    _PLACEHOLDER_VAR,
)
# Variables that would point git at a repository other than the temp one, or
# pull in the developer's own config (hooks, templates, signing).
_GIT_REDIRECT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES")


# ── fixtures / builders ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _SCRIPT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    yield
    root.handlers[:] = saved[1]
    root.setLevel(saved[0])


def _registry_doc(**environments: dict[str, Any]) -> dict[str, Any]:
    """A minimal valid registry document; *environments* replace the default set."""
    envs = environments or {
        "dev": {"trigger": {"branch": _TEST_BRANCH}, "service": "svc-dev"},
        "prod": {
            "trigger": {"tag_pattern": "v*", "ancestor_branch": _TEST_TAG_BRANCH},
            "service": "svc",
        },
    }
    return {
        "schema_version": deploy_env.SUPPORTED_SCHEMA_VERSION,
        "defaults": {
            "region": "test-region",
            "repository": "test-repo",
            "image": "test-image",
            "base_manifest": "m.yaml",
        },
        "environments": envs,
    }


def _registry(**environments: dict[str, Any]) -> Any:
    return deploy_env.parse_registry(_registry_doc(**environments))


def _base_manifest() -> dict[str, Any]:
    return _manifest.base_manifest(_REAL_BASE_MANIFEST)


def _environment(overrides: dict[str, Any] | None = None, service: str = "svc-x") -> Any:
    registry = _registry(
        x={"trigger": {"branch": _TEST_BRANCH}, "service": service, "overrides": overrides or {}}
    )
    return registry.environments["x"]


def _fake_git(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    def run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["git", *args], returncode, stdout, stderr)

    return run


def _git(cwd: Path, *args: str) -> str:
    # Fixed argv built by this file, against a tmp repo it created.
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo where ``origin/<_TEST_TAG_BRANCH>`` holds one commit and ``side`` another.

    The remote-tracking ref is written with ``update-ref`` so no network or
    second repository is needed; ``merge-base --is-ancestor`` only reads refs.
    """
    for name in _GIT_REDIRECT_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "on release line")
    _git(repo, "update-ref", f"refs/remotes/{_TEST_REMOTE}/{_TEST_TAG_BRANCH}", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "side only")
    monkeypatch.chdir(repo)
    return repo


# ── the real registry ──────────────────────────────────────────────────────


def test_real_registry_parses() -> None:
    registry = deploy_env.load_registry(_REAL_REGISTRY)
    assert registry.environments, "deploy/environments.yaml declares no environments"
    assert registry.base_manifest == _REAL_BASE_MANIFEST.relative_to(_REPO_ROOT)


# ── registry validation ────────────────────────────────────────────────────


def _mutated(path: Sequence[str], value: Any) -> dict[str, Any]:
    doc = _registry_doc()
    target: Any = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return doc


@pytest.mark.parametrize(
    ("document", "message"),
    [
        pytest.param([], "expected a mapping", id="not-a-mapping"),
        pytest.param(_mutated(["surprise"], 1), "unknown key", id="unknown-top-key"),
        pytest.param(_mutated(["schema_version"], 99), "unsupported", id="schema-version"),
        pytest.param(_mutated(["defaults", "zone"], "z"), "unknown key", id="unknown-default"),
        pytest.param(_mutated(["defaults", "region"], ""), "non-empty string", id="empty-region"),
        pytest.param(_mutated(["environments"], {}), "at least one", id="no-environments"),
        pytest.param(
            _mutated(["environments", "Bad_Name"], {}), "name must match", id="bad-env-name"
        ),
        pytest.param(
            _mutated(["environments", "dev", "extra"], 1), "unknown key", id="unknown-env-key"
        ),
        pytest.param(
            _mutated(["environments", "dev", "service"], "Not_Valid"),
            "not a valid Cloud Run name",
            id="bad-service-name",
        ),
        pytest.param(
            _mutated(["environments", "dev", "trigger"], {}), "exactly one", id="no-trigger"
        ),
        pytest.param(
            _mutated(["environments", "dev", "trigger"], {"branch": "a", "tag_pattern": "v*"}),
            "exactly one",
            id="two-triggers",
        ),
        pytest.param(
            _mutated(["environments", "dev", "trigger", "ancestor_branch"], "main"),
            "applies only to 'tag_pattern'",
            id="ancestor-on-branch",
        ),
        pytest.param(
            _mutated(["environments", "dev", "trigger", "cron"], "x"),
            "unknown key",
            id="unknown-trigger-key",
        ),
        pytest.param(
            _mutated(["environments", "dev", "overrides"], {"volumes": {}}),
            "unknown key",
            id="unknown-overlay-key",
        ),
        pytest.param(
            _mutated(["environments", "dev", "overrides"], {"env": {_PLAIN_VAR: 1}}),
            "non-empty string",
            id="non-string-env-value",
        ),
        pytest.param(
            _mutated(["environments", "dev", "overrides"], {"service_account": ""}),
            "non-empty string",
            id="empty-service-account",
        ),
        pytest.param(
            _mutated(["environments", "prod", "service"], "svc-dev"),
            "service names must be unique",
            id="duplicate-service",
        ),
        pytest.param(
            _mutated(["environments", "prod", "trigger"], {"branch": _TEST_BRANCH}),
            "branch triggers must be unique",
            id="duplicate-branch",
        ),
    ],
)
def test_parse_registry_rejects(document: Any, message: str) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match=message):
        deploy_env.parse_registry(document)


def test_load_registry_reports_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="cannot read"):
        deploy_env.load_registry(tmp_path / "missing.yaml")


def test_load_registry_reports_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("environments: [unclosed", encoding="utf-8")
    with pytest.raises(deploy_env.DeployConfigError, match="malformed YAML"):
        deploy_env.load_registry(path)


def test_branch_triggers_lists_only_branch_environments() -> None:
    assert _registry().branch_triggers() == [_TEST_BRANCH]


# ── trigger matching / resolution ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("trigger", "ref", "expected"),
    [
        ({"branch": "dev"}, "refs/heads/dev", True),
        ({"branch": "dev"}, "refs/heads/dev-2", False),
        ({"branch": "dev"}, "refs/tags/dev", False),
        ({"tag_pattern": "v*"}, "refs/tags/v1.2.3", True),
        ({"tag_pattern": "v*"}, "refs/tags/release-1", False),
        ({"tag_pattern": "v*"}, "refs/heads/v1", False),
    ],
)
def test_trigger_matches(trigger: dict[str, str], ref: str, expected: bool) -> None:
    assert deploy_env.Trigger(**trigger).matches(ref) is expected


def test_resolve_by_branch() -> None:
    env = deploy_env.resolve_environment(_registry(), f"refs/heads/{_TEST_BRANCH}", None)
    assert env.name == "dev"


def test_resolve_refuses_unowned_ref() -> None:
    with pytest.raises(deploy_env.DeployRefused, match="no environment deploys"):
        deploy_env.resolve_environment(_registry(), "refs/heads/feature/x", None)


def test_resolve_accepts_requested_environment_that_owns_the_ref() -> None:
    env = deploy_env.resolve_environment(_registry(), "refs/tags/v2.0.0", "prod")
    assert env.name == "prod"


def test_resolve_refuses_requested_environment_that_does_not_own_the_ref() -> None:
    """A manual dispatch must not push a feature branch to production."""
    with pytest.raises(deploy_env.DeployRefused, match="does not deploy from"):
        deploy_env.resolve_environment(_registry(), f"refs/heads/{_TEST_BRANCH}", "prod")


def test_resolve_refuses_unknown_requested_environment() -> None:
    with pytest.raises(deploy_env.DeployRefused, match="not one of"):
        deploy_env.resolve_environment(_registry(), f"refs/heads/{_TEST_BRANCH}", "staging")


def test_resolve_rejects_ambiguous_tag_patterns() -> None:
    registry = _registry(
        a={"trigger": {"tag_pattern": "v*"}, "service": "svc-a"},
        b={"trigger": {"tag_pattern": "v1*"}, "service": "svc-b"},
    )
    with pytest.raises(deploy_env.DeployConfigError, match="several environments"):
        deploy_env.resolve_environment(registry, "refs/tags/v1.0", None)


# ── ancestry (real git) ────────────────────────────────────────────────────


def test_ancestry_accepts_commit_on_the_ancestor_branch(git_repo: Path) -> None:
    _git(git_repo, "checkout", "-q", f"{_TEST_REMOTE}/{_TEST_TAG_BRANCH}")
    deploy_env.verify_ancestry(_registry().environments["prod"], remote=_TEST_REMOTE)


@pytest.mark.usefixtures("git_repo")
def test_ancestry_refuses_commit_off_the_ancestor_branch() -> None:
    with pytest.raises(deploy_env.DeployRefused, match="not reachable"):
        deploy_env.verify_ancestry(_registry().environments["prod"], remote=_TEST_REMOTE)


@pytest.mark.usefixtures("git_repo")
def test_ancestry_reports_missing_ancestor_ref() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="ancestry check"):
        deploy_env.verify_ancestry(_registry().environments["prod"], remote="nonexistent")


def test_ancestry_skipped_for_branch_triggers() -> None:
    def explode(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"git must not run for a branch trigger: {args}")

    deploy_env.verify_ancestry(
        _registry().environments["dev"], remote=_TEST_REMOTE, run_git=explode
    )


def test_content_image_tag_is_the_tree_hash(git_repo: Path) -> None:
    assert deploy_env.content_image_tag() == _git(git_repo, "rev-parse", "HEAD^{tree}")


def test_content_image_tag_ignores_commit_identity(git_repo: Path) -> None:
    """The promotion property: same tree, different commit → same image tag."""
    before = deploy_env.content_image_tag()
    _git(git_repo, "commit", "-q", "--allow-empty", "-m", "promotion merge stand-in")
    assert deploy_env.content_image_tag() == before


def test_git_failure_is_a_config_error() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="boom"):
        deploy_env.content_image_tag(_fake_git(128, stderr="boom"))


def test_git_failure_without_stderr_is_still_reported() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="no stderr"):
        deploy_env.content_image_tag(_fake_git(128))


def test_missing_git_executable_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(deploy_env.subprocess, "run", missing)
    with pytest.raises(deploy_env.DeployConfigError, match="failed to start"):
        deploy_env.content_image_tag()


# ── plan / outputs ─────────────────────────────────────────────────────────


def test_build_plan_for_a_release_tag(git_repo: Path) -> None:
    _git(git_repo, "checkout", "-q", f"{_TEST_REMOTE}/{_TEST_TAG_BRANCH}")
    plan = deploy_env.build_plan(
        _registry(), ref="refs/tags/v3.1.0", requested=None, remote=_TEST_REMOTE
    )
    assert plan == {
        "environment": "prod",
        "service": "svc",
        "region": "test-region",
        "repository": "test-repo",
        "image": "test-image",
        "image_tag": _git(git_repo, "rev-parse", "HEAD^{tree}"),
        "release_tag": "v3.1.0",
    }


def test_build_plan_for_a_branch_has_no_release_tag() -> None:
    plan = deploy_env.build_plan(
        _registry(),
        ref=f"refs/heads/{_TEST_BRANCH}",
        requested=None,
        remote=_TEST_REMOTE,
        run_git=_fake_git(0, stdout="tree123\n"),
    )
    assert plan["release_tag"] == ""
    assert plan["image_tag"] == "tree123"


def test_write_outputs_appends_to_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.write_text("existing=1\n", encoding="utf-8")
    deploy_env.write_outputs({"a": "1", "b_c": ""}, out)
    assert out.read_text(encoding="utf-8") == "existing=1\na=1\nb_c=\n"


def test_write_outputs_prints_without_destination(capsys: pytest.CaptureFixture[str]) -> None:
    deploy_env.write_outputs({"a": "1"}, None)
    assert capsys.readouterr().out == "a=1\n"


@pytest.mark.parametrize(
    "outputs",
    [{"a": "1\nforged=1"}, {"a": "1\rforged"}, {"Bad-Key": "1"}],
    ids=["newline", "carriage-return", "bad-key"],
)
def test_write_outputs_refuses_injection(outputs: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="unsafe output"):
        deploy_env.write_outputs(outputs, tmp_path / "out")


# ── render ─────────────────────────────────────────────────────────────────


def test_render_does_not_mutate_the_base() -> None:
    base = _base_manifest()
    deploy_env.render_manifest(base, _environment({"env": {_PLAIN_VAR: "dev"}}), _TEST_IMAGE)
    assert base == _base_manifest()


def test_render_applies_every_overlay_field() -> None:
    env = _environment(
        {
            "env": {_PLAIN_VAR: "dev", "MANGOMAS_LOG_LEVEL": "DEBUG"},
            "secrets": {_SECRET_VAR: "secret-x"},
            "annotations": {"autoscaling.knative.dev/maxScale": "2"},
            "service_account": _TEST_SERVICE_ACCOUNT,
        },
        service="svc-x",
    )
    rendered = deploy_env.render_manifest(_base_manifest(), env, _TEST_IMAGE)
    template = rendered["spec"]["template"]
    container = template["spec"]["containers"][0]
    env_vars = {e["name"]: e for e in container["env"]}
    assert rendered["metadata"]["name"] == "svc-x"
    assert rendered["metadata"]["labels"]["app"] == "svc-x"
    assert template["metadata"]["annotations"]["autoscaling.knative.dev/maxScale"] == "2"
    assert template["spec"]["serviceAccountName"] == _TEST_SERVICE_ACCOUNT
    assert env_vars[_PLAIN_VAR]["value"] == "dev"
    assert env_vars["MANGOMAS_LOG_LEVEL"]["value"] == "DEBUG"
    assert env_vars[_SECRET_VAR]["valueFrom"]["secretKeyRef"]["name"] == "secret-x"
    assert container["image"] == _TEST_IMAGE


def test_render_creates_missing_metadata_and_annotations() -> None:
    base = _base_manifest()
    del base["metadata"]
    del base["spec"]["template"]["metadata"]
    env = _environment({"annotations": {_SCALE_KEY: "v"}})
    rendered = deploy_env.render_manifest(base, env, _TEST_IMAGE)
    assert rendered["metadata"] == {"name": "svc-x", "labels": {"app": "svc-x"}}
    assert rendered["spec"]["template"]["metadata"]["annotations"] == {_SCALE_KEY: "v"}


def test_render_refuses_plain_value_over_a_secret() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="secretKeyRef in the base"):
        deploy_env.render_manifest(
            _base_manifest(), _environment({"env": {_SECRET_VAR: "leaked"}}), _TEST_IMAGE
        )


@pytest.mark.parametrize("name", [_PLAIN_VAR, "MANGOMAS_NOT_IN_BASE"])
def test_render_refuses_secret_override_without_a_base_secret(name: str) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="no secretKeyRef"):
        deploy_env.render_manifest(
            _base_manifest(), _environment({"secrets": {name: "s"}}), _TEST_IMAGE
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda m: m["spec"]["template"]["spec"].__setitem__("containers", []), "exactly one"),
        (lambda m: m["spec"].__delitem__("template"), "no spec.template"),
    ],
    ids=["no-containers", "no-template"],
)
def test_render_rejects_unusable_manifests(mutate: Any, message: str) -> None:
    base = _base_manifest()
    mutate(base)
    with pytest.raises(deploy_env.DeployConfigError, match=message):
        deploy_env.render_manifest(base, _environment(), _TEST_IMAGE)


def test_render_logs_each_override_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="deploy_environment")
    deploy_env.render_manifest(
        _base_manifest(), _environment({"env": {_PLAIN_VAR: "dev"}}), _TEST_IMAGE
    )
    assert any(f"env {_PLAIN_VAR}" in r.getMessage() for r in caplog.records)


# ── CLI ────────────────────────────────────────────────────────────────────


def _write_registry(tmp_path: Path) -> Path:
    path = tmp_path / "environments.yaml"
    path.write_text(yaml.safe_dump(_registry_doc()), encoding="utf-8")
    return path


@pytest.mark.usefixtures("git_repo")
def test_main_plan_writes_outputs(tmp_path: Path) -> None:
    out = tmp_path / "github_output"
    code = deploy_env.main(
        [
            "--registry",
            str(_write_registry(tmp_path)),
            "plan",
            "--ref",
            f"refs/heads/{_TEST_BRANCH}",
            "--github-output",
            str(out),
        ]
    )
    assert code == deploy_env.EXIT_OK
    assert "environment=dev\n" in out.read_text(encoding="utf-8")


def test_main_plan_refusal_exit_code(tmp_path: Path) -> None:
    code = deploy_env.main(
        ["--registry", str(_write_registry(tmp_path)), "plan", "--ref", "refs/heads/nope"]
    )
    assert code == deploy_env.EXIT_REFUSED


def test_main_plan_requires_a_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(deploy_env.GITHUB_REF_ENV, raising=False)
    code = deploy_env.main(["--registry", str(_write_registry(tmp_path)), "plan", "--ref", ""])
    assert code == deploy_env.EXIT_CONFIG_ERROR


@pytest.mark.usefixtures("git_repo")
def test_main_plan_reads_github_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defaults come from the variables GitHub sets, so the workflow passes no flags."""
    out = tmp_path / "github_output"
    monkeypatch.setenv(deploy_env.GITHUB_REF_ENV, f"refs/heads/{_TEST_BRANCH}")
    monkeypatch.setenv(deploy_env.GITHUB_EVENT_NAME_ENV, "push")
    monkeypatch.setenv(deploy_env.REQUESTED_ENVIRONMENT_ENV, "dev")
    monkeypatch.setenv(deploy_env.GITHUB_OUTPUT_ENV, str(out))
    code = deploy_env.main(["--registry", str(_write_registry(tmp_path)), "plan"])
    assert code == deploy_env.EXIT_OK
    assert "service=svc-dev\n" in out.read_text(encoding="utf-8")


def test_main_render_writes_manifest(tmp_path: Path) -> None:
    out = tmp_path / "rendered.yaml"
    code = deploy_env.main(
        [
            "--registry",
            str(_REAL_REGISTRY),
            "render",
            "--environment",
            "prod",
            "--image",
            _TEST_IMAGE,
            "--output",
            str(out),
            "--base-manifest",
            str(_REAL_BASE_MANIFEST),
        ]
    )
    assert code == deploy_env.EXIT_OK
    rendered = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert rendered["spec"]["template"]["spec"]["containers"][0]["image"] == _TEST_IMAGE


def test_main_render_uses_registry_base_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_REPO_ROOT)
    monkeypatch.setenv("PROJECT_ID", TEST_PROJECT_ID)
    out = tmp_path / "rendered.yaml"
    args = [
        "render",
        "--environment",
        "dev",
        "--image",
        _TEST_IMAGE,
        "--output",
        str(out),
        "--substitute-from-env",
        "PROJECT_ID",
    ]
    assert deploy_env.main(["--registry", str(_REAL_REGISTRY), *args]) == deploy_env.EXIT_OK
    expected = deploy_env.load_registry(_REAL_REGISTRY).environments["dev"].service
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["metadata"]["name"] == expected


def test_main_render_unknown_environment(tmp_path: Path) -> None:
    code = deploy_env.main(
        [
            "--registry",
            str(_REAL_REGISTRY),
            "render",
            "--environment",
            "staging",
            "--image",
            _TEST_IMAGE,
            "--output",
            str(tmp_path / "o.yaml"),
        ]
    )
    assert code == deploy_env.EXIT_CONFIG_ERROR


def test_runner_debug_enables_debug_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(deploy_env.RUNNER_DEBUG_ENV, "1")
    deploy_env.main(["--registry", str(_write_registry(tmp_path)), "plan", "--ref", "refs/x"])
    assert logging.getLogger().level == logging.DEBUG


def test_failure_emits_github_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(deploy_env.GITHUB_ACTIONS_ENV, "true")
    deploy_env.main(["--registry", str(_write_registry(tmp_path)), "plan", "--ref", "refs/x"])
    assert "::error title=Deploy refused::no environment deploys from refs/x" in (
        capsys.readouterr().out
    )


def test_failure_without_actions_emits_no_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(deploy_env.GITHUB_ACTIONS_ENV, raising=False)
    deploy_env.main(["--registry", str(_write_registry(tmp_path)), "plan", "--ref", "refs/x"])
    assert "::error" not in capsys.readouterr().out


# ── review follow-ups: repositories, placeholders, self-check, hardening ───


def test_environment_repository_overrides_the_default() -> None:
    registry = _registry(
        dev={"trigger": {"branch": _TEST_BRANCH}, "service": "svc-dev", "repository": "repo-dev"},
        prod={"trigger": {"tag_pattern": "v*"}, "service": "svc"},
    )
    assert registry.repository_for(registry.environments["dev"]) == "repo-dev"
    assert registry.repository_for(registry.environments["prod"]) == "test-repo"


def test_plan_reports_the_environment_repository() -> None:
    registry = _registry(
        dev={"trigger": {"branch": _TEST_BRANCH}, "service": "svc-dev", "repository": "repo-dev"},
    )
    plan = deploy_env.build_plan(
        registry,
        ref=f"refs/heads/{_TEST_BRANCH}",
        requested=None,
        remote=_TEST_REMOTE,
        run_git=_fake_git(0, stdout="tree\n"),
    )
    assert plan["repository"] == "repo-dev"


def test_empty_repository_override_is_rejected() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="non-empty string"):
        _registry(x={"trigger": {"branch": "b"}, "service": "svc-x", "repository": ""})


def test_placeholders_fill_every_overlay_field() -> None:
    env = _environment(
        {
            "env": {_PLAIN_VAR: f"{_PLACEHOLDER}-env"},
            "secrets": {_SECRET_VAR: f"secret-{_PLACEHOLDER}"},
            "annotations": {_SCALE_KEY: f"{_PLACEHOLDER}"},
            "service_account": f"sa@{_PLACEHOLDER}.iam.gserviceaccount.com",
        }
    )
    rendered = deploy_env.render_manifest(
        _base_manifest(), env, _TEST_IMAGE, {_PLACEHOLDER_VAR: "p1"}
    )
    template = rendered["spec"]["template"]
    env_vars = {e["name"]: e for e in template["spec"]["containers"][0]["env"]}
    assert env_vars[_PLAIN_VAR]["value"] == "p1-env"
    assert env_vars[_SECRET_VAR]["valueFrom"]["secretKeyRef"]["name"] == "secret-p1"
    assert template["metadata"]["annotations"][_SCALE_KEY] == "p1"
    assert template["spec"]["serviceAccountName"] == "sa@p1.iam.gserviceaccount.com"


def test_double_dollar_is_a_literal_dollar() -> None:
    env = _environment({"env": {_PLAIN_VAR: "cost$$5"}})
    rendered = deploy_env.render_manifest(_base_manifest(), env, _TEST_IMAGE)
    env_vars = {e["name"]: e for e in rendered["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env_vars[_PLAIN_VAR]["value"] == "cost$5"


def test_unfilled_placeholder_is_an_error() -> None:
    env = _environment({"service_account": f"sa@{_PLACEHOLDER}"})
    with pytest.raises(deploy_env.DeployConfigError, match="has no value"):
        deploy_env.render_manifest(_base_manifest(), env, _TEST_IMAGE)


def test_malformed_placeholder_is_an_error() -> None:
    env = _environment({"env": {_PLAIN_VAR: "${unclosed"}})
    with pytest.raises(deploy_env.DeployConfigError, match="malformed placeholder"):
        deploy_env.render_manifest(_base_manifest(), env, _TEST_IMAGE)


def test_substitution_values_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_PLACEHOLDER_VAR, "from-env")
    registry_path = tmp_path / "environments.yaml"
    doc = _registry_doc(
        dev={
            "trigger": {"branch": _TEST_BRANCH},
            "service": "svc-dev",
            "overrides": {"service_account": f"sa@{_PLACEHOLDER}"},
        }
    )
    registry_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    out = tmp_path / "rendered.yaml"
    code = deploy_env.main(
        [
            "--registry",
            str(registry_path),
            "render",
            "--environment",
            "dev",
            "--image",
            _TEST_IMAGE,
            "--output",
            str(out),
            "--base-manifest",
            str(_REAL_BASE_MANIFEST),
            "--substitute-from-env",
            _PLACEHOLDER_VAR,
        ]
    )
    assert code == deploy_env.EXIT_OK
    rendered = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert rendered["spec"]["template"]["spec"]["serviceAccountName"] == "sa@from-env"


def test_unset_substitution_variable_is_an_error() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="unset environment variable"):
        deploy_env._variables_from_env([_PLACEHOLDER_VAR])


def test_verify_rendered_accepts_the_written_image(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    manifest = deploy_env.render_manifest(_base_manifest(), _environment(), _TEST_IMAGE)
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    deploy_env.verify_rendered(path, _TEST_IMAGE)


def test_verify_rendered_refuses_a_different_image(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(_base_manifest()), encoding="utf-8")
    with pytest.raises(deploy_env.DeployConfigError, match="expected"):
        deploy_env.verify_rendered(path, _TEST_IMAGE)


def test_secret_override_on_non_mapping_value_from_is_a_config_error() -> None:
    base = _base_manifest()
    container = base["spec"]["template"]["spec"]["containers"][0]
    container["env"].append({"name": "MANGOMAS_ODD", "valueFrom": "not-a-mapping"})
    with pytest.raises(deploy_env.DeployConfigError, match="no secretKeyRef"):
        deploy_env.render_manifest(
            base, _environment({"secrets": {"MANGOMAS_ODD": "s"}}), _TEST_IMAGE
        )


def test_annotation_escapes_newlines_so_no_command_is_forged(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message carrying ``\n::warning::`` must stay one annotation line."""
    monkeypatch.setenv(deploy_env.GITHUB_ACTIONS_ENV, "true")
    deploy_env._report(deploy_env.DeployRefused("bad\n::warning::forged 100%"), "Deploy refused")
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["::error title=Deploy refused::bad%0A::warning::forged 100%25"]


# ── hygiene-review follow-ups: closed schema, shapes, tags, writes ─────────


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"env": {"BAD NAME": "x"}}, "not a valid environment", id="env-name"),
        pytest.param({"env": {"PORT": "9"}}, "reserved by Cloud Run", id="reserved-env"),
        pytest.param({"secrets": {"K_SERVICE": "s"}}, "reserved by Cloud Run", id="reserved-sec"),
        pytest.param(
            {"annotations": {"run.googleapis.com/vpc-access-egress": "all"}},
            "not overridable",
            id="security-annotation",
        ),
        pytest.param({"env": {_PLAIN_VAR: True}}, "quote it in YAML", id="yaml-bool"),
        pytest.param({"env": {_PLAIN_VAR: 0}}, "quote it in YAML", id="yaml-int"),
    ],
)
def test_overlay_schema_rejects(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match=message):
        _environment(overrides)


@pytest.mark.parametrize("section", ["env", "secrets", "annotations"])
def test_empty_overlay_section_is_treated_as_omitted(section: str) -> None:
    """``env:`` with nothing under it parses as None — same as ``overrides:``."""
    assert getattr(_environment({section: None}).overrides, section) == {}


def test_ambiguous_ref_is_refused_even_when_an_environment_is_requested() -> None:
    registry = _registry(
        a={"trigger": {"tag_pattern": "v*"}, "service": "svc-a"},
        b={"trigger": {"tag_pattern": "v1*"}, "service": "svc-b"},
    )
    with pytest.raises(deploy_env.DeployConfigError, match="several environments"):
        deploy_env.resolve_environment(registry, "refs/tags/v1.0", "a")


@pytest.mark.parametrize("tag", ["v1.0.0+build", "v1/nested", "v" + "1" * 128])
def test_release_name_that_is_not_an_image_tag_is_refused(tag: str) -> None:
    """Refused at plan time — not after the image is pushed and the alias tag fails."""
    with pytest.raises(deploy_env.DeployRefused, match="not a valid image tag"):
        deploy_env.build_plan(
            _registry(prod={"trigger": {"tag_pattern": "v*"}, "service": "svc"}),
            ref=f"refs/tags/{tag}",
            requested=None,
            remote=_TEST_REMOTE,
            run_git=_fake_git(0, stdout="tree"),
        )


def test_ancestry_check_uses_the_fully_qualified_remote_ref() -> None:
    seen: list[Sequence[str]] = []

    def record(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    deploy_env.verify_ancestry(_registry().environments["prod"], remote="up", run_git=record)
    assert seen == [["merge-base", "--is-ancestor", "HEAD", f"refs/remotes/up/{_TEST_TAG_BRANCH}"]]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.__setitem__("env", "not-a-list"), "must be a list"),
        (lambda c: c["env"].append("not-a-mapping"), "expected a mapping"),
        (lambda c: c["env"].append(dict(c["env"][0])), "more than once"),
    ],
    ids=["env-not-list", "env-entry-not-mapping", "duplicate-env-name"],
)
def test_malformed_base_env_is_a_config_error(mutate: Any, message: str) -> None:
    base = _base_manifest()
    mutate(base["spec"]["template"]["spec"]["containers"][0])
    with pytest.raises(deploy_env.DeployConfigError, match=message):
        deploy_env.render_manifest(base, _environment({"env": {_PLAIN_VAR: "x"}}), _TEST_IMAGE)


def test_null_env_and_null_annotations_are_created() -> None:
    base = _base_manifest()
    base["spec"]["template"]["spec"]["containers"][0]["env"] = None
    base["spec"]["template"]["metadata"]["annotations"] = None
    env = _environment({"env": {_PLAIN_VAR: "x"}, "annotations": {_SCALE_KEY: "1"}})
    rendered = deploy_env.render_manifest(base, env, _TEST_IMAGE)
    assert rendered["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {"name": _PLAIN_VAR, "value": "x"}
    ]
    assert rendered["spec"]["template"]["metadata"]["annotations"] == {_SCALE_KEY: "1"}


def test_unwritable_github_output_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="cannot write outputs"):
        deploy_env.write_outputs({"a": "1"}, tmp_path / "missing-dir" / "out")


def test_unwritable_render_output_exits_with_config_error(tmp_path: Path) -> None:
    code = deploy_env.main(
        [
            "--registry",
            str(_REAL_REGISTRY),
            "render",
            "--environment",
            "prod",
            "--image",
            _TEST_IMAGE,
            "--output",
            str(tmp_path / "missing-dir" / "out.yaml"),
            "--base-manifest",
            str(_REAL_BASE_MANIFEST),
        ]
    )
    assert code == deploy_env.EXIT_CONFIG_ERROR


# ── validate subcommand (wired into `make validate-config`) ─────────────────


def test_placeholder_names_cover_every_overlay_field() -> None:
    registry = _registry(
        x={
            "trigger": {"branch": _TEST_BRANCH},
            "service": "svc-x",
            "overrides": {
                "env": {_PLAIN_VAR: "${A}"},
                "secrets": {_SECRET_VAR: "s-${B}"},
                "annotations": {_SCALE_KEY: "${C}"},
                "service_account": "sa@${D}",
            },
        }
    )
    assert deploy_env.placeholder_names(registry) == {"A", "B", "C", "D"}


def test_validate_registry_renders_every_environment() -> None:
    registry = deploy_env.load_registry(_REAL_REGISTRY)
    assert deploy_env.validate_registry(registry, _base_manifest()) == list(registry.environments)


def test_validate_registry_reports_an_environment_that_cannot_render() -> None:
    """A secret override with no base secretKeyRef only fails at render — validate finds it."""
    registry = _registry(x={"trigger": {"branch": "b"}, "service": "svc-x"})
    bad = _registry(
        y={
            "trigger": {"branch": "c"},
            "service": "svc-y",
            "overrides": {"secrets": {_PLAIN_VAR: "s"}},
        }
    )
    assert deploy_env.validate_registry(registry, _base_manifest()) == ["x"]
    with pytest.raises(deploy_env.DeployConfigError, match="no secretKeyRef"):
        deploy_env.validate_registry(bad, _base_manifest())


def test_main_validate_exit_codes(tmp_path: Path) -> None:
    ok = deploy_env.main(
        ["--registry", str(_REAL_REGISTRY), "validate", "--base-manifest", str(_REAL_BASE_MANIFEST)]
    )
    assert ok == deploy_env.EXIT_OK
    broken = tmp_path / "environments.yaml"
    broken.write_text("schema_version: 99\n", encoding="utf-8")
    assert deploy_env.main(["--registry", str(broken), "validate"]) == deploy_env.EXIT_CONFIG_ERROR


def test_validate_uses_the_registry_base_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_REPO_ROOT)
    assert deploy_env.main(["--registry", str(_REAL_REGISTRY), "validate"]) == deploy_env.EXIT_OK


# ── scan-refs: branches the nightly security scans check out ────────────────


def test_environment_branches_are_triggers_then_ancestors_without_repeats() -> None:
    registry = _registry(
        dev={"trigger": {"branch": _TEST_BRANCH}, "service": "svc-dev"},
        prod={
            "trigger": {"tag_pattern": "v*", "ancestor_branch": _TEST_TAG_BRANCH},
            "service": "svc",
        },
        hotfix={
            "trigger": {"tag_pattern": "h*", "ancestor_branch": _TEST_TAG_BRANCH},
            "service": "svc-h",
        },
    )
    assert deploy_env.environment_branches(registry) == [_TEST_BRANCH, _TEST_TAG_BRANCH]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), (" TRUE ", True), ("false", False), ("", False)],
)
def test_parse_flag_accepts_booleans(value: str, expected: bool) -> None:
    assert deploy_env.parse_flag(value, "flag") is expected


@pytest.mark.parametrize("value", ["yes", "1", "on", "treu"])
def test_parse_flag_rejects_anything_else(value: str) -> None:
    """A typo in the admin variable must fail the job, not quietly mean "off"."""
    with pytest.raises(deploy_env.DeployConfigError, match="expected true/false"):
        deploy_env.parse_flag(value, "flag")


def test_scan_refs_disabled_scans_the_default_branch_only() -> None:
    def explode(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"git must not run while disabled: {args}")

    refs = deploy_env.scan_refs(_registry(), enabled=False, remote=_TEST_REMOTE, run_git=explode)
    assert refs == [deploy_env.DEFAULT_BRANCH_REF]


@pytest.mark.usefixtures("git_repo")
def test_scan_refs_keeps_existing_branches_and_logs_missing_ones(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real git: the tag branch exists under refs/remotes/origin, the trigger branch does not."""
    caplog.set_level(logging.WARNING, logger="deploy_environment")
    refs = deploy_env.scan_refs(_registry(), enabled=True, remote=_TEST_REMOTE)
    assert refs == [_TEST_TAG_BRANCH]
    assert any(_TEST_BRANCH in r.getMessage() for r in caplog.records)


def test_scan_refs_in_registry_order_when_all_exist(git_repo: Path) -> None:
    _git(git_repo, "update-ref", f"refs/remotes/{_TEST_REMOTE}/{_TEST_BRANCH}", "HEAD")
    refs = deploy_env.scan_refs(_registry(), enabled=True, remote=_TEST_REMOTE)
    assert refs == [_TEST_BRANCH, _TEST_TAG_BRANCH]


@pytest.mark.usefixtures("git_repo")
def test_scan_refs_enabled_with_no_branches_is_a_config_error() -> None:
    """Never a silent empty matrix: on, with nothing to scan, is a failure."""
    with pytest.raises(deploy_env.DeployConfigError, match="none of"):
        deploy_env.scan_refs(_registry(), enabled=True, remote="nonexistent")


def test_scan_refs_git_failure_is_a_config_error() -> None:
    with pytest.raises(deploy_env.DeployConfigError, match="cannot resolve"):
        deploy_env.scan_refs(
            _registry(), enabled=True, remote=_TEST_REMOTE, run_git=_fake_git(128, stderr="bad")
        )


@pytest.mark.usefixtures("git_repo")
def test_main_scan_refs_writes_a_json_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "github_output"
    monkeypatch.setenv(deploy_env.SCAN_ENABLED_ENV, "true")
    code = deploy_env.main(
        ["--registry", str(_write_registry(tmp_path)), "scan-refs", "--github-output", str(out)]
    )
    assert code == deploy_env.EXIT_OK
    assert out.read_text(encoding="utf-8") == f'refs=["{_TEST_TAG_BRANCH}"]\n'


def test_main_scan_refs_disabled_by_default(tmp_path: Path) -> None:
    out = tmp_path / "github_output"
    code = deploy_env.main(
        ["--registry", str(_write_registry(tmp_path)), "scan-refs", "--github-output", str(out)]
    )
    assert code == deploy_env.EXIT_OK
    assert out.read_text(encoding="utf-8") == 'refs=[""]\n'


def test_main_scan_refs_bad_flag_exits_with_config_error(tmp_path: Path) -> None:
    code = deploy_env.main(
        ["--registry", str(_write_registry(tmp_path)), "scan-refs", "--enabled", "maybe"]
    )
    assert code == deploy_env.EXIT_CONFIG_ERROR
