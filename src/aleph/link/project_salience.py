"""Project-wide salience scoring and attention budget (Phase 2.2).

Extends single-file fan-in to cross-file fan-in and assigns attention levels.
"""

from __future__ import annotations

import os
from collections import defaultdict

from aleph.model.components import (
    ProjectSalienceComponent, ProjectSalienceEntry,
    ProjectAttentionComponent, ProjectAttentionEntry,
)
from aleph.model.enums import AttentionLevel


# Attention thresholds (salience score boundaries)
CRITICAL_THRESHOLD = 0.7
IMPORTANT_THRESHOLD = 0.3
PERIPHERAL_THRESHOLD = 0.05


def compute_project_salience(
    root: str,
    file_results: dict[str, dict],
    cross_refs: list | None = None,
) -> ProjectSalienceComponent:
    """Compute project-wide salience from aggregated call graph data.

    For each symbol, salience = normalized(local_fan_in + cross_file_fan_in).
    Cross-file fan-in counts how many files reference a symbol, weighted
    higher than local fan-in because cross-file dependencies are structurally
    more important.

    Args:
        root: Project root directory.
        file_results: Dict of source_file -> run_pipeline() result.
        cross_refs: Optional pre-computed cross-file references (from builder).

    Returns:
        ProjectSalienceComponent with normalized 0-1 scores.
    """
    # Pass 1: Build all symbol data + local fan-in + cross-file fan-in in one sweep
    symbol_file: dict[str, str] = {}
    symbol_name: dict[str, str] = {}
    symbol_private: dict[str, bool] = {}
    local_fan_in: dict[str, int] = defaultdict(int)
    cross_file_fan_in: dict[str, int] = defaultdict(int)
    caller_files: dict[str, set[str]] = defaultdict(set)

    # Cache file classification to avoid repeated path pattern matching
    file_class: dict[str, tuple[bool, bool]] = {}  # path -> (is_test, is_vendor)

    # First: extract all symbol data (must complete before cross-file edge counting)
    for source_file, result in file_results.items():
        rel_path = os.path.relpath(source_file, root)
        if rel_path not in file_class:
            file_class[rel_path] = (_is_test_file(rel_path), _is_vendor_file(rel_path))
        for sym in result["symbols"]:
            sid = str(sym.id)
            symbol_file[sid] = rel_path
            symbol_name[sid] = sym.raw.qualified_name
            local_fan_in[sid] = len(sym.called_by)
            name = sym.raw.name
            symbol_private[sid] = name.startswith("_") and not name.startswith("__")

    # Then: count cross-file edges (all symbol_file entries populated)
    for source_file, result in file_results.items():
        rel_path = os.path.relpath(source_file, root)
        struct = result["struct_component"]
        for caller_id, callee_id in struct.call_edges:
            callee_file = symbol_file.get(callee_id)
            if callee_file and callee_file != rel_path:
                cross_file_fan_in[callee_id] += 1
                caller_files[callee_id].add(rel_path)

    # Count cross-file references from post-hoc resolution
    if cross_refs:
        for xref in cross_refs:
            callee_id = xref.callee_id
            if callee_id in symbol_file:
                cross_file_fan_in[callee_id] += 1
                caller_files[callee_id].add(xref.source_file)

    # Pass 2: Compute scores + normalize + create entries
    raw_scores: dict[str, float] = {}
    for sid, rel_path in symbol_file.items():
        is_test, is_vendor = file_class.get(rel_path, (False, False))
        test_factor = 0.25 if is_test else 1.0
        private_factor = 0.5 if symbol_private.get(sid, False) else 1.0
        vendor_factor = 0.1 if is_vendor else 1.0
        file_diversity_bonus = len(caller_files.get(sid, set())) * 1.5
        raw = (local_fan_in.get(sid, 0) + 2 * cross_file_fan_in.get(sid, 0) + file_diversity_bonus) * test_factor * private_factor * vendor_factor
        raw_scores[sid] = raw

    max_score = max(raw_scores.values()) if raw_scores else 0
    entries: list[ProjectSalienceEntry] = []
    for sid in sorted(symbol_file.keys()):
        score = raw_scores[sid] / max_score if max_score > 0 else 0.0
        entries.append(ProjectSalienceEntry(
            symbol_id=sid,
            qualified_name=symbol_name.get(sid, ""),
            file=symbol_file[sid],
            score=round(score, 4),
            local_fan_in=local_fan_in.get(sid, 0),
            cross_file_fan_in=cross_file_fan_in.get(sid, 0),
            total_fan_in=local_fan_in.get(sid, 0) + cross_file_fan_in.get(sid, 0),
        ))

    entries.sort(key=lambda e: (-e.score, e.file, e.symbol_id))

    return ProjectSalienceComponent(root=root, entries=entries)


