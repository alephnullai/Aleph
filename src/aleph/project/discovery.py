"""Source file discovery for project builds.

Migrated from the legacy ``aleph.project.indexer`` module (retired in
favor of the QueryEngine artifact pipeline); discovery is the part both
systems shared.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from aleph.ingest.languages import LanguageRegistry

# Vendored third-party code is skipped by default: it dominates parse
# time and store size and pollutes salience with library symbols (a
# vendor/ tree can be half the files in a project). Opt back in with
# ALEPH_INCLUDE_VENDOR=1 (CLI: `aleph build --include-vendor`) or a
# `!vendor` negation line in .alephignore.
_VENDOR_DIRS = frozenset({"vendor", "third_party", "thirdparty"})

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
}) | _VENDOR_DIRS


def _load_alephignore(root: str) -> tuple[set[str], set[str]]:
    """Load project-specific ignore patterns from .alephignore.

    Each line is a directory name to skip. Lines starting with # are
    comments. A leading ``!`` negates: ``!vendor`` re-includes a
    directory that the default skip list excludes.
    Example .alephignore:
        # Skip generated docs
        generated
        docs/api
        # ...but index our vendored fork
        !vendor

    Returns (ignores, negations).
    """
    ignore_path = os.path.join(root, ".alephignore")
    ignores: set[str] = set()
    negations: set[str] = set()
    if not os.path.isfile(ignore_path):
        return ignores, negations
    with open(ignore_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!"):
                negations.add(line[1:].strip().rstrip("/"))
            else:
                ignores.add(line.rstrip("/"))
    return ignores, negations


def _include_vendor_env() -> bool:
    """True when ALEPH_INCLUDE_VENDOR opts vendored code back in."""
    return os.environ.get("ALEPH_INCLUDE_VENDOR", "").strip().lower() in (
        "1", "true", "yes", "on")


def effective_skip_dirs(root: str) -> set[str]:
    """Directory names discovery (and temporal scoping) skips for *root*.

    The default skip list plus .alephignore entries, minus the vendor
    names when ALEPH_INCLUDE_VENDOR is set, minus any ``!dir``
    .alephignore negations.
    """
    ignores, negations = _load_alephignore(root)
    skip = set(_SKIP_DIRS) | ignores
    if _include_vendor_env():
        skip -= _VENDOR_DIRS
    skip -= negations
    return skip


def temporal_pathspecs(root: str) -> list[str]:
    """Git pathspecs scoping history scans to what the build indexes.

    The temporal phase shells out to ``git log --numstat``; unscoped,
    git computes per-file stats for every path in every commit — on
    repos with heavy binary churn (capture/golden/baseline dirs) that
    is effectively unbounded. This derives pathspecs from the same
    knowledge discovery uses:

      * one ``*<ext>`` glob per supported source extension (default
        pathspec matching is fnmatch without FNM_PATHNAME, so ``*.rs``
        matches nested paths too), and
      * ``:(exclude)`` specs for vendor/build dirs and ``.alephignore``
        entries (``effective_skip_dirs`` — the same set discovery
        prunes, including the vendor opt-in/negation handling) — both
        top-level (``dir``) and nested (``*/dir/*``).
    """
    skip = sorted(effective_skip_dirs(root))
    specs = [f"*{ext}" for ext in sorted(LanguageRegistry.supported_extensions())]
    for d in skip:
        d = d.rstrip("/")
        specs.append(f":(exclude){d}")
        specs.append(f":(exclude)*/{d}/*")
    return specs


def discover_source_files(
    root: str,
    on_warning: Callable[[str], None] | None = None,
) -> list[str]:
    """Recursively find all supported source files under root.

    on_warning: called once (when given) with a notice counting the
        vendor directories skipped by the default exclusion, so users
        can tell why vendored files are absent from the build.
    """
    supported_exts = set(LanguageRegistry.supported_extensions())
    skip = effective_skip_dirs(root)

    vendor_skipped = 0
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place (prevents os.walk from descending)
        kept: list[str] = []
        for d in dirnames:
            if d in skip or d.startswith("."):
                if d in _VENDOR_DIRS and d in skip:
                    vendor_skipped += 1
                continue
            kept.append(d)
        dirnames[:] = kept
        for fname in filenames:
            if Path(fname).suffix.lower() in supported_exts:
                files.append(os.path.join(dirpath, fname))
    if vendor_skipped and on_warning is not None:
        on_warning(
            f"discovery: skipped {vendor_skipped} vendor dir(s) "
            f"(vendor/third_party, excluded by default) — set "
            f"ALEPH_INCLUDE_VENDOR=1 or add '!vendor' to .alephignore "
            f"to index vendored code"
        )
    return sorted(files)
