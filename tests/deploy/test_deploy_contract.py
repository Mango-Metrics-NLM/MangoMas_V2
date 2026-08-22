"""Contract tests for the Cloud Run deploy artifacts (Milestone E + spec-0024).

These assert the *shape* of the deploy manifests, their doc-sync with the
settings model, and — since spec-0024 — the workflow↔manifest tie: the deploy
workflow must actually *apply* ``deploy/service.yaml`` (before that, it ran an
image-only ``gcloud run deploy`` and every manifest field — env vars,
secretKeyRefs, probes, limits, autoscaling — was silently dropped) and must
smoke-probe the deployed revision. They make **no live-cloud assertion** —
this repo provisions no GCP resources (ADR-0001).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import BaseModel

from mangomas.config import Settings
from tests.deploy import _workflows

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO_ROOT / "deploy"
_SERVICE_YAML = _DEPLOY / "service.yaml"
_README = _DEPLOY / "README.md"
_DEPLOY_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "deploy.yml"

_HEALTHZ_PATH = "/healthz"
_READYZ_PATH = "/readyz"

# ── spec-0024: the workflow must consume the manifest, not just ship it ───────
# The repo-relative path the workflow's run: bodies must reference; asserted
# equal to the real file below so a rename on either side breaks the tie.
_MANIFEST_WORKFLOW_REF = "deploy/service.yaml"
# The full-manifest application command; its image-only predecessor is banned.
_MANIFEST_APPLY_COMMAND = "gcloud run services replace"
_IMAGE_ONLY_DEPLOY_COMMAND = "gcloud run deploy"
# The smoke probe runs against a private (--no-allow-unauthenticated posture)
# service, so it must authenticate; an anonymous curl would 401/403 and prove
# nothing about health.
_SMOKE_AUTH_COMMAND = "gcloud auth print-identity-token"
# `-f` is what converts a non-2xx probe response into a failing job.
_SMOKE_CURL_COMMAND = "curl -fsS"


def _deploy_run_bodies() -> list[str]:
    """Every ``run:`` body in deploy.yml, via the shared workflow reader."""
    prefix = f"{_DEPLOY_WORKFLOW.name}:"
    return [body for where, body in _workflows.run_bodies() if where.startswith(prefix)]


def _service_container() -> dict[str, Any]:
    doc = yaml.safe_load(_SERVICE_YAML.read_text(encoding="utf-8"))
    assert doc["kind"] == "Service"
    containers = doc["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "expected exactly one container"
    return cast("dict[str, Any]", containers[0])


def test_service_yaml_is_valid_yaml() -> None:
    doc = yaml.safe_load(_SERVICE_YAML.read_text(encoding="utf-8"))
    assert doc["apiVersion"] == "serving.knative.dev/v1"


def test_service_yaml_has_liveness_and_readiness_probes() -> None:
    container = _service_container()
    assert container["livenessProbe"]["httpGet"]["path"] == _HEALTHZ_PATH
    assert container["readinessProbe"]["httpGet"]["path"] == _READYZ_PATH


def test_service_yaml_uses_secret_ref_not_literal_secret() -> None:
    """Any secret-shaped env var must come from Secret Manager, not a literal."""
    container = _service_container()
    for env in container.get("env", []):
        name = env["name"]
        if name.endswith("API_KEY") or name.endswith("__URL"):
            assert "value" not in env, f"{name} must not carry a literal secret value"
            assert "valueFrom" in env, f"{name} must use a secretKeyRef"


@pytest.mark.parametrize(
    "group_prefix",
    sorted(
        f"MANGOMAS_{name.upper()}__"
        for name, field in Settings.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    ),
)
def test_readme_documents_every_settings_group(group_prefix: str) -> None:
    """deploy/README.md must document the env prefix of every settings group."""
    readme = _README.read_text(encoding="utf-8")
    assert group_prefix in readme, f"{group_prefix} missing from deploy/README.md"


def test_deploy_workflow_is_valid_yaml() -> None:
    doc = yaml.safe_load(_DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    # `on:` is parsed by PyYAML as the boolean key True — accept either form.
    assert "jobs" in doc
    assert "deploy" in doc["jobs"]


def test_deploy_workflow_applies_the_service_manifest() -> None:
    """spec-0024: the manifest must be applied, not merely documented.

    Both directions of the tie: some ``run:`` body names the manifest path and
    applies it with the full-manifest command, AND the referenced path is the
    very file the rest of this suite validates — so renaming either the file
    or the workflow reference fails here rather than at deploy time.
    """
    bodies = _deploy_run_bodies()
    assert bodies, "deploy.yml has no run: steps — has the workflow been emptied?"
    referencing = [b for b in bodies if _MANIFEST_WORKFLOW_REF in b]
    assert referencing, f"no deploy.yml run: body references {_MANIFEST_WORKFLOW_REF!r}"
    assert any(_MANIFEST_APPLY_COMMAND in b for b in bodies), (
        f"deploy.yml never runs {_MANIFEST_APPLY_COMMAND!r}; the manifest's env vars, "
        "secretKeyRefs, probes, limits and autoscaling bounds would not reach Cloud Run"
    )
    # The tie's other direction: the path the workflow consumes is the real
    # manifest, not a lookalike.
    assert _SERVICE_YAML.relative_to(_REPO_ROOT).as_posix() == _MANIFEST_WORKFLOW_REF
    assert _SERVICE_YAML.is_file()


def test_deploy_workflow_dropped_the_image_only_deploy() -> None:
    """The pre-spec-0024 ``gcloud run deploy --image`` call must never return.

    That form carried only the image: a service deployed by it runs with
    library defaults (auth off, SQLite, console exporter, localhost LLM URL).
    Kept separate from the positive assertion above so re-adding the old call
    *alongside* the manifest application still fails.
    """
    offenders = [b for b in _deploy_run_bodies() if _IMAGE_ONLY_DEPLOY_COMMAND in b]
    assert offenders == []


def test_deploy_workflow_smoke_probes_health_and_ready() -> None:
    """spec-0024: a deploy that reports success must have answered 200 twice.

    One step must probe both ``/healthz`` and ``/readyz``, authenticate with
    an identity token (the service is private — no allUsers invoker binding —
    so an anonymous probe proves nothing), and use ``curl -f`` so any non-2xx
    fails the job.
    """
    smoke = [b for b in _deploy_run_bodies() if _HEALTHZ_PATH in b and _READYZ_PATH in b]
    assert len(smoke) == 1, f"expected exactly one smoke step probing both paths: {smoke!r}"
    body = smoke[0]
    assert _SMOKE_AUTH_COMMAND in body, "smoke probe must send an identity token"
    assert _SMOKE_CURL_COMMAND in body, "smoke probe must fail the job on non-200 (curl -f)"


def test_deploy_job_is_gated_by_the_verify_job() -> None:
    """The deferred "verify job gating deploy.yml" item, absorbed by spec-0024.

    A release publish must not deploy code that fails the offline test gate;
    the verify job's steps delegate to ``make`` (the one-place rule from
    ``test_ci_make_parity.py``), so the gate commands live only in the
    Makefile.
    """
    jobs = _workflows.jobs(_DEPLOY_WORKFLOW.name)
    assert jobs["deploy"]["needs"] == "verify"
    verify_commands = [
        step["run"]
        for step in jobs["verify"]["steps"]
        if "run" in step and not str(step["run"]).startswith("pip install")
    ]
    assert verify_commands == ["make test", "make coverage"]


def test_deploy_workflow_uses_workload_identity_federation() -> None:
    """No service-account JSON keys — auth must be via WIF with id-token perms."""
    raw = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    # Version-agnostic: the action is SHA-pinned (see test_workflow_hardening),
    # so asserting a tag literal here would rot on every Dependabot bump.
    assert "google-github-actions/auth@" in raw
    assert "workload_identity_provider" in raw
    assert "id-token: write" in raw
    # Guard against a committed key file / inline credentials.
    assert "credentials_json" not in raw
    assert "service_account_key" not in raw
