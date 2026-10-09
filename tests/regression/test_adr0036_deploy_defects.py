"""Regression guards for defects found and fixed while delivering ADR-0036.

The dev → qa → main branching model and the per-environment deploy went
through architect, test-engineer, security and code-hygiene reviews; each
defect below was real on some commit of the branch. Offline only — no git
remote, cloud or LLM is touched.

Defect classes covered:
  D1 — ci.yml: protected-path base pinned to one trunk name (rename broke CI)
  D2 — ci.yml: protected-path run steps spliced the base branch into script text
  D3 — Makefile: deploy recipes spliced ENVIRONMENT/IMAGE into recipe text
  D4 — environments.yaml: one image repository shared by every environment
  D5 — environments.yaml: non-production ran as production's runtime identity
  D6 — deploy.yml / registry: prereleases and non-version `v*` tags reached prod
  D7 — scripts/deploy_environment.py: shipped with no owning agent
  D8 — deploy_environment.py: duplicate base env names were half-overridden
  D9 — deploy_environment.py: a requested environment bypassed the ambiguity check
  D10 — nightly.yml: security scans only ever checked out the default branch
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests._script_loader import load_script_module
from tests.deploy import _makefile, _manifest, _workflows

deploy_env = load_script_module("deploy_environment.py")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY = deploy_env.load_registry(_REPO_ROOT / _makefile.variable("DEPLOY_REGISTRY"))
_CI = "ci.yml"
_DEPLOY = "deploy.yml"
_EXPRESSION_RE = re.compile(r"\$\{\{")


def _production() -> Any:
    return next(e for e in _REGISTRY.environments.values() if e.trigger.tag_pattern)


# ── D1: trunk name pinned in CI ──────────────────────────────────────────────
#
# RCA: `BASE_BRANCH: feat/initial-release` meant renaming the trunk left every
#      PR without a fetchable base, and a qa → main promotion was judged
#      against the wrong branch.
# Fix: resolve from the event — the PR's base, else the default branch.


def test_d1_protected_path_base_follows_the_event() -> None:
    """D1: the gate's base is derived, never a literal branch name."""
    base = _workflows.jobs(_CI)["protected-paths"]["env"]["BASE_BRANCH"]
    assert "github.base_ref" in base and "default_branch" in base, (
        f"BASE_BRANCH must resolve from the event, got {base!r}"
    )


# ── D2: expression splicing in the gate's run steps ──────────────────────────
#
# RCA: once BASE_BRANCH came from event data, `git fetch origin ${{ env.X }}`
#      spliced a branch name (git allows `$`, `(`, `;`) into script text.
# Fix: read it as a quoted shell variable.


def test_d2_protected_path_steps_read_a_shell_variable() -> None:
    """D2: no `${{ }}` inside the protected-path job's run bodies."""
    steps = _workflows.jobs(_CI)["protected-paths"]["steps"]
    spliced = [s["run"] for s in steps if "run" in s and _EXPRESSION_RE.search(s["run"])]
    assert spliced == [], f'use "$BASE_BRANCH" instead of an expression: {spliced}'


# ── D3: make expansion of runtime values ─────────────────────────────────────
#
# RCA: `--environment "$(ENVIRONMENT)"` let make paste the value into the
#      shell command before quoting applied.
# Fix: recipes read `"$$ENVIRONMENT"` from the exported environment.


@pytest.mark.parametrize("target", ["deploy-render", "deploy-apply"])
def test_d3_deploy_recipes_never_expand_runtime_values(target: str) -> None:
    """D3: ENVIRONMENT/IMAGE/REGION reach recipes only as shell variables."""
    body = _makefile.target_body(target)
    leaked = [name for name in ("ENVIRONMENT", "IMAGE", "REGION") if f"$({name})" in body]
    assert leaked == [], f"{target} expands {leaked} into recipe text"


# ── D4 / D5: cross-environment trust ─────────────────────────────────────────
#
# RCA: a shared repository plus reuse-by-tag let a dev deployer pre-push an
#      image prod would trust; without a runtime SA every service ran as the
#      default identity that can read production's secrets.
# Fix: per-environment repository and runtime identity, digest-pinned image.


def test_d4_every_environment_has_its_own_repository() -> None:
    """D4: no two environments share an Artifact Registry repository."""
    repos = [_REGISTRY.repository_for(e) for e in _REGISTRY.environments.values()]
    assert len(set(repos)) == len(repos), f"shared image repository: {repos}"


