"""Regression guards for soft-deploy / v0.5.0 Cloud Run edge defects.

Defects caught live on mangomas-dev (2026-11-20) and fixed before claiming
soft-deploy green. Offline only — no cloud calls.

Defect classes:
  D1 — deploy.yml smoke probed ``/healthz``; Google Frontend returns HTML 404
  D2 — service.yaml / Dockerfile still used ``*z`` probe paths against known-issues
  D3 — mangomas-dev Vertex ``LOCATION`` must be ``global`` for gemini-3 flash
  D4 — package version claim drifted from advertised release cut
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.deploy import _makefile, _manifest, _workflows

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_WF = "deploy.yml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_REGISTRY = _REPO_ROOT / "deploy" / "environments.yaml"


def _deploy_smoke_bodies() -> list[str]:
    return [
        step["run"]
        for step in _workflows.jobs(_DEPLOY_WF)["deploy"]["steps"]
        if "run" in step and "Smoke-test" in str(step.get("name", ""))
    ]


def test_d1_external_smoke_never_probes_healthz() -> None:
    """D1: public ``*.run.app`` GFE 404s exact ``/healthz`` — smoke must use ``/health``."""
    bodies = _deploy_smoke_bodies()
    assert len(bodies) == 1, f"expected one smoke step, got {len(bodies)}"
    body = bodies[0]
    assert re.search(r"for path in /health /ready\b", body)
    assert not re.search(r"for path in[^\n]*/(?:healthz|readyz)", body)


def test_d2_manifest_and_dockerfile_prefer_non_z_probes() -> None:
    """D2: Cloud Run known-issues — avoid probe paths ending in ``z``."""
    container = _manifest.container(_manifest.base_manifest())
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
    healthcheck = _DOCKERFILE.read_text(encoding="utf-8")
    assert "/health" in healthcheck
    assert "/healthz" not in healthcheck


def test_d3_mangomas_dev_vertex_location_is_global() -> None:
    """D3: regional gemini-2.5/2.0 IDs 404 on hrmtrain; soft-deploy needs global."""
    registry = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    env = registry["environments"]["dev"]["overrides"]["env"]
    assert env["MANGOMAS_LLM__LOCATION"] == "global"


def test_d4_package_version_is_honest_point_five() -> None:
    """D4: after historical ``v0.4.0`` tag, trunk advertises ``0.5.0``."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match is not None
    assert match.group(1) == "0.5.0"


def test_makefile_still_owns_deploy_render_apply() -> None:
    """Sanity: soft-deploy still goes through make targets, not ad-hoc gcloud."""
    for target in ("deploy-render", "deploy-apply"):
        assert _makefile.target_body(target)
