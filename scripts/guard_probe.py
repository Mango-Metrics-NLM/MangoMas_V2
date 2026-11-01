"""Prove a guard can still fire (spec-0033).

Each manifest entry names a file, the fixed snippet, the defect to put back,
and the gate command that must go red under that defect and green once the
original bytes are restored. The script refuses a dirty tree unless
``--allow-dirty`` is passed, and it restores the file in ``finally`` so an
exception cannot leave the mutation behind.

``{python}`` or a leading ``python`` in a gate command is rewritten to this
interpreter. Everything else gate-specific lives in the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_PROBE = 1
_PYTHON_TOKENS = frozenset({"{python}", "python"})


class ProbeError(Exception):
    """A manifest, mutation, or gate result the probe will not treat as success."""


@dataclass(frozen=True)
class ProbeEntry:
    """One guarded gate and the mutation that must disarm it."""

    entry_id: str
    path: str
    fixed: str
    defect: str
    gate: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_manifest() -> Path:
    return _repo_root() / "scripts" / "guard_probe_manifest.json"


def load_manifest(path: Path) -> list[ProbeEntry]:
    """Read *path*. An empty, missing, or unreadable manifest is a probe failure."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"manifest unreadable: {path}") from exc
    raw_entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ProbeError(f"manifest {path} has no entries")
    entries: list[ProbeEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ProbeError("manifest entry is not an object")
        try:
            gate = raw["gate"]
            entry = ProbeEntry(
                entry_id=str(raw["id"]),
                path=str(raw["path"]),
                fixed=str(raw["fixed"]),
                defect=str(raw["defect"]),
                gate=tuple(str(part) for part in gate),
            )
        except (KeyError, TypeError) as exc:
            raise ProbeError(f"manifest entry is missing a field: {exc}") from exc
        if not entry.gate:
            raise ProbeError(f"{entry.entry_id}: gate command is empty")
        entries.append(entry)
    return entries


def git_status_porcelain(cwd: Path) -> str:
    """Return ``git status --porcelain`` stdout, or raise if git cannot answer."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise ProbeError(f"git status failed in {cwd}: {detail}")
    return proc.stdout


def tree_is_dirty(cwd: Path) -> bool:
    return bool(git_status_porcelain(cwd).strip())


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def apply_once(text: str, fixed: str, defect: str) -> str:
    """Replace the one LF-normalized *fixed* snippet, preserving the file's newlines."""
    normalized = _to_lf(text)
    needle = _to_lf(fixed)
    found = normalized.count(needle)
    if found != 1:
        raise ProbeError(f"fixed snippet found {found} times, expected 1")
    mutated = normalized.replace(needle, _to_lf(defect), 1)
    style = _newline(text)
    if style != "\n":
        return mutated.replace("\n", style)
    return mutated


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_command(gate: tuple[str, ...]) -> list[str]:
    """Point a manifest's ``python`` token at the interpreter running the probe."""
    command = list(gate)
    if command[0] in _PYTHON_TOKENS:
        command[0] = sys.executable
    return command


def run_gate(gate: tuple[str, ...], cwd: Path) -> int:
    proc = subprocess.run(resolve_command(gate), cwd=cwd, check=False)
    return proc.returncode


def drop_bytecode(target: Path) -> None:
    """Evict *target*'s cached bytecode so the next gate compiles the bytes on disk.

    CPython validates a ``.pyc`` by the source's mtime in whole seconds and its
    size. A mutation that keeps the byte count (moving a line, swapping two
    tokens of equal length) that is written, gated and restored inside one
    second leaves the cache compiled from the *mutated* source valid for the
    restored file, so the post-restore gate imports the defect and the probe
    reports "stayed red after restore" on a correct tree. Seen in CI for
    ``MUT-0040-M06``; harmless locally only because the gate happened to cross
    a second boundary. Non-Python targets have no cache to evict.
    """
    if target.suffix != ".py":
        return
    Path(importlib.util.cache_from_source(str(target))).unlink(missing_ok=True)


def probe_entry(root: Path, entry: ProbeEntry) -> None:
    """Mutate, require red, restore, require green. Restore runs on every exit.

    The restore puts back the original bytes **and** the original mtime, and the
    target's bytecode cache is dropped before each gate, so neither gate can
    run against stale compiled code (see :func:`drop_bytecode`).
    """
    target = root / entry.path
    original = target.read_bytes()
    original_stat = target.stat()
    digest = content_sha256(original)
    logger.info("probing %s", entry.entry_id, extra={"event": "guard_probe_entry"})
    try:
        mutated = apply_once(original.decode("utf-8"), entry.fixed, entry.defect)
        target.write_bytes(mutated.encode("utf-8"))
        drop_bytecode(target)
        if run_gate(entry.gate, root) == 0:
            raise ProbeError(f"{entry.entry_id}: gate stayed green under the mutation")
    finally:
        target.write_bytes(original)
        os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        drop_bytecode(target)
    restored = target.read_bytes()
    if restored != original or content_sha256(restored) != digest:
        raise ProbeError(f"{entry.entry_id}: restore did not match the original bytes")
    if run_gate(entry.gate, root) != 0:
        raise ProbeError(f"{entry.entry_id}: gate stayed red after restore")


def run_probe(root: Path, manifest: Path, *, allow_dirty: bool) -> None:
    entries = load_manifest(manifest)
    if not allow_dirty and tree_is_dirty(root):
        raise ProbeError("working tree is dirty; pass --allow-dirty to probe anyway")
    for entry in entries:
        probe_entry(root, entry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove manifest guards still fire.")
    parser.add_argument("--manifest", type=Path, default=_default_manifest())
    parser.add_argument("--root", type=Path, default=_repo_root())
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run_probe(args.root, args.manifest, allow_dirty=args.allow_dirty)
    except ProbeError as exc:
        logger.error("%s", exc)
        return EXIT_PROBE
    logger.info("guard probe passed")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
