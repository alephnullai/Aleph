"""Regenerate the text artifact set from the SQLite store.

``aleph export`` rebuilds the full project.aleph.* artifact files plus
.aleph.index.json from aleph.db — the old emit path, now an export
format. Components are reconstructed with the exact same sort orders the
builder uses so exported artifacts are equivalent to builder-written
ones.
"""

from __future__ import annotations

import os

from aleph.emit.file_components import FileComponentWriter
from aleph.link.project_salience import compute_attention_budget
from aleph.model.components import (
    ProjectMapComponent, ProjectFileEntry,
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectFSComponent, ProjectFSEntry, ProjectModuleDep,
    ProjectStructComponent, ProjectCrossRef, ProjectFileDep,
    ProjectSalienceComponent, ProjectSalienceEntry,
    ProjectTemporalComponent, ProjectTemporalEntry,
    ProjectCoverageComponent, ProjectCoverageEntry,
)
from aleph.store.sqlite_store import SqliteStore, DB_FILENAME, abs_from_rel
from aleph.util.hashing import byte_hash

# Builder parity: aleph.temporal.git_analyzer sorts entries this way.
_STABILITY_ORDER = {"volatile": 0, "active": 1, "stable": 2}

ARTIFACT_NAMES = [
    "project.aleph.map",
    "project.aleph.dict",
    "project.aleph.fs",
    "project.aleph.struct",
    "project.aleph.salience",
    "project.aleph.attention",
    "project.aleph.temporal",
    "project.aleph.coverage",
    ".aleph.index.json",
]


def export_text_artifacts(root: str, output_dir: str | None = None) -> list[str]:
    """Write the full text artifact set from <root>/.aleph/aleph.db.

    Returns the list of paths written. Raises FileNotFoundError when no
    db exists.
    """
    root = os.path.abspath(root)
    db_path = os.path.join(root, ".aleph", DB_FILENAME)
    if not os.path.isfile(db_path):
        raise FileNotFoundError(
            f"No SQLite store at {db_path}. Run `aleph build` first."
        )
    output_dir = output_dir or os.path.join(root, ".aleph")
    store = SqliteStore(db_path)
    try:
        return _export(store, root, output_dir)
    finally:
        store.close()


