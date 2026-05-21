"""Project-level indexing and query helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aleph.ingest.languages import LanguageRegistry
from aleph.util.hashing import byte_hash


@dataclass
class IndexStats:
    indexed_files: int = 0
    reused_files: int = 0
    skipped_files: int = 0


# Directory names to skip during discovery
_SKIP_DIRS = frozenset({
    ".venv", "venv", ".env", "env",
    ".git", ".hg", ".svn",
    ".hypothesis", ".pytest_cache", "__pycache__",
    "node_modules", ".tox", ".mypy_cache", ".ruff_cache",
    "build", "dist", ".eggs",
    # Language-specific build output
    "target",          # Rust (cargo build, rustdoc)
    "out", "output",   # Common build output dirs
    "bin", "obj",      # C#/.NET build output
    "pkg",             # Go build output
    ".next",           # Next.js build output
    "coverage",        # Test coverage reports
})


def _load_alephignore(root: str) -> set[str]:
    """Load project-specific ignore patterns from .alephignore.

    Each line is a directory name to skip. Lines starting with # are comments.
    Example .alephignore:
        # Skip generated docs
        generated
        docs/api
    """
    ignore_path = os.path.join(root, ".alephignore")
    if not os.path.isfile(ignore_path):
        return set()
    patterns = set()
    with open(ignore_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.add(line.rstrip("/"))
    return patterns


def discover_source_files(root: str) -> list[str]:
    supported_exts = set(LanguageRegistry.supported_extensions())
    custom_ignores = _load_alephignore(root)
    skip = _SKIP_DIRS | custom_ignores

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place (prevents os.walk from descending)
        dirnames[:] = [
            d for d in dirnames
            if d not in skip and not d.startswith(".")
        ]
        for fname in filenames:
            if Path(fname).suffix.lower() in supported_exts:
                files.append(os.path.join(dirpath, fname))
    return sorted(files)


def load_index(path: str) -> dict:
    if not os.path.isfile(path):
        return {"version": "1.0", "root": "", "files": {}, "symbols": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def build_index(
    root: str,
    runner: Callable[[str], dict],
    previous: dict | None = None,
) -> tuple[dict, IndexStats]:
    previous = previous or {"files": {}}
    files: dict[str, dict] = {}
    symbols: dict[str, list[dict]] = {}
    stats = IndexStats()

    for source_file in discover_source_files(root):
        stat = os.stat(source_file)
        stamp = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
        prev = previous.get("files", {}).get(source_file)
        if prev and prev.get("stamp") == stamp:
            file_entry = prev
            stats.reused_files += 1
        else:
            result = runner(source_file)
            file_entry = {
                "language": result["language"],
                "semantic_hash": result["semantic_hash"],
                "symbols": [
                    {
                        "id": str(sym.id),
                        "name": sym.raw.name,
                        "qualified_name": sym.raw.qualified_name,
                        "kind": sym.raw.kind.value,
                        "scope": sym.raw.scope,
                    }
                    for sym in result["symbols"]
                ],
                "calls": result["struct_component"].call_edges,
                "signature_hashes": {
                    str(sym.id): byte_hash(sym.raw.signature_text)[:8]
                    for sym in result["symbols"]
                    if sym.raw.signature_text
                },
                "body_hashes": {
                    str(sym.id): byte_hash(sym.raw.body_text)[:8]
                    for sym in result["symbols"]
                    if sym.raw.body_text
                },
                "stamp": stamp,
            }
            stats.indexed_files += 1

        files[source_file] = file_entry
        for symbol in file_entry.get("symbols", []):
            for key in (symbol["name"], symbol["qualified_name"], symbol["id"]):
                symbols.setdefault(key, []).append(
                    {"file": source_file, **symbol}
                )

    payload = {
        "version": "1.0",
        "root": os.path.abspath(root),
        "files": files,
        "symbols": symbols,
    }
    return payload, stats


def query_symbols(index: dict, needle: str) -> list[dict]:
    exact = index.get("symbols", {}).get(needle, [])
    if exact:
        dedup_exact: dict[tuple[str, str], dict] = {}
        for item in exact:
            dedup_exact[(item["file"], item["id"])] = item
        return sorted(dedup_exact.values(), key=lambda x: (x["file"], x["qualified_name"]))
    needle_lower = needle.lower()
    out: list[dict] = []
    for key, values in index.get("symbols", {}).items():
        if needle_lower in key.lower():
            out.extend(values)
    dedup: dict[tuple[str, str], dict] = {}
    for item in out:
        dedup[(item["file"], item["id"])] = item
    return sorted(dedup.values(), key=lambda x: (x["file"], x["qualified_name"]))