def test_d5_non_production_environments_set_a_runtime_identity() -> None:
    """D5: every non-production overlay names its own service account."""
    accounts = [
        e.overrides.service_account for e in _REGISTRY.environments.values() if e != _production()
    ]
    assert all(accounts), "a non-production environment has no service_account"
    assert len(set(accounts)) == len(accounts), f"runtime identities shared: {accounts}"


# ── D6: prereleases / loose tags ─────────────────────────────────────────────
#
# RCA: `release: published` also fires for prereleases, and `v*` matched
#      `vandal` and `v1/../x`.
# Fix: the plan job skips prereleases; production matches `v<digit>…` only.


def test_d6_prereleases_do_not_plan() -> None:
    """D6: the plan job's condition excludes prereleases."""
    assert "github.event.release.prerelease" in _workflows.jobs(_DEPLOY)["plan"]["if"]


@pytest.mark.parametrize("tag", ["vandal", "v", "version"])
def test_d6_non_version_tags_do_not_match_production(tag: str) -> None:
    """D6: only version-shaped tags resolve to production."""
    assert not _production().trigger.matches(f"{deploy_env.TAG_REF_PREFIX}{tag}")


# ── D7: unowned script ───────────────────────────────────────────────────────
#
# RCA: mango-ci-dev disclaimed `scripts/`; mango-harness-dev owned exactly four.
# Fix: mango-ci-dev claims the deploy script; a corpus test now covers scripts/.


def test_d7_deploy_script_is_claimed_by_ci_dev() -> None:
    """D7: the deploy script is named in mango-ci-dev's surface, before "Not yours"."""
    body = (_REPO_ROOT / ".claude" / "agents" / "mango-ci-dev.md").read_text(encoding="utf-8")
    surface = body.split("## Surface You Own", 1)[1].split("Not yours", 1)[0]
    assert "scripts/deploy_environment.py" in surface


# ── D8 / D9: render and resolve edge cases ───────────────────────────────────
#
# RCA: `_env_entry` updated only the first of two same-named entries, leaving
#      production's value live; a requested environment returned before the
#      overlapping-pattern check ran.
# Fix: duplicate names are rejected; ambiguity is checked first.


def test_d8_duplicate_base_env_names_are_rejected() -> None:
    """D8: a duplicated env name in the base cannot be half-overridden."""
    base = _manifest.base_manifest()
    env = _manifest.container(base)["env"]
    env.append(dict(env[0]))
    with pytest.raises(deploy_env.DeployConfigError, match="more than once"):
        deploy_env.render_manifest(base, _production(), "image@sha256:" + "0" * 64)


def test_d9_ambiguity_is_checked_before_a_requested_environment() -> None:
    """D9: overlapping tag patterns fail even when a dispatch names one of them."""
    registry = deploy_env.parse_registry(
        {
            "schema_version": deploy_env.SUPPORTED_SCHEMA_VERSION,
            "defaults": {"region": "r", "repository": "p", "image": "i", "base_manifest": "m"},
            "environments": {
                "a": {"trigger": {"tag_pattern": "v*"}, "service": "svc-a"},
                "b": {"trigger": {"tag_pattern": "v1*"}, "service": "svc-b"},
            },
        }
    )
    with pytest.raises(deploy_env.DeployConfigError, match="several environments"):
        deploy_env.resolve_environment(registry, f"{deploy_env.TAG_REF_PREFIX}v1.0", "a")


# ── D10: production code never scanned ───────────────────────────────────────
#
# RCA: scheduled workflows run on the default branch only, so the nightly
#      secret and SBOM scans never saw `main` — the code actually in production.
# Fix: a `scan-refs` job derives the environment branches from the registry
#      (opt-in until legacy `main` is reset) and the scans matrix over them.


@pytest.mark.parametrize("job", ["secret-scan", "sbom-scan"])
def test_d10_security_scans_matrix_over_resolved_branches(job: str) -> None:
    """D10: each security scan checks out every branch scan-refs resolves."""
    spec = _workflows.jobs("nightly.yml")[job]
    assert "needs.scan-refs.outputs.refs" in spec["strategy"]["matrix"]["ref"]


def test_d10_registry_branches_include_production() -> None:
    """D10: the derived scan list reaches the branch production deploys from."""
    production_branch = _production().trigger.ancestor_branch
    assert production_branch in deploy_env.environment_branches(_REGISTRY)
