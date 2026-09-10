"""Live-suite env names, timeout budgets, hardware contract, env-gates."""

from __future__ import annotations

import math
import os

# ── LM Studio E2E env-var names (single source of truth) ──────────────────────
LMSTUDIO_BASE_URL_ENV: str = "LMSTUDIO_BASE_URL"
LMSTUDIO_MODEL_ENV: str = "LMSTUDIO_MODEL"
# Second loaded model id for the per-agent MODEL_OVERRIDE scenario (spec-0029
# R4/L11). Unset ⇒ that one scenario skips at runtime with the sanctioned
# ``set LMSTUDIO_OVERRIDE_MODEL to run ...`` reason (see
# GATED_RUNTIME_SKIP_REASON_PREFIXES below).
LMSTUDIO_OVERRIDE_MODEL_ENV: str = "LMSTUDIO_OVERRIDE_MODEL"

# ── Vertex AI E2E env-var names (single source of truth) ──────────────────────
VERTEX_PROJECT_ENV: str = "VERTEX_PROJECT_ID"
VERTEX_LOCATION_ENV: str = "VERTEX_LOCATION"
VERTEX_MODEL_ENV: str = "VERTEX_MODEL"
VERTEX_CREDENTIALS_PATH_ENV: str = "VERTEX_CREDENTIALS_PATH"
# Default Vertex model used by E2E tests when ``VERTEX_MODEL`` is unset.
DEFAULT_VERTEX_TEST_MODEL: str = "gemini-1.5-flash"
STUB_VERTEX_REPLY: str = "stub-vertex-reply"

# ── Live-suite timeout budgets (spec-0029 R2.1) ───────────────────────────────
# One budget per live suite, read from the environment so a slow box can raise
# it without editing code — the thing both live conftests' docstrings always
# claimed and neither delivered.
#
# Why this replaced a separate ``HTTPX_REQUEST_TIMEOUT_SECONDS = 60.0``: the
# adapter was given 240 s while the httpx *client* wrapping the same call was
# given 60 s, so on a CPU box the client aborted a request the adapter was
# still legitimately waiting on. On a GPU box the completion landed inside 60 s
# and the mismatch was invisible — the suite was green on one machine and red
# on another for a reason unrelated to the code under test. The client budget
# is now *derived* from the adapter budget (never below it), which makes that
# inversion unrepresentable rather than merely fixed once.
LMSTUDIO_E2E_TIMEOUT_ENV: str = "LMSTUDIO_E2E_TIMEOUT_SECONDS"
VERTEX_E2E_TIMEOUT_ENV: str = "VERTEX_E2E_TIMEOUT_SECONDS"
# Sized for the slowest supported hardware (a local model on CPU), not for the
# fastest. A GPU box simply never reaches it.
DEFAULT_LIVE_E2E_TIMEOUT_SECONDS: float = 240.0
# Headroom added to the adapter budget when deriving the client budget, so the
# adapter's own typed timeout error (LLMTimeout → 504) always surfaces first
# and the test reports *that* rather than an opaque httpx ReadTimeout.
LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS: float = 30.0
# Tighter budget for scenarios that *expect* a fast failure (a 404 path, a bad
# base URL) and never run inference — hardware-independent by construction.
HTTPX_ERROR_PATH_TIMEOUT_SECONDS: float = 30.0
# A per-step budget below one network round trip, for the live StepTimeout
# scenario (spec-0029 R2.5). No hardware — GPU or CPU — returns a completion
# this fast, so the timeout fires identically on every device. Contrast
# TINY_STEP_TIMEOUT_SECONDS, which bounds an in-process fake.
LIVE_STEP_TIMEOUT_SECONDS: float = 0.001


