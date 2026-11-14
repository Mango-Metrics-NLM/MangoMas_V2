"""Unisolated host subprocess backend for the repair-loop oracle (default ``none``)."""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from mangomas.errors import ToolExecutionError
from mangomas.harness.isolation.base import (
    IsolationResult,
    SubprocessRunner,
    reject_candidate_python_startup_hooks,
    resolve_sandbox_argv,
)

logger = logging.getLogger(__name__)

_PYTHON_EXE_PREFIXES: tuple[str, ...] = ("python", "pypy")
_INLINE_FLAG_PREFIX_LEN = 2
_PYTHON_ENTRY_FLAGS: frozenset[str] = frozenset({"c", "m"})
_PYTHON_VALUE_FLAGS: frozenset[str] = frozenset({"W", "X"})
_PYTHON_LONG_VALUE_OPTIONS: frozenset[str] = frozenset({"--check-hash-based-pycs"})

# Executed via ``-s -c`` when launching a live Python oracle on the host:
# 1. Installs a MetaPathFinder at sys.meta_path[0] so partial staged packages
#    (e.g. a single file without __init__.py) always win over an editable/site install.
# 2. Clears wrapper parameters from sys.argv and module globals before candidate code runs.
# 3. Writes the per-run cryptographic nonce to the external sentinel file inside
#    finally: (so normal completion, sys.exit, and Python tracebacks write it, while
#    os._exit(0) short-circuits leave the sentinel absent and fail closed).
_TRUSTED_ORACLE_WRAPPER: str = """
import importlib.abc, importlib.util, pathlib, runpy, sys

class _StagedOverlayFinder(importlib.abc.MetaPathFinder):
    def __init__(self, roots, fallback_paths):
        self._roots = tuple(roots)
        self._fallback = tuple(fallback_paths)

    def find_spec(self, fullname, path=None, target=None):
        parts = fullname.split('.')
        for root in self._roots:
            pkg_dir = root.joinpath(*parts)
            init_py = pkg_dir / '__init__.py'
            if init_py.is_file():
                search = []
                for entry in (*self._roots, *self._fallback):
                    cand = pathlib.Path(entry).joinpath(*parts)
                    if cand.is_dir():
                        sc = str(cand)
                        if sc not in search:
                            search.append(sc)
                return importlib.util.spec_from_file_location(
                    fullname, str(init_py), submodule_search_locations=search
                )
            mod_py = root.joinpath(*parts[:-1], parts[-1] + '.py')
            if mod_py.is_file():
                return importlib.util.spec_from_file_location(fullname, str(mod_py))
        return None

def _bootstrap():
    sentinel_path, nonce, cwd_str, mode, target, *rest = sys.argv[1:]
    staged_root = pathlib.Path(cwd_str).resolve()
    staged_src = staged_root / 'src'
    roots = [staged_src, staged_root]
    fallback = [p for p in sys.path if p not in ('', str(staged_src), str(staged_root))]
    for r in reversed(roots):
        sr = str(r)
        if sr not in sys.path:
            sys.path.insert(0, sr)
    sys.meta_path.insert(0, _StagedOverlayFinder(roots, fallback))
    sys.argv = ['-c' if mode == '-c' else target, *rest]
    try:
        if mode == '-c':
            exec(compile(target, '<oracle>', 'exec'), {'__name__': '__main__', '__package__': None})
        elif mode == '-m':
            runpy.run_module(target, run_name='__main__', alter_sys=True)
        else:
            runpy.run_path(target, run_name='__main__')
    finally:
        pathlib.Path(sentinel_path).write_text(nonce, encoding='utf-8')

_bootstrap()
""".strip()


@dataclass(frozen=True)
class _PythonOracleCommand:
    mode: str
    target: str
    args: tuple[str, ...]


def _is_python_executable(exe: str) -> bool:
    stem = Path(exe).stem.lower()
    return stem == "py" or stem.startswith(_PYTHON_EXE_PREFIXES)


def _resolve_python_inline_target(
    mode: str, after: str, argv: Sequence[str], idx: int
) -> _PythonOracleCommand | None:
    if after:
        return _PythonOracleCommand(mode, after, tuple(argv[idx + 1 :]))
    if idx + 1 < len(argv):
        return _PythonOracleCommand(mode, argv[idx + 1], tuple(argv[idx + 2 :]))
    return None


def _consume_python_short_options(
    tok: str, argv: Sequence[str], idx: int
) -> tuple[int | None, _PythonOracleCommand | None]:
    """Scan short options in *tok*; return ``(next_idx_or_none, resolved_command)``."""
    for pos, ch in enumerate(tok[1:]):
        if ch in _PYTHON_ENTRY_FLAGS:
            return None, _resolve_python_inline_target(
                f"-{ch}", tok[_INLINE_FLAG_PREFIX_LEN + pos :], argv, idx
            )
        if ch in _PYTHON_VALUE_FLAGS:
            after = tok[_INLINE_FLAG_PREFIX_LEN + pos :]
            return (idx + 1 if after else idx + 2), None
    return idx + 1, None


