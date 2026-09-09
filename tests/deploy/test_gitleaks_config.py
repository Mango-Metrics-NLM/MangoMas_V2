"""Contract tests for `.gitleaks.toml` — the secret-scan ruleset.

A misconfigured scanner is indistinguishable from a clean tree: both print
"no leaks found" and exit 0. That makes `make secret-scan` the one gate in this
repo whose *success* carries no information unless something independently
proves the scanner can still fail. These tests are that proof.

Two layers, because neither alone is honest:

* **Structure + Python model** (always runs, offline). Parses the TOML and
  evaluates the rule's regex and its allowlist with Python's `re`. Cheap,
  runs on every commit, and catches the mistakes that actually happen — a
  dropped `useDefault`, an allowlist widened until it exempts real
  credentials. It is a *model* of gitleaks, not gitleaks: Go's RE2 and
  Python's `re` agree on these patterns but are not the same engine.
* **The real binary** (`RUN_GITLEAKS=1`, `@pytest.mark.gitleaks`). Plants
  fixtures in a tmp directory and runs the pinned binary against them. This is
  the authoritative check; the model exists so a regression is usually caught
  without it.

The planted secrets are synthetic strings in well-known example formats. None
is a live credential, and they live only in tmp files the tests create.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests.constants import DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH, SIGNAL_POLICY_SNAPSHOT_HASH_ENV

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / ".gitleaks.toml"
_CONNECTION_STRING_RULE = "mangomas-connection-string-password"

# Fixtures are *assembled* rather than written out, so no line in this file is
# itself a detectable secret. The alternative — spelling them out and adding
# `# gitleaks:allow` to each — works, but every such marker is a line the
# scanner is told to ignore forever, in the one file most likely to accumulate
# them. Building the strings at runtime leaves nothing to exempt: the scan sees
# a clean file, and the test still plants real, detectable patterns on disk.


def _db_url(password: str, *, scheme: str = "postgresql", host: str = "db.prod.internal") -> str:
    """A connection string carrying ``password`` — assembled, never written out."""
    return f"MANGOMAS_DB__URL={scheme}://svc:{password}@{host}:5432/mangomas"


def _github_pat() -> str:
    """A syntactically valid GitHub PAT. Synthetic; never issued."""
    return 'TOKEN = "' + "ghp_" + "16CharsAndMoreABCDEFGHIJKLMNOPQRSTUV12" + '"'


# Secrets the scan MUST catch, as (label, file name, line). The connection
# strings are the class the built-in ruleset provably misses — that gap is the
# whole reason the custom rule exists — and the vendor token confirms
# `useDefault = true` is still in force.
#
# `SuperPassword2024Xy` is deliberate: it contains the word "password", which a
# conventional `stopwords` list would exempt as a substring. See
# `test_placeholder_allowlist_does_not_exempt_a_real_password`.
_MUST_DETECT: tuple[tuple[str, str, str], ...] = (
    ("db-url-password", "leak.env", _db_url("S3cr3tP4ssw0rdHere", host="10.0.0.5")),
    ("db-url-password-asyncpg", "leak2.env", _db_url("Zx9QmL2vTr8Kd", scheme="postgresql+asyncpg")),
    ("db-url-contains-placeholder-word", "leak3.env", _db_url("SuperPassword2024Xy")),
    ("github-pat", "leak.py", _github_pat()),
)

# Lines the scan must NOT flag: the repo's own fixtures and docs. A rule that
# fires on these gets disabled by the next contributor, which is how a scanner
# stops scanning.
_MUST_IGNORE: tuple[tuple[str, str, str], ...] = (
    ("reserved-invalid-host", "ok1.py", 'URL = "postgresql://user:pw@nonexistent.invalid:5432/db"'),
    ("reserved-test-host", "ok2.py", 'URL = "postgresql://user:secret@db.example.test:5432/app"'),
    ("loopback", "ok3.env", "MANGOMAS_DB__URL=postgresql://mangomas:pass@localhost:5432/mangomas"),
    ("env-interpolation", "ok4.yaml", "  value: postgresql://svc:${DB_PASSWORD}@10.0.0.5/db"),
    ("placeholder-password", "ok5.md", "    postgresql://user:pass@host:5432/mangomas"),
    ("sqlite-has-no-credential", "ok6.env", "MANGOMAS_DB__URL=sqlite:///./data/mangomas.db"),
    (
        "signal-policy-hash",
        "ok7.env",
        f"{SIGNAL_POLICY_SNAPSHOT_HASH_ENV}={DEFAULT_SIGNAL_POLICY_SNAPSHOT_HASH}",
    ),
)


def _config() -> dict[str, object]:
    return tomllib.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _connection_string_rule() -> dict[str, object]:
    rules = _config()["rules"]
    assert isinstance(rules, list)
    for rule in rules:
        assert isinstance(rule, dict)
        if rule.get("id") == _CONNECTION_STRING_RULE:
            return rule
    pytest.fail(f"{_CONNECTION_STRING_RULE} is not defined in {_CONFIG_PATH.name}")


def _model_detects(line: str) -> bool:
    """Would the connection-string rule flag ``line``? (Python model.)"""
    rule = _connection_string_rule()
    if re.search(str(rule["regex"]), line) is None:
        return False
    allowlist = rule.get("allowlist")
    assert isinstance(allowlist, dict), "the rule must carry its own allowlist"
    return not any(re.search(pattern, line) for pattern in allowlist["regexes"])


# ── Structure ─────────────────────────────────────────────────────────────────


def test_config_exists_and_extends_the_default_ruleset() -> None:
    """`useDefault = true` is what keeps every built-in rule.

    Dropping it turns the file from "defaults plus one rule" into "one rule",
    silently narrowing the scan to connection strings alone while still
    exiting 0 on a tree full of GitHub tokens.
    """
    assert _CONFIG_PATH.exists(), f"{_CONFIG_PATH} is missing"
    extend = _config().get("extend")
    assert isinstance(extend, dict), "no [extend] table — the built-in rules are not inherited"
    assert extend.get("useDefault") is True


def test_makefile_passes_the_config_to_both_passes() -> None:
    """Both scan passes must be given `-c`.

    gitleaks does not auto-discover a repo-root config when a scan path is
    supplied, so an omitted flag reverts that pass to the built-in rules with
    no warning — and the `dir` pass is the one that sees an uncommitted `.env`.
    """
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    scan_lines = [
        line for line in makefile.splitlines() if re.search(r"^\s*\./gitleaks (dir|git)\b", line)
    ]
    assert len(scan_lines) == 2, f"expected a dir pass and a git pass, found: {scan_lines}"
    for line in scan_lines:
        assert "-c $(GITLEAKS_CONFIG)" in line, line
    assert "GITLEAKS_CONFIG ?= .gitleaks.toml" in makefile


# Global path exemptions, each of which is a place the scanner does not look.
# Pinned as an exact set: the easy way to make a red scan green is to add a
# path here, and that must be a reviewed edit rather than a quiet one.
_EXPECTED_ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        r"^\.hypothesis/",
        r"^gitleaks$",
        r"(^|/)__pycache__/",
    }
)


def test_global_allowlist_stays_narrow() -> None:
    """No path may be exempted from the scan without review.

    All three entries are generated or downloaded artefacts that git never
    tracks, so nothing a human wrote can hide behind them. Note what is *not*
    here: `tests/` is fully scanned, including this file — its fixtures are
    assembled at runtime precisely so no exemption is needed.
    """
    allowlist = _config().get("allowlist")
    assert isinstance(allowlist, dict), "no global [allowlist] table"
    assert set(allowlist.get("paths", [])) == _EXPECTED_ALLOWLIST_PATHS


def test_rule_regex_is_anchored_to_a_credentialed_url() -> None:
    """The rule must require `user:password@`, not merely a scheme.

    A rule matching a bare `postgresql://` would fire on every docstring in
    `adapters/storage/postgres.py` and be switched off within a day.
    """
    rule = _connection_string_rule()
    assert rule.get("secretGroup") == 1, "the report must redact the password, not the whole URL"
    assert not _model_detects("postgresql://host/db"), "fires without a credential"
    assert not _model_detects("see the postgresql:// scheme"), "fires on prose"


# ── The Python model ──────────────────────────────────────────────────────────


_MODEL_DETECT = tuple(f for f in _MUST_DETECT if f[0].startswith("db-url"))


@pytest.mark.parametrize(("label", "_name", "line"), _MODEL_DETECT, ids=lambda v: str(v)[:40])
def test_model_detects_planted_secrets(label: str, _name: str, line: str) -> None:
    """Every connection-string fixture is caught by the custom rule.

    Scoped to the connection-string cases on purpose: the vendor-token
    fixtures belong to built-in rules this model does not reimplement, and
    the real-binary test below is what proves those still fire.
    """
    assert _model_detects(line), f"{label}: the rule does not flag {line!r}"


@pytest.mark.parametrize(("label", "_name", "line"), _MUST_IGNORE, ids=lambda v: str(v)[:40])
def test_model_ignores_repo_fixtures(label: str, _name: str, line: str) -> None:
    """No must-ignore fixture is flagged."""
    assert not _model_detects(line), f"{label}: false positive on {line!r}"


def test_placeholder_allowlist_does_not_exempt_a_real_password() -> None:
    """The allowlist must match a placeholder *whole*, never as a substring.

    This is the trap the conventional config walks into. gitleaks `stopwords`
    are substring matches, so the obvious `stopwords = ["password", ...]`
    exempts `SuperPassword2024Xy` — a real credential — because it contains
    "password". Verified against the real binary, not assumed. The config
    therefore uses allowlist regexes anchored between `:` and `@` instead, and
    declares no stopwords at all.
    """
    allowlist = _connection_string_rule().get("allowlist")
    assert isinstance(allowlist, dict), "the rule must carry its own allowlist"
    assert "stopwords" not in allowlist, (
        "stopwords are substring matches; use an anchored allowlist regex instead"
    )
    assert _model_detects(_db_url("SuperPassword2024Xy"))
    assert _model_detects(_db_url("mypassphrase9"))
    # …while the bare placeholder itself stays exempt.
    assert not _model_detects(_db_url("pass"))


# ── The real binary ───────────────────────────────────────────────────────────


def _gitleaks_binary() -> str | None:
    local = _REPO_ROOT / "gitleaks"
    if local.is_file():
        return str(local)
    return shutil.which("gitleaks")


@pytest.mark.gitleaks
def test_real_gitleaks_catches_every_planted_secret(tmp_path: Path) -> None:
    """Authoritative non-vacuity proof: run the pinned binary on planted leaks.

    Plants one fixture per must-detect class and requires the scan to exit
    non-zero and name every one. This is what stops `make secret-scan`'s green
    from being a green that means nothing — and it covers what the Python model
    above cannot, since RE2 and `re` are different engines.
    """
    binary = _gitleaks_binary()
    if binary is None:
        pytest.skip("set RUN_GITLEAKS=1 to run gitleaks config behaviour tests")

    for _label, name, line in _MUST_DETECT:
        (tmp_path / name).write_text(line + "\n", encoding="utf-8")
    for _label, name, line in _MUST_IGNORE:
        (tmp_path / name).write_text(line + "\n", encoding="utf-8")

    report = tmp_path / "report.json"
    result = subprocess.run(  # noqa: S603 -- trusted: resolved binary + fixed args
        [
            binary,
            "dir",
            "--no-banner",
            "--exit-code",
            "1",
            "-c",
            str(_CONFIG_PATH),
            "--report-format",
            "json",
            "--report-path",
            str(report),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    logger.debug(
        "gitleaks probe finished",
        extra={"event": "gitleaks_probe", "returncode": result.returncode},
    )
    assert result.returncode != 0, (
        "gitleaks exited 0 on a directory full of planted secrets — the config "
        f"is vacuous.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    findings = json.loads(report.read_text(encoding="utf-8"))
    flagged = {Path(f["File"]).name for f in findings}

    missed = sorted({name for _l, name, _line in _MUST_DETECT} - flagged)
    assert missed == [], f"planted secrets the scan did not catch: {missed}"

    false_positives = sorted({name for _l, name, _line in _MUST_IGNORE} & flagged)
    assert false_positives == [], f"scan flagged repo fixtures: {false_positives}"