def _validated_budget(value: float, source: str) -> float:
    """Return *value* if it is a usable timeout budget, else raise saying why.

    A budget must be finite and positive. ``NaN`` and ``inf`` are rejected
    explicitly rather than left to propagate: they defeat the very comparison
    the derivation below promises, because ``nan + 30 > nan`` and
    ``inf + 30 > inf`` are both ``False``. A silently non-finite budget would
    make "the client budget is never below the adapter budget" a claim that
    reads true and is not.
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{source} must be a finite positive number of seconds, got {value!r}")
    return value


def resolve_live_timeout(env_var: str) -> float:
    """Read a live-suite adapter budget from the environment.

    Shared by both live conftests so neither hand-rolls the env read (and so
    a malformed value fails loudly here rather than silently reverting to the
    default in one suite only).

    A malformed value names *the env var* in the error. A bare
    ``float("abc")`` raises "could not convert string to float: 'abc'", which
    tells whoever set it nothing about which variable to fix — and this is a
    knob operators reach for precisely when a suite is already misbehaving on
    their hardware.
    """
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        return DEFAULT_LIVE_E2E_TIMEOUT_SECONDS
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"{env_var} must be a number of seconds, got {raw!r}") from exc
    return _validated_budget(parsed, env_var)


def client_timeout_for(adapter_timeout_seconds: float) -> float:
    """Derive the httpx client budget from the adapter budget.

    Always strictly greater, so the client can never abort a request the
    adapter is still legitimately waiting on (spec-0029 R2.1) — which is only
    true for a finite positive input, so that is enforced rather than assumed.
    """
    validated = _validated_budget(adapter_timeout_seconds, "adapter timeout")
    return validated + LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS


# Single source for three consumers: the collection gate in tests/conftest.py
# builds its skip marks from these, the zero-skip session guard treats exactly
# these reasons as sanctioned, and tests/tooling/test_collection_gate.py
# asserts the wiring in a subprocess.
#
# Stored as {env_var: what-it-runs} and *formatted* into the reason, rather
# than storing the full sentence: the reason repeats its own key, so a
# hand-written table admits `{"RUN_RAG": "set RUN_LANGFUSE=1 to run RAG..."}`
# — a typo the gate would emit, the guard would sanction (it is in .values()),
# and no test would catch. Deriving it makes that desync unrepresentable.
ENV_GATE_SUITES: dict[str, str] = {
    "RUN_INTEGRATION": "integration tests",
    "RUN_LMSTUDIO": "LM Studio tests",
    "RUN_POSTGRES": "Postgres tests",
    "RUN_VERTEX": "Vertex AI tests",
    "RUN_GCP_SECRETS": "GCP Secret Manager tests",
    "RUN_GCP_TRACE": "Cloud Trace exporter tests",
    "RUN_EMBEDDINGS_LOCAL": "sentence-transformers tests",
    "RUN_RAG": "chromadb-backed RAG tests",
    "RUN_LANGFUSE": "Langfuse sink tests",
    "RUN_GITLEAKS": "gitleaks config behaviour tests",
}


def env_gate_skip_reason(env_var: str, suite: str) -> str:
    """Render the one sanctioned skip-reason sentence shape."""
    return f"set {env_var}=1 to run {suite}"


ENV_GATE_SKIP_REASONS: dict[str, str] = {
    env: env_gate_skip_reason(env, suite) for env, suite in ENV_GATE_SUITES.items()
}
# Runtime `pytest.skip(...)` calls inside already-enabled gated suites (the
# vertex/gcp fixtures that additionally need a project id) phrase their reason
# `set <VAR> to run <suite>`. Applied with `fullmatch`, not `match`: a prefix
# test would sanction `pytest.skip("set VERTEX_X ... actually just flaky")`.
# The alternation is derived from the env-var names the suites actually use,
# so it cannot rot away from them.
GATED_RUNTIME_SKIP_REASON_PREFIXES: tuple[str, ...] = ("VERTEX_", "GCP_", "LMSTUDIO_")
GATED_RUNTIME_SKIP_REASON_RE: str = (
    r"^set (?:" + "|".join(GATED_RUNTIME_SKIP_REASON_PREFIXES) + r")\w+ to run [\w \-]+$"
)

# ══════════════════════════════════════════════════════════════════════════════
# spec-0029 — hardware-agnostic end-to-end suites
# ══════════════════════════════════════════════════════════════════════════════

# ── Torch device names (spec-0029 R2.4) ───────────────────────────────────────
# The ONLY place a device literal may appear in the test tree. Every tier-3
# test names a device through these; the hardware-contract lint
# (tests/tooling/test_e2e_hardware_contract.py) fails any scoped file that
# spells one inline, which is how "no device literals" stays true after the
# author who wrote the rule has moved on.
DEVICE_CPU: str = "cpu"
DEVICE_CUDA: str = "cuda"
DEVICE_MPS: str = "mps"
TORCH_DEVICE_NAMES: tuple[str, ...] = (DEVICE_CPU, DEVICE_CUDA, DEVICE_MPS)

# ── Embedding comparison tolerance (spec-0029 R2.2) ───────────────────────────
# Real embedding backends are float32 and reorder reductions across devices, so
# two runs of the same text agree to ~1e-6, never bit-for-bit. Comparisons are
# cosine-similarity-within-tolerance; equality would be a test that passes only
# on the machine it was written on.
EMBEDDING_COSINE_ATOL: float = 1e-4
# Self-similarity of a unit vector with itself, the value E1 asserts against.
EMBEDDING_SELF_COSINE: float = 1.0

# ── Tier-3 fixed retrieval corpus (spec-0029 E1/E2) ───────────────────────────
# Two documents whose topics are far enough apart that *ranking* is stable on
# any device even though the scores are not. The oracle is which document wins,
# never by how much.
RAG_DEVICE_CORPUS: dict[str, str] = {
    "harness.md": "The harness wraps agent dispatch in a telemetry span for observability.",
    "loop.md": "The orchestrator loop caps steps and enforces a per-step timeout.",
}
RAG_DEVICE_QUERY: str = "how does the harness telemetry wrapper work?"
RAG_DEVICE_EXPECTED_TOP_SOURCE: str = "harness.md"
# Local sentence-transformers model used by the tier-3 suite. Small (~80 MB) so
# the nightly CPU job's cache stays cheap; overridable via the env var below for
# a contributor who has a different model already downloaded.
DEFAULT_LOCAL_EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
LOCAL_EMBEDDING_MODEL_ENV: str = "LOCAL_EMBEDDING_MODEL"

# ── Hardware-contract lint scope (spec-0029 R7.1) ─────────────────────────────
# Repo-root-relative globs of the tiers that touch real hardware. Tier 1 is
# absent on purpose: it runs fakes in-process, so none of the six rules can be
# violated there and scanning it would only invite false positives.
HARDWARE_CONTRACT_SCOPE: tuple[str, ...] = (
    "tests/lmstudio/*.py",
    "tests/vertex/*.py",
    "tests/rag/test_end_to_end*.py",
    "tests/rag/test_cli_local_round_trip.py",
)
# Minimum number of files the scope must match. A glob that matches nothing
# would make the lint vacuously green — the same fail-open shape
# `scripts/lint_agent_frontmatter.py`'s MIN_AGENT_FILES floor exists to stop.
MIN_HARDWARE_CONTRACT_FILES: int = 10

# ── Gated suites with no hosted-runner home (spec-0029 R7.2) ──────────────────
# Roadmap item 2.1: every gated suite either runs somewhere in CI, or says here
# why it cannot. A suite in BOTH this table and a workflow is a stale claim and
# fails the parity guard — that direction is the point, because an infeasibility
# reason nobody revisits is exactly how a suite that *became* runnable stays
# unrun.
HOSTED_RUNNER_INFEASIBLE: dict[str, str] = {
    "RUN_LMSTUDIO": (
        "needs a local LM Studio process serving a loaded model; hosted runners "
        "cannot host one and no self-hosted runner is provisioned"
    ),
    "RUN_VERTEX": ("needs a real GCP project with Gemini model access — roadmap decision D2"),
    "RUN_GCP_SECRETS": ("needs GCP Secret Manager access (ADC + project) — roadmap decision D2"),
    "RUN_GCP_TRACE": ("needs the gcp extra plus a Cloud Trace destination — roadmap decision D2"),
    "RUN_LANGFUSE": ("needs the langfuse extra plus Langfuse credentials — roadmap decision D2"),
}

# ── Tier-1 integration-flow values (spec-0029 R3) ─────────────────────────────
# Env-var names the composed-app flows set. Named here (not inline) for the
# same reason every other env-var name is: one typo in a literal silently makes
# a test assert the default instead of the override, and still passes.
# Every settings env var shares this prefix, so it is also what a hermetic
# fixture scrubs: a flow that declares its own MANGOMAS_* world must not
# inherit the rest of the developer's shell.
SETTINGS_ENV_PREFIX: str = "MANGOMAS_"
LOOP_STEP_TIMEOUT_ENV: str = "MANGOMAS_LOOP__STEP_TIMEOUT_SECONDS"
LOOP_MAX_STEPS_ENV: str = "MANGOMAS_LOOP__MAX_STEPS"
CHAT_MODEL_OVERRIDE_ENV: str = "MANGOMAS_AGENTS__CHAT__MODEL_OVERRIDE"
PLANNER_VALIDATE_OUTPUT_ENV: str = "MANGOMAS_AGENTS__PLANNER__VALIDATE_OUTPUT"
REVIEWER_VALIDATE_OUTPUT_ENV: str = "MANGOMAS_AGENTS__REVIEWER__VALIDATE_OUTPUT"
TENANCY_ENABLED_ENV: str = "MANGOMAS_TENANCY__ENABLED"
DB_URL_ENV: str = "MANGOMAS_DB__URL"
EMBEDDINGS_PROVIDER_ENV: str = "MANGOMAS_EMBEDDINGS__PROVIDER"
EMBEDDINGS_ENABLED_ENV: str = "MANGOMAS_EMBEDDINGS__ENABLED"
EMBEDDINGS_MODEL_ENV: str = "MANGOMAS_EMBEDDINGS__MODEL"
EMBEDDINGS_DEVICE_ENV: str = "MANGOMAS_EMBEDDINGS__DEVICE"
VECTOR_ENABLED_ENV: str = "MANGOMAS_VECTOR__ENABLED"
VECTOR_PERSIST_DIR_ENV: str = "MANGOMAS_VECTOR__PERSIST_DIR"
# In-memory SQLite so a developer's real database is never touched by a flow.
IN_MEMORY_SQLITE_URL: str = "sqlite:///:memory:"
# A model id that differs from DEFAULT_LLM_MODEL, for the MODEL_OVERRIDE flows.
# Never loaded — the recording factory intercepts before any client is built.
OVERRIDE_MODEL_ID: str = "override-model"
# Loop budget the multi-step flow sets, chosen > 1 so `steps_taken` discriminates
# between "the env var was read" and "the field default (1) was used".
FLOW_LOOP_MAX_STEPS: int = 3
# A body-supplied budget differing from FLOW_LOOP_MAX_STEPS, so the request tier
# of `_effective_max_steps` is distinguishable from the settings tier.
FLOW_REQUEST_MAX_STEPS: int = 2

__all__ = [
    "CHAT_MODEL_OVERRIDE_ENV",
    "DB_URL_ENV",
    "DEFAULT_LIVE_E2E_TIMEOUT_SECONDS",
    "DEFAULT_LOCAL_EMBEDDING_MODEL",
    "DEFAULT_VERTEX_TEST_MODEL",
    "DEVICE_CPU",
    "DEVICE_CUDA",
    "DEVICE_MPS",
    "EMBEDDINGS_DEVICE_ENV",
    "EMBEDDINGS_ENABLED_ENV",
    "EMBEDDINGS_MODEL_ENV",
    "EMBEDDINGS_PROVIDER_ENV",
    "EMBEDDING_COSINE_ATOL",
    "EMBEDDING_SELF_COSINE",
    "ENV_GATE_SKIP_REASONS",
    "ENV_GATE_SUITES",
    "FLOW_LOOP_MAX_STEPS",
    "FLOW_REQUEST_MAX_STEPS",
    "GATED_RUNTIME_SKIP_REASON_PREFIXES",
    "GATED_RUNTIME_SKIP_REASON_RE",
    "HARDWARE_CONTRACT_SCOPE",
    "HOSTED_RUNNER_INFEASIBLE",
    "HTTPX_ERROR_PATH_TIMEOUT_SECONDS",
    "IN_MEMORY_SQLITE_URL",
    "LIVE_CLIENT_TIMEOUT_HEADROOM_SECONDS",
    "LIVE_STEP_TIMEOUT_SECONDS",
    "LMSTUDIO_BASE_URL_ENV",
    "LMSTUDIO_E2E_TIMEOUT_ENV",
    "LMSTUDIO_MODEL_ENV",
    "LMSTUDIO_OVERRIDE_MODEL_ENV",
    "LOCAL_EMBEDDING_MODEL_ENV",
    "LOOP_MAX_STEPS_ENV",
    "LOOP_STEP_TIMEOUT_ENV",
    "MIN_HARDWARE_CONTRACT_FILES",
    "OVERRIDE_MODEL_ID",
    "PLANNER_VALIDATE_OUTPUT_ENV",
    "RAG_DEVICE_CORPUS",
    "RAG_DEVICE_EXPECTED_TOP_SOURCE",
    "RAG_DEVICE_QUERY",
    "REVIEWER_VALIDATE_OUTPUT_ENV",
    "SETTINGS_ENV_PREFIX",
    "STUB_VERTEX_REPLY",
    "TENANCY_ENABLED_ENV",
    "TORCH_DEVICE_NAMES",
    "VECTOR_ENABLED_ENV",
    "VECTOR_PERSIST_DIR_ENV",
    "VERTEX_CREDENTIALS_PATH_ENV",
    "VERTEX_E2E_TIMEOUT_ENV",
    "VERTEX_LOCATION_ENV",
    "VERTEX_MODEL_ENV",
    "VERTEX_PROJECT_ENV",
    "client_timeout_for",
    "env_gate_skip_reason",
    "resolve_live_timeout",
]