def compute_attention_budget(
    salience: ProjectSalienceComponent,
) -> ProjectAttentionComponent:
    """Classify symbols into attention levels based on salience scores.

    Thresholds:
        >= 0.7  → CRITICAL   (must always be in context)
        >= 0.3  → IMPORTANT  (include when relevant)
        >= 0.05 → PERIPHERAL (include on demand)
        < 0.05  → SKIP       (omit unless explicitly requested)

    Returns:
        ProjectAttentionComponent with classified entries and budget summary.
    """
    entries: list[ProjectAttentionEntry] = []
    budget: dict[str, int] = {
        AttentionLevel.CRITICAL.value: 0,
        AttentionLevel.IMPORTANT.value: 0,
        AttentionLevel.PERIPHERAL.value: 0,
        AttentionLevel.SKIP.value: 0,
    }

    # Adaptive thresholds for large projects (> 1000 symbols)
    total = len(salience.entries)
    if total > 1000:
        scores_desc = sorted([e.score for e in salience.entries], reverse=True)
        critical_n = min(max(50, int(total * 0.005)), len(scores_desc))
        important_n = min(max(200, int(total * 0.02)), len(scores_desc))
        critical_thresh = scores_desc[critical_n - 1] if critical_n > 0 else CRITICAL_THRESHOLD
        important_thresh = scores_desc[important_n - 1] if important_n > 0 else IMPORTANT_THRESHOLD
        critical_thresh = max(critical_thresh, 0.01)
        important_thresh = max(min(important_thresh, critical_thresh * 0.5), 0.001)
        peripheral_thresh = PERIPHERAL_THRESHOLD
    else:
        critical_thresh = CRITICAL_THRESHOLD
        important_thresh = IMPORTANT_THRESHOLD
        peripheral_thresh = PERIPHERAL_THRESHOLD

    for sal in salience.entries:
        if sal.score >= critical_thresh:
            level = AttentionLevel.CRITICAL
        elif sal.score >= important_thresh:
            level = AttentionLevel.IMPORTANT
        elif sal.score >= peripheral_thresh:
            level = AttentionLevel.PERIPHERAL
        else:
            level = AttentionLevel.SKIP

        budget[level.value] += 1
        entries.append(ProjectAttentionEntry(
            symbol_id=sal.symbol_id,
            qualified_name=sal.qualified_name,
            file=sal.file,
            level=level,
            score=sal.score,
        ))

    return ProjectAttentionComponent(
        root=salience.root,
        entries=entries,
        budget=budget,
    )


def _is_test_file(rel_path: str) -> bool:
    """Detect test files by convention (Python, Rust, TypeScript/JavaScript)."""
    basename = os.path.basename(rel_path)
    name_no_ext = os.path.splitext(basename)[0]
    # Python: test_*.py, */tests/*
    if basename.startswith("test_"):
        return True
    # TS/JS: *.test.ts, *.spec.ts, *.test.js, *.spec.js
    if ".test." in basename or ".spec." in basename:
        return True
    # Directory-based: /tests/, /test/, /__tests__/
    normalized = rel_path.replace(os.sep, "/")
    if "/tests/" in normalized or "/test/" in normalized or "/__tests__/" in normalized:
        return True
    # Rust: tests/ directory already covered above
    return False


_VENDOR_PATTERNS = (
    "vendor/", "vendors/", "third_party/", "third-party/",
    "external/", "extern/", "deps/", "node_modules/",
    "_vendor/", ".cargo/registry/",
)


def _is_vendor_file(rel_path: str) -> bool:
    """Detect vendored/third-party code by path convention."""
    normalized = rel_path.replace(os.sep, "/").lower()
    return any(normalized.startswith(p) or f"/{p}" in normalized for p in _VENDOR_PATTERNS)