def _split_python_oracle_command(argv: Sequence[str]) -> _PythonOracleCommand | None:
    """Extract ``('-c'|'-m'|'script', target, args)`` for Python oracle commands."""
    spec: _PythonOracleCommand | None = None
    if argv and _is_python_executable(argv[0]):
        idx = 1
        while idx < len(argv):
            tok = argv[idx]
            if tok == "--":
                if idx + 1 < len(argv):
                    spec = _PythonOracleCommand("script", argv[idx + 1], tuple(argv[idx + 2 :]))
                break
            if tok == "-":
                break
            if not tok.startswith("-"):
                spec = _PythonOracleCommand("script", tok, tuple(argv[idx + 1 :]))
                break
            if tok in _PYTHON_LONG_VALUE_OPTIONS:
                idx += 2
                continue
            if tok.startswith("--"):
                idx += 1
                continue
            next_idx, spec = _consume_python_short_options(tok, argv, idx)
            if next_idx is None:
                break
            idx = next_idx
    return spec


def _build_wrapped_argv(
    executable: str,
    *,
    sentinel_path: Path,
    nonce: str,
    cwd: Path,
    spec: _PythonOracleCommand,
) -> list[str]:
    return [
        executable,
        "-s",
        "-c",
        _TRUSTED_ORACLE_WRAPPER,
        str(sentinel_path),
        nonce,
        str(cwd),
        spec.mode,
        spec.target,
        *spec.args,
    ]


class NoIsolationBackend:
    """Host subprocess backend that preserves legacy behaviour and logs a warning."""

    name: str = "none"

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        interpreter: str = sys.executable,
    ) -> None:
        self._runner = runner
        self._interpreter = interpreter

    def is_available(self) -> bool:
        """Host subprocess execution is always available."""
        return True

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
    ) -> IsolationResult:
        """Run *argv* on the host after emitting an unisolated-oracle warning."""
        logger.warning(
            "Repair-sandbox oracle running WITHOUT OS-enforced isolation (backend='none')",
            extra={"event": "repair_oracle_unisolated", "backend": self.name},
        )
        resolved_argv = resolve_sandbox_argv(
            argv,
            interpreter=self._interpreter,
            backend_name=self.name,
            allow_host_interpreter=True,
        )
        resolved_cwd = cwd.resolve()
        reject_candidate_python_startup_hooks(resolved_cwd)
        staged_prefix = f"{resolved_cwd / 'src'}{os.pathsep}{resolved_cwd}"
        run_env = dict(env)
        existing_pythonpath = run_env.get("PYTHONPATH", "")
        run_env["PYTHONPATH"] = (
            f"{staged_prefix}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else staged_prefix
        )
        run_env["PYTHONNOUSERSITE"] = "1"
        if self._runner is None and os.name == "nt" and "SYSTEMROOT" in os.environ:
            # Windows Winsock / asyncio (_overlapped) fails with WinError 10106
            # inside live subprocesses when SYSTEMROOT is stripped from env.
            run_env.setdefault("SYSTEMROOT", os.environ["SYSTEMROOT"])
        python_spec = _split_python_oracle_command(resolved_argv) if self._runner is None else None
        if python_spec is not None:
            with tempfile.TemporaryDirectory(prefix="mangomas-oracle-") as sentinel_dir:
                sentinel_path = Path(sentinel_dir) / "oracle.done"
                nonce = secrets.token_hex(16)
                wrapped_argv = _build_wrapped_argv(
                    resolved_argv[0],
                    sentinel_path=sentinel_path,
                    nonce=nonce,
                    cwd=resolved_cwd,
                    spec=python_spec,
                )
                completed = self._invoke_subprocess(
                    wrapped_argv,
                    cwd=resolved_cwd,
                    env=run_env,
                    timeout_seconds=timeout_seconds,
                )
                recorded = (
                    sentinel_path.read_text(encoding="utf-8") if sentinel_path.is_file() else ""
                )
                if not hmac.compare_digest(recorded, nonce):
                    raise ToolExecutionError(
                        "Oracle exited without completing verification sentinel",
                        tool_name="repair_sandbox",
                    )
        else:
            completed = self._invoke_subprocess(
                resolved_argv,
                cwd=resolved_cwd,
                env=run_env,
                timeout_seconds=timeout_seconds,
            )
        return IsolationResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            backend=self.name,
        )

    def _invoke_subprocess(
        self,
        resolved_argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        resolved_cwd = cwd
        run_env = env
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        runner = subprocess.run if self._runner is None else self._runner
        try:
            completed = runner(
                resolved_argv,
                cwd=resolved_cwd,
                env=run_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(
                f"Oracle subprocess exceeded timeout of {timeout_seconds}s",
                tool_name="repair_sandbox",
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(
                f"Oracle subprocess failed to start: {exc}",
                tool_name="repair_sandbox",
            ) from exc
        return completed