def _export(store: SqliteStore, root: str, output_dir: str) -> list[str]:
    writer = FileComponentWriter(output_dir)
    written: list[str] = []

    file_rows = store.file_rows()

    # map + fs file entries
    map_entries: list[ProjectFileEntry] = []
    fs_entries: list[ProjectFSEntry] = []
    for f in file_rows:
        map_entries.append(ProjectFileEntry(
            path=f["path"],
            language=f["lang"],
            semantic_hash=f["semantic_hash"],
            symbol_count=f["symbol_count"],
            call_edge_count=f["call_edge_count"],
            original_tokens=f["original_tokens"],
            compressed_tokens=f["compressed_tokens"],
            reduction_percent=f["reduction_percent"],
        ))
        fs_entries.append(ProjectFSEntry(
            path=f["path"], language=f["lang"], symbol_count=f["symbol_count"],
        ))

    # dict entries — same construction as the builder (sig hash from
    # signature text, 1-based span lines, language falls back to the
    # file's language).
    dict_entries: list[ProjectSymbolEntry] = []
    for f in file_rows:
        for s in store.symbol_rows_for_file(f["id"]):
            sig = s["signature"]
            dict_entries.append(ProjectSymbolEntry(
                symbol_id=s["id"],
                name=s["name"],
                qualified_name=s["qualified_name"],
                kind=s["kind"],
                scope=s["scope"],
                file=f["path"],
                signature_hash=byte_hash(sig)[:8] if sig else "",
                start_line=s["span_start"] + 1,
                end_line=s["span_end"] + 1,
                language=s["language"] or f["lang"],
            ))
    dict_entries.sort(key=lambda e: (e.file, e.symbol_id))

    # struct cross-refs + file/module deps (deps are the per-pair
    # cross-ref counts, exactly how the builder tallies them)
    cross_refs: list[ProjectCrossRef] = []
    dep_counts: dict[tuple[str, str], int] = {}
    for e in store.cross_edge_rows():
        cross_refs.append(ProjectCrossRef(
            caller_id=e["caller_id"],
            callee_id=e["callee_id"],
            source_file=e["source_file"] or "",
            target_file=e["target_file"] or "",
            caller_name=e["caller_name"] or "",
            callee_name=e["callee_name"] or "",
        ))
        key = (e["source_file"] or "", e["target_file"] or "")
        dep_counts[key] = dep_counts.get(key, 0) + 1
    cross_refs.sort(key=lambda x: (x.source_file, x.caller_id, x.callee_id))
    file_deps = [
        ProjectFileDep(source=src, target=tgt, symbol_refs=count)
        for (src, tgt), count in sorted(dep_counts.items())
    ]
    module_deps = [
        ProjectModuleDep(source=src, target=tgt, symbol_count=count)
        for (src, tgt), count in sorted(dep_counts.items())
    ]

    # salience (+ attention, a pure function of salience)
    salience_entries = [
        ProjectSalienceEntry(
            symbol_id=r["symbol_id"],
            qualified_name=r["qualified_name"],
            file=r["file"],
            score=r["score"],
            local_fan_in=r["local_fan_in"],
            cross_file_fan_in=r["cross_file_fan_in"],
            total_fan_in=r["total_fan_in"],
        )
        for r in store.salience_rows()
    ]
    salience_entries.sort(key=lambda e: (-e.score, e.file, e.symbol_id))
    salience_component = ProjectSalienceComponent(root=root, entries=salience_entries)
    attention_component = compute_attention_budget(salience_component)

    # temporal
    temporal_entries = [
        ProjectTemporalEntry(
            symbol_id=r["symbol_id"],
            qualified_name=r["qualified_name"],
            file=r["file"],
            age_days=r["age_days"],
            last_modified_days=r["last_modified_days"],
            churn_count=r["churn"],
            churn_label=r["churn_label"],
            stability=r["stability"],
        )
        for r in store.temporal_rows()
    ]
    temporal_entries.sort(key=lambda e: (
        _STABILITY_ORDER.get(e.stability, 3), -e.churn_count, e.file, e.symbol_id,
    ))
    temporal_component = ProjectTemporalComponent(
        root=root,
        computed_date=store.get_meta("temporal_computed_date") or "",
        entries=temporal_entries,
        insufficient_history=store.get_meta("temporal_insufficient_history") == "1",
    )

    # coverage
    coverage_entries = [
        ProjectCoverageEntry(
            symbol_id=r["symbol_id"],
            qualified_name=r["qualified_name"],
            file=r["file"],
            status=r["status"],
            test_count=r["test_count"],
        )
        for r in store.coverage_rows()
    ]
    coverage_entries.sort(key=lambda e: (e.file, e.symbol_id))
    coverage_component = ProjectCoverageComponent(
        root=root,
        symbols_total=len(coverage_entries),
        covered=sum(1 for e in coverage_entries if e.status == "covered"),
        partial=sum(1 for e in coverage_entries if e.status == "partial"),
        none_count=sum(1 for e in coverage_entries if e.status == "none"),
        entries=coverage_entries,
    )

    written.append(writer.write_project_map(
        ProjectMapComponent(root=root, files=map_entries)))
    written.append(writer.write_project_dict(
        ProjectDictComponent(root=root, symbols=dict_entries)))
    written.append(writer.write_project_fs(
        ProjectFSComponent(root=root, files=fs_entries, module_deps=module_deps)))
    written.append(writer.write_project_struct(
        ProjectStructComponent(root=root, cross_refs=cross_refs, file_deps=file_deps)))
    written.append(writer.write_project_salience(salience_component))
    written.append(writer.write_project_attention(attention_component))
    written.append(writer.write_project_temporal(temporal_component))
    written.append(writer.write_project_coverage(
        coverage_component, salience=salience_component))

    written.append(_export_index_json(store, root, output_dir, file_rows))
    return written


def _export_index_json(store, root, output_dir, file_rows) -> str:
    """Rebuild .aleph.index.json (pipeline.build_index_from_result parity)."""
    from aleph.pipeline import save_index

    files: dict[str, dict] = {}
    for f in file_rows:
        abs_path = abs_from_rel(f["path"], root)
        symbols = []
        signature_hashes: dict[str, str] = {}
        body_hashes: dict[str, str] = {}
        for s in store.symbol_rows_for_file(f["id"]):
            sid = s["id"]
            sig = s["signature"]
            body = s["body_text"] or ""
            symbols.append({
                "id": sid,
                "name": s["name"],
                "qualified_name": s["qualified_name"],
                "kind": s["kind"],
                "scope": s["scope"],
                # Stored POSIX rel path, written as-is — the builder also
                # writes POSIX everywhere (rel_posix) since the Windows
                # parity fix; converting to os.sep would re-break export
                # byte-identity on Windows.
                "file": f["path"],
                # Quirk preserved from pipeline.build_index_from_result:
                # the per-symbol field is the first 8 chars of the raw
                # signature text, not a hash.
                "signature_hash": sig[:8] if sig else "",
            })
            if sig:
                signature_hashes[sid] = byte_hash(sig)[:8]
            if body:
                body_hashes[sid] = byte_hash(body)[:8]
        files[abs_path] = {
            "symbols": symbols,
            "calls": store.local_edges_for_file(f["id"]),
            "semantic_hash": f["semantic_hash"],
            "signature_hashes": signature_hashes,
            "body_hashes": body_hashes,
        }
    payload = {"version": "2.0", "root": root, "files": files}
    index_path = os.path.join(output_dir, ".aleph.index.json")
    save_index(index_path, payload)
    return index_path
