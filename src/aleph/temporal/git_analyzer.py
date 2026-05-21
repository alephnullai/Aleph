"""Project-level temporal analysis: aggregate per-symbol git history across all files."""

from __future__ import annotations

import os
from datetime import datetime

from aleph.model.components import (
    ProjectTemporalComponent,
    ProjectTemporalEntry,
    TemporalComponent,
)


def _churn_label(churn_count: int) -> str:
    """Classify churn count into low/medium/high."""
    if churn_count >= 3:
        return "high"
    elif churn_count >= 1:
        return "medium"
    return "low"


def compute_project_temporal(
    root: str,
    file_results: dict[str, dict],
    reference_date: datetime | None = None,
) -> ProjectTemporalComponent:
    """Aggregate per-file temporal components into project-level temporal data.

    Collects TemporalEntry objects from each file's pipeline result and
    merges them into a single ProjectTemporalComponent with per-symbol
    entries including age, last_modified, churn, and stability.
    """
    now = reference_date or datetime.now()
    computed_date = now.strftime("%Y-%m-%d")

    entries: list[ProjectTemporalEntry] = []

    for source_file, result in sorted(file_results.items()):
        rel_path = os.path.relpath(source_file, root)
        temporal: TemporalComponent | None = result.get("temporal_component")
        symbols = result.get("symbols", [])

        # Build symbol lookup for qualified names
        sym_by_id: dict[str, str] = {}
        for sym in symbols:
            sym_by_id[str(sym.id)] = sym.raw.qualified_name

        if temporal is None:
            continue

        for te in temporal.entries:
            sid = str(te.symbol_id)
            entries.append(ProjectTemporalEntry(
                symbol_id=sid,
                qualified_name=sym_by_id.get(sid, sid),
                file=rel_path,
                age_days=te.age_days,
                last_modified_days=te.last_modified_days,
                churn_count=te.churn_count,
                churn_label=_churn_label(te.churn_count),
                stability=te.stability,
            ))

    # Sort: volatile first (highest churn), then by churn descending
    stability_order = {"volatile": 0, "active": 1, "stable": 2}
    entries.sort(key=lambda e: (stability_order.get(e.stability, 3), -e.churn_count, e.file, e.symbol_id))

    # Detect insufficient history: all entries have identical temporal data
    insufficient = False
    if entries:
        stabilities = {e.stability for e in entries}
        ages = {e.age_days for e in entries}
        churns = {e.churn_count for e in entries}
        if len(stabilities) <= 1 and len(ages) <= 1 and len(churns) <= 1:
            insufficient = True

    return ProjectTemporalComponent(
        root=root,
        computed_date=computed_date,
        entries=entries,
        insufficient_history=insufficient,
    )
