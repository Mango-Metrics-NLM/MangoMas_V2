"""Contract tests for the Cloud Run deploy artifacts (Milestone E).

These assert the *shape* of the deploy manifests and their doc-sync with the
settings model. They make **no live-cloud assertion** — this repo provisions no
GCP resources (ADR-0001).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from pydantic import BaseModel

from mangomas.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO_ROOT / "deploy"
_SERVICE_YAML = _DEPLOY / "service.yaml"
_README = _DEPLOY / "README.md"
_DEPLOY_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "deploy.yml"

_HEALTHZ_PATH = "/healthz"
_READYZ_PATH = "/readyz"


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


def test_deploy_workflow_uses_workload_identity_federation() -> None:
    """No service-account JSON keys — auth must be via WIF with id-token perms."""
    raw = _DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "google-github-actions/auth@v2" in raw
    assert "workload_identity_provider" in raw
    assert "id-token: write" in raw
    # Guard against a committed key file / inline credentials.
    assert "credentials_json" not in raw
    assert "service_account_key" not in raw
