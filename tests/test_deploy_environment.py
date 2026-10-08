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
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._script_loader import load_script_module

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_REGISTRY = _REPO_ROOT / "deploy" / "environments.yaml"
_REAL_BASE_MANIFEST = _REPO_ROOT / "deploy" / "service.yaml"

_TEST_IMAGE = "us-docker.pkg.dev/test-project/repo/svc:0123abcd"
_TEST_REMOTE = "origin"
_TEST_BRANCH = "integration"
_TEST_TAG_BRANCH = "release-line"
_TEST_SERVICE_ACCOUNT = "runtime@test-project.iam.gserviceaccount.com"
_SECRET_VAR = "MANGOMAS_LLM__API_KEY"  # noqa: S105 — env var name, not a secret
_PLAIN_VAR = "MANGOMAS_ENV"


# ── fixtures / builders ────────────────────────────────────────────────────


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
    return yaml.safe_load(_REAL_BASE_MANIFEST.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


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


def test_render_production_is_the_base_manifest_plus_image() -> None:
    """Backwards compatibility: prod renders to exactly the pre-ADR-0036 deploy."""
    registry = deploy_env.load_registry(_REAL_REGISTRY)
    base = _base_manifest()
    rendered = deploy_env.render_manifest(base, registry.environments["prod"], _TEST_IMAGE)
    expected = _base_manifest()
    expected["spec"]["template"]["spec"]["containers"][0]["image"] = _TEST_IMAGE
    assert rendered == expected


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
    env = _environment({"annotations": {"k": "v"}})
    rendered = deploy_env.render_manifest(base, env, _TEST_IMAGE)
    assert rendered["metadata"] == {"name": "svc-x", "labels": {"app": "svc-x"}}
    assert rendered["spec"]["template"]["metadata"]["annotations"] == {"k": "v"}


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
    out = tmp_path / "rendered.yaml"
    args = ["render", "--environment", "dev", "--image", _TEST_IMAGE, "--output", str(out)]
    assert deploy_env.main(["--registry", str(_REAL_REGISTRY), *args]) == deploy_env.EXIT_OK
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["metadata"]["name"] == "mangomas-dev"


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
