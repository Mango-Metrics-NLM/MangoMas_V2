"""Pre-commit local hooks stay a documented subset of the Makefile gate.

``test_ci_make_parity.py`` reads workflows, not ``.pre-commit-config.yaml``.
These two cheap mirrors (``validate-config``, ``lint-imports``) must exist
here and name the same files / invocation as the Makefile, or a contributor
who only runs pre-commit silently skips them.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRECOMMIT = _REPO_ROOT / ".pre-commit-config.yaml"
_MAKEFILE = _REPO_ROOT / "Makefile"

_REQUIRED_LOCAL_HOOK_IDS: tuple[str, ...] = (
    "lint-agent-frontmatter",
    "validate-config",
    "lint-imports",
)

_VALIDATE_CONFIG_FILES: tuple[str, ...] = (
    ".mcp.json",
    ".claude/settings.json",
    ".claude/settings.local.json.example",
)


def _precommit_text() -> str:
    return _PRECOMMIT.read_text(encoding="utf-8")


def _local_hook_ids(text: str) -> list[str]:
    """Return ``id:`` values under the first ``repo: local`` block.

    The file is small and the contract is "these ids exist", not a full
    YAML reimplementation of pre-commit's loader.
    """
    ids: list[str] = []
    in_local = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- repo:"):
            in_local = stripped.endswith("local")
            continue
        if in_local and stripped.startswith("- id:"):
            ids.append(stripped.split(":", 1)[1].strip())
    return ids


def test_precommit_config_exists() -> None:
    assert _PRECOMMIT.is_file()


def test_precommit_local_hooks_include_makefile_mirrors() -> None:
    present = _local_hook_ids(_precommit_text())
    missing = [hook_id for hook_id in _REQUIRED_LOCAL_HOOK_IDS if hook_id not in present]
    assert missing == [], f"pre-commit local hooks missing {missing}; have {present}"


def test_validate_config_hook_covers_the_makefile_files() -> None:
    """``make validate-config`` json.tool's three files; the hook must name them."""
    makefile = _MAKEFILE.read_text(encoding="utf-8")
    hook = _precommit_text()
    for relpath in _VALIDATE_CONFIG_FILES:
        assert relpath in makefile, f"Makefile validate-config no longer names {relpath}"
        assert relpath in hook, f"pre-commit validate-config no longer names {relpath}"


def test_lint_imports_hook_matches_makefile_invocation() -> None:
    """``python -m importlinter`` has no ``__main__``; both must call ``lint_imports``."""
    snippet = "lint_imports(no_logo=True)"
    assert snippet in _MAKEFILE.read_text(encoding="utf-8")
    assert snippet in _precommit_text()
