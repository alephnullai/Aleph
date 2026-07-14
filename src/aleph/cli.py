"""Aleph CLI for single-file and project-level workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys

from aleph.emit.serializer import AlephSerializer
from aleph.emit.file_components import FileComponentWriter
from aleph.emit.loader import AlephLoader
from aleph.query.engine import QueryEngine
from aleph.diff.semantic_diff import SemanticDiff

# Canonical build entry points live in aleph.pipeline (moved out of the CLI
# so mcp.server/handlers no longer import the CLI). The re-exports keep the
# historical aleph.cli.run_pipeline / aleph.cli._auto_build names working.
from aleph.pipeline import (  # noqa: F401  (re-exported for backward compat)
    run_pipeline,
    auto_build as _auto_build,
    load_index,
    save_index,
)


def _json_print(payload: dict | list) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _default_index_path(root: str) -> str:
    aleph_dir = os.path.join(root, ".aleph")
    if os.path.isdir(aleph_dir):
        return os.path.join(aleph_dir, ".aleph.index.json")
    return os.path.join(root, ".aleph.index.json")


def _handle_compress(args) -> None:
    if not os.path.isfile(args.file):
        print(f"Error: {args.file} not found", file=sys.stderr)
        sys.exit(1)

    result = run_pipeline(args.file, args.output)
    serializer = AlephSerializer()
    bundle_json = serializer.serialize_bundle_json(
        result["struct_component"], result["bodies_component"]
    )
    if args.json:
        _json_print(
            {
                "source_file": result["source_file"],
                "language": result["language"],
                "symbols_extracted": result["symbols_extracted"],
                "call_edges": result["call_edges"],
                "semantic_hash": result["semantic_hash"],
                "original_tokens": result["original_tokens"],
                "compressed_tokens": result["compressed_tokens"],
                "token_reduction_percent": result["token_reduction_percent"],
                "call_edge_metadata": result["call_edge_metadata"],
                "intents_count": len(result["intents_component"].entries),
                "errors_count": len(result["errors_component"].sources),
                "tests_coverage_count": len(result["tests_component"].coverage),
                "bundle": json.loads(bundle_json),
            }
        )
        return

    print(f"File:       {result['source_file']}")
    print(f"Language:   {result['language']}")
    print(f"Symbols:    {result['symbols_extracted']}")
    print(f"Call edges: {result['call_edges']}")
    print(f"Sem hash:   {result['semantic_hash']}")
    print(f"Tokens:     {result['original_tokens']} -> {result['compressed_tokens']}")
    print(f"Reduction:  {result['token_reduction_percent']:.1f}%")
    print(f"Intents:    {len(result['intents_component'].entries)}")
    print(f"Errors:     {len(result['errors_component'].sources)}")
    print(f"Coverage:   {len(result['tests_component'].coverage)} symbols mapped")

    if args.output:
        writer = FileComponentWriter(args.output)
        writer.write_struct(result["struct_component"])
        writer.write_bodies(
            result["bodies_component"],
            include_original_bodies=args.include_original_bodies,
        )
        writer.write_temporal(result["temporal_component"])
        writer.write_intents(result["intents_component"])
        writer.write_errors(result["errors_component"])
        writer.write_tests(result["tests_component"])
        if args.bundle_json:
            writer.write_bundle_json(result["struct_component"], result["bodies_component"])
        print(f"\nOutput written to {args.output}/")
    else:
        print("\n--- .aleph.struct ---")
        print(result["struct_text"])
        print("--- .aleph.bodies ---")
        print(
            AlephSerializer().serialize_bodies(
                result["bodies_component"],
                include_original_bodies=args.include_original_bodies,
            )
        )


def _handle_index(args) -> None:
    """Deprecated alias for `aleph build` (the legacy v1.0 indexer is retired)."""
    print(
        "[aleph] Warning: `aleph index` is deprecated — it now runs `aleph build`. "
        "Use `aleph build` directly.",
        file=sys.stderr,
    )
    if getattr(args, "index_file", None):
        print(
            "[aleph] Warning: --index-file is ignored; the index is written to "
            "<root>/.aleph/.aleph.index.json by the build.",
            file=sys.stderr,
        )
    args.output = None
    args.full = False
    args.per_file = False
    _handle_build(args)


def _load_required_index(index_file: str | None, path_hint: str = ".") -> tuple[str, dict]:
    idx_path = index_file or _default_index_path(os.path.abspath(path_hint))
    if not os.path.isfile(idx_path):
        print(f"Error: index not found at {idx_path}. Run `aleph build` first.", file=sys.stderr)
        sys.exit(1)
    return idx_path, load_index(idx_path)


def _require_query_engine(project_dir: str) -> QueryEngine:
    """Build a QueryEngine, exiting with guidance when artifacts are missing."""
    root = os.path.abspath(project_dir)
    engine = QueryEngine(root)
    dict_path = os.path.join(engine._artifact_dir, "project.aleph.dict")
    if engine._store is None and not os.path.isfile(dict_path):
        print(
            f"Error: no .aleph artifacts found under {root}. Run `aleph build` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return engine


def _resolve_symbol_matches(engine: QueryEngine, symbol: str) -> list:
    """Resolve a symbol reference (ID, name, or qualified name) to entries."""
    exact = engine.resolve(symbol)
    if exact is not None:
        return [exact]
    return engine.find_by_name(symbol)


def _resolve_query_ref(engine: QueryEngine, ref: str, args):
    """Resolve an id-or-name ref for a `query` subcommand.

    Returns the resolved symbol id (str) to operate on, or exits the
    process after printing a diagnostic / emitting JSON. This is the CLI
    half of the trust contract: a NAME auto-resolves (with an echoed
    note), ambiguity lists candidates, and a true miss says 'no such
    symbol' — never a downstream silent-empty masquerading as a real
    'no callers'/'no body' answer.
    """
    sref = engine.resolve_ref(ref)
    if sref.status in ("id", "resolved"):
        if sref.status == "resolved" and not getattr(args, "json", False):
            print(f"[{sref.note}]", file=sys.stderr)
        return sref.symbol_id
    if sref.status == "ambiguous":
        if getattr(args, "json", False):
            _json_print({
                "query": ref, "ambiguous": True,
                "candidates": [c.to_dict() for c in sref.candidates],
            })
        else:
            print(
                f"Ambiguous: '{ref}' matches {len(sref.candidates)} symbols "
                f"— pass an id:",
                file=sys.stderr,
            )
            for c in sref.candidates:
                print(f"  {c.symbol_id} {c.qualified_name} ({c.file})", file=sys.stderr)
        sys.exit(1)
    # not_found
    if getattr(args, "json", False):
        _json_print({"query": ref, "error": f"no symbol named '{ref}'"})
    else:
        print(
            f"No symbol named '{ref}' (not an id and not a known name). "
            f"Try `aleph query SEARCH {ref}` to find it.",
            file=sys.stderr,
        )
    sys.exit(1)


def _handle_query(args) -> None:
    """Dispatch aleph query <COMMAND> <args> to the QueryEngine."""
    project_dir = os.path.abspath(args.project_dir)
    command = args.query_command.upper()

    engine = QueryEngine(project_dir)

    if command == "EXPAND":
        _query_expand(engine, args)
    elif command == "RESOLVE":
        _query_resolve(engine, args)
    elif command == "CALLERS":
        _query_callers(engine, args)
    elif command == "CONTEXT":
        _query_context(engine, args)
    elif command == "SEARCH":
        _query_search(engine, args)
    else:
        print(f"Error: unknown query command '{args.query_command}'", file=sys.stderr)
        print("Available: EXPAND, RESOLVE, CALLERS, CONTEXT, SEARCH", file=sys.stderr)
        sys.exit(1)


def _query_expand(engine: QueryEngine, args) -> None:
    symbol_id = _resolve_query_ref(engine, args.query_args, args)
    body = engine.expand(symbol_id)
    if body is None:
        # symbol_id is a confirmed-real symbol (resolve_ref passed): this is
        # genuinely 'no body recorded', not a failed lookup.
        print(
            f"No body recorded for {symbol_id} "
            f"(per-file bodies require: aleph build <dir> --per-file).",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.json:
        _json_print({"symbol_id": symbol_id, "body": body})
        return
    print(body)


def _query_resolve(engine: QueryEngine, args) -> None:
    sref = engine.resolve_ref(args.query_args)
    if sref.status == "ambiguous":
        if args.json:
            _json_print({
                "query": args.query_args, "ambiguous": True,
                "candidates": [c.to_dict() for c in sref.candidates],
            })
            return
        print(
            f"Ambiguous: '{args.query_args}' matches {len(sref.candidates)} "
            f"symbols — pass an id:",
            file=sys.stderr,
        )
        for c in sref.candidates:
            print(f"  {c.symbol_id} {c.qualified_name} ({c.file})", file=sys.stderr)
        sys.exit(1)
    if sref.status == "not_found":
        if args.json:
            _json_print({"query": args.query_args, "error": f"no symbol named '{args.query_args}'"})
            return
        print(
            f"No symbol named '{args.query_args}'. "
            f"Try `aleph query SEARCH {args.query_args}` to find it.",
            file=sys.stderr,
        )
        sys.exit(1)
    result = sref.entry
    if args.json:
        d = result.to_dict()
        if sref.note:
            d["note"] = sref.note
        _json_print(d)
        return
    if sref.note:
        print(f"[{sref.note}]", file=sys.stderr)
    print(f"ID:        {result.symbol_id}")
    print(f"Name:      {result.name}")
    print(f"Qualified: {result.qualified_name}")
    print(f"Kind:      {result.kind}")
    print(f"Scope:     {result.scope}")
    print(f"File:      {result.file}")
    if result.signature_hash:
        print(f"Sig hash:  {result.signature_hash}")


def _query_callers(engine: QueryEngine, args) -> None:
    symbol_id = _resolve_query_ref(engine, args.query_args, args)
    results = engine.callers(symbol_id)
    if args.json:
        _json_print({"symbol_id": symbol_id, "callers": [c.to_dict() for c in results]})
        return
    if not results:
        # symbol_id is confirmed-real: this genuinely has zero callers.
        print(f"{symbol_id} has no callers (0 — confirmed against the resolved symbol).")
        return
    print(f"Callers of {symbol_id}: {len(results)}")
    for c in results:
        print(f"  {c.caller_id} {c.caller_name} ({c.caller_file})")


def _query_context(engine: QueryEngine, args) -> None:
    symbol_id = _resolve_query_ref(engine, args.query_args, args)
    result = engine.context(symbol_id)
    if result is None:
        print(f"No symbol found for {symbol_id}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        _json_print(result.to_dict())
        return
    s = result.symbol
    print(f"Symbol: {s.symbol_id} {s.qualified_name} ({s.kind}) in {s.file}")
    if result.callers:
        print(f"Callers ({len(result.callers)}):")
        for c in result.callers:
            print(f"  <- {c.caller_id} {c.caller_name}")
    if result.callees:
        print(f"Callees ({len(result.callees)}):")
        for c in result.callees:
            print(f"  -> {c.symbol_id} {c.qualified_name}")


def _query_search(engine: QueryEngine, args) -> None:
    results = engine.search(args.query_args)
    if args.json:
        if results:
            _json_print({"query": args.query_args, "results": [r.to_dict() for r in results]})
            return
        nearest = engine.search_nearest(args.query_args)
        _json_print({
            "query": args.query_args, "results": [],
            "nearest": [r.to_dict() for r in nearest],
        })
        return
    if not results:
        # Never dead-end: offer nearest candidates or actionable guidance.
        nearest = engine.search_nearest(args.query_args)
        if nearest:
            names = ", ".join(r.qualified_name for r in nearest)
            print(f"No direct match for '{args.query_args}'; nearest: {names}")
        else:
            print(
                f"No match for '{args.query_args}'. Try a symbol name or file "
                f"path; for free-text content search, grep may fit better."
            )
        return
    print(f"Matches for '{args.query_args}': {len(results)}")
    for r in results:
        print(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")


def _handle_resolve(args) -> None:
    """Resolve a symbol ID/name/qualified name via the QueryEngine."""
    engine = _require_query_engine(args.project_dir)
    matches = _resolve_symbol_matches(engine, args.symbol)
    if not matches:
        # Fall back to lexical search for partial matches (parity with the
        # retired indexer's substring lookup).
        search = engine.search(args.symbol)
        matches = [engine.resolve(r.symbol_id) for r in search]
        matches = [m for m in matches if m is not None]
    if args.json:
        _json_print({
            "symbol": args.symbol,
            "matches": [m.to_dict() for m in matches],
        })
        return
    if not matches:
        print(f"No symbol match for {args.symbol}")
        return
    for m in matches:
        print(f"{m.symbol_id} -> {m.qualified_name} ({m.file})")


def _handle_neighbors(args) -> None:
    """Show call-graph neighbors for a symbol via the QueryEngine."""
    engine = _require_query_engine(args.project_dir)
    matches = _resolve_symbol_matches(engine, args.symbol)
    if not matches:
        if args.json:
            _json_print({"symbol": args.symbol, "neighbors": []})
        else:
            print(f"No symbol match for {args.symbol}")
        return

    neighbors: set[tuple[str, str, str]] = set()
    for m in matches:
        ctx = engine.context(m.symbol_id)
        if ctx is None:
            continue
        for caller in ctx.callers:
            neighbors.add((m.symbol_id, caller.caller_id, "in"))
        for callee in ctx.callees:
            neighbors.add((m.symbol_id, callee.symbol_id, "out"))

    payload = [
        {"symbol_id": src, "neighbor_id": dst, "direction": direction}
        for src, dst, direction in sorted(neighbors)
    ]
    if args.json:
        _json_print({"symbol": args.symbol, "neighbors": payload})
        return
    if not payload:
        print(f"No neighbors found for {args.symbol}")
        return
    for item in payload:
        arrow = "->" if item["direction"] == "out" else "<-"
        print(f"{item['symbol_id']} {arrow} {item['neighbor_id']}")


def _handle_expand(args) -> None:
    if not os.path.isfile(args.bodies_file):
        print(f"Error: {args.bodies_file} not found", file=sys.stderr)
        sys.exit(1)
    with open(args.bodies_file, "r", encoding="utf-8") as f:
        text = f.read()

    loader = AlephLoader()

    # Detect component type from header
    first_line = text.split("\n", 1)[0].strip()
    if "INTENTS" in first_line:
        component = loader.deserialize_intents(text)
        if args.json:
            _json_print({
                "source_file": component.source_file,
                "entries": [
                    {"symbol_id": str(e.symbol_id), "tag": e.tag_type, "desc": e.description}
                    for e in component.entries
                ],
            })
        else:
            for e in component.entries:
                print(f"{e.symbol_id} {e.tag_type}:{e.description}")
        return
    elif "ERRORS" in first_line:
        component = loader.deserialize_errors(text)
        if args.json:
            _json_print({"source_file": component.source_file, "sources": len(component.sources)})
        else:
            for s in component.sources:
                print(f"{s.symbol_id} {s.error_type} {s.propagation}")
        return
    elif "TESTS" in first_line:
        component = loader.deserialize_tests(text)
        if args.json:
            _json_print({"source_file": component.source_file, "coverage": len(component.coverage)})
        else:
            for c in component.coverage:
                print(f"{c.symbol_id} {c.status}")
        return
    elif "TEMPORAL" in first_line:
        component = loader.deserialize_temporal(text)
        if args.json:
            _json_print({"source_file": component.source_file, "entries": len(component.entries)})
        else:
            for e in component.entries:
                print(f"{e.symbol_id} stability={e.stability} churn={e.churn_count}")
        return

    # Default: bodies
    component = loader.deserialize_bodies(text)
    expanded = loader.expand_bodies(component)
    if args.symbol_id:
        expanded = {k: v for k, v in expanded.items() if k == args.symbol_id}
    if args.json:
        _json_print({"source_file": component.source_file, "expanded": expanded})
        return
    for sid, body in expanded.items():
        print(f"[{sid}]")
        print(body)


def _handle_build(args) -> None:
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output or os.path.join(root, ".aleph")
    text_artifacts = not getattr(args, "no_text_artifacts", False)
    if getattr(args, "include_vendor", False):
        # Discovery and temporal pathspecs read the env var directly, so
        # the flag works for every call site without extra plumbing.
        os.environ["ALEPH_INCLUDE_VENDOR"] = "1"
    # Progress goes to stderr (stdout stays clean for --json): on when
    # stderr is a TTY or ALEPH_PROGRESS=1, off with --quiet.
    from aleph.util.progress import ProgressReporter
    progress = ProgressReporter(quiet=getattr(args, "quiet", False))
    result = _auto_build(
        root,
        full=args.full,
        per_file=getattr(args, "per_file", False),
        text_artifacts=text_artifacts,
        semantic=getattr(args, "semantic", False),
        progress=progress,
    )
    stats = result.stats

    reduction = (
        (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
        if stats.total_original_tokens > 0 else 0.0
    )

    if args.json:
        _json_print({
            "root": root,
            "output_dir": output_dir,
            "total_files": stats.total_files,
            "total_symbols": stats.total_symbols,
            "total_call_edges": stats.total_call_edges,
            "total_cross_refs": stats.total_cross_refs,
            "total_original_tokens": stats.total_original_tokens,
            "total_compressed_tokens": stats.total_compressed_tokens,
            "total_reduction_percent": round(reduction, 1),
            "rebuilt_files": stats.rebuilt_files,
            "reused_files": stats.reused_files,
            "removed_files": stats.removed_files,
            "errors": stats.errors,
            "attention_budget": result.attention_component.budget,
            "per_file": getattr(args, "per_file", False),
            "artifacts": ["aleph.db"] + ([
                "project.aleph.map",
                "project.aleph.dict",
                "project.aleph.fs",
                "project.aleph.struct",
                "project.aleph.salience",
                "project.aleph.attention",
                "project.aleph.temporal",
                "project.aleph.coverage",
                ".aleph.index.json",
            ] if text_artifacts else []),
        })
        return

    print(f"Project:     {root}")
    print(f"Files:       {stats.total_files}")
    print(f"Symbols:     {stats.total_symbols}")
    print(f"Call edges:  {stats.total_call_edges}")
    print(f"Cross-refs:  {stats.total_cross_refs}")
    print(f"Tokens:      {stats.total_original_tokens} -> {stats.total_compressed_tokens}")
    print(f"Reduction:   {reduction:.1f}%")
    if stats.reused_files > 0:
        print(f"Incremental: {stats.reused_files} reused, {stats.rebuilt_files} rebuilt")
    if stats.removed_files > 0:
        print(f"Removed:     {stats.removed_files} stale cache entries")
    budget = result.attention_component.budget
    print(f"Attention:   {budget.get('critical', 0)} critical, "
          f"{budget.get('important', 0)} important, "
          f"{budget.get('peripheral', 0)} peripheral, "
          f"{budget.get('skip', 0)} skip")
    temporal = result.temporal_component
    volatile_count = sum(1 for e in temporal.entries if e.stability == "volatile")
    stable_count = sum(1 for e in temporal.entries if e.stability == "stable")
    print(f"Temporal:    {len(temporal.entries)} symbols "
          f"({volatile_count} volatile, {stable_count} stable)")
    if stats.errors:
        print(f"Errors:      {len(stats.errors)}")
        for err in stats.errors:
            print(f"  - {err}")
    cov = result.coverage_component
    print(f"Coverage:    {cov.symbols_total} symbols "
          f"({cov.covered} covered, {cov.partial} partial, {cov.none_count} none)")
    print(f"\nArtifacts written to {output_dir}/")
    print("  aleph.db (canonical store)")
    if text_artifacts:
        print("  project.aleph.map")
        print("  project.aleph.dict")
        print("  project.aleph.fs")
        print("  project.aleph.struct")
        print("  project.aleph.salience")
        print("  project.aleph.attention")
        print("  project.aleph.temporal")
        print("  project.aleph.coverage")
        print("  .aleph.index.json (not committed)")
    else:
        print("  (text artifacts skipped — regenerate with `aleph export`)")


def _handle_export(args) -> None:
    """Regenerate the text artifact set from the SQLite store (aleph.db)."""
    from aleph.store.export import export_text_artifacts

    root = os.path.abspath(args.path)
    try:
        written = export_text_artifacts(root, output_dir=args.output)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json_print({"root": root, "artifacts": written})
        return
    print(f"Exported {len(written)} artifacts from aleph.db:")
    for path in written:
        print(f"  {path}")


def _handle_migrate_ids(args) -> None:
    """Migrate legacy absolute-path symbol IDs to the portable v2 scheme."""
    from aleph.symbols.id_migration import migrate_ids

    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)
    try:
        report = migrate_ids(root, dry_run=args.dry_run, old_root=args.old_root)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(report.summary())


def _handle_watch(args) -> None:
    """Watch for file changes and rebuild incrementally."""
    import time
    root = os.path.abspath(args.path)
    interval = args.interval

    print(f"[aleph] Watching {root} (poll every {interval:.0f}s) — Ctrl+C to stop")

    # Initial build
    result = _auto_build(root)
    stats = result.stats
    reduction = (
        (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
        if stats.total_original_tokens > 0 else 0.0
    )
    print(f"[aleph] Initial: {stats.total_files} files, {stats.total_symbols} symbols, {reduction:.1f}%")

    # Track file stamps
    from aleph.project.discovery import discover_source_files
    from aleph.project.cache import FileStamp

    prev_stamps: dict[str, FileStamp] = {}
    for f in discover_source_files(root):
        try:
            prev_stamps[f] = FileStamp.from_file(f)
        except OSError:
            pass

    try:
        while True:
            time.sleep(interval)
            current_files = set(discover_source_files(root))
            prev_files = set(prev_stamps.keys())

            changed = []
            added = list(current_files - prev_files)
            removed = list(prev_files - current_files)

            for f in current_files & prev_files:
                try:
                    # Stat-only fast path: only read+hash on stat mismatch
                    if prev_stamps[f].stat_matches(FileStamp.from_stat(f)):
                        continue
                    current = FileStamp.from_file(f)
                    if current.content_hash != prev_stamps[f].content_hash:
                        changed.append(f)
                    # Refresh the stamp either way so an mtime-only change
                    # isn't re-hashed on every poll
                    prev_stamps[f] = current
                except OSError:
                    pass

            for f in added:
                try:
                    prev_stamps[f] = FileStamp.from_file(f)
                except OSError:
                    pass

            for f in removed:
                del prev_stamps[f]

            if changed or added or removed:
                n_changed = len(changed) + len(added) + len(removed)
                details = []
                if changed:
                    details.append(f"{len(changed)} modified")
                if added:
                    details.append(f"{len(added)} added")
                if removed:
                    details.append(f"{len(removed)} removed")
                print(f"[aleph] {', '.join(details)} — rebuilding...")

                result = _auto_build(root)
                stats = result.stats
                print(
                    f"[aleph] Rebuilt: {stats.rebuilt_files} files "
                    f"({stats.reused_files} cached)"
                )

                # Refresh stamps
                for f in discover_source_files(root):
                    try:
                        prev_stamps[f] = FileStamp.from_file(f)
                    except OSError:
                        pass

    except KeyboardInterrupt:
        print("\n[aleph] Watch stopped.")


def _handle_memory(args) -> None:
    """Dispatch aleph memory compress|resume."""
    sub = args.memory_command
    if sub == "compress":
        _handle_memory_compress(args)
    elif sub == "resume":
        _handle_memory_resume(args)
    else:
        print(f"Error: unknown memory command '{sub}'", file=sys.stderr)
        print("Available: compress, resume", file=sys.stderr)
        sys.exit(1)


def _handle_memory_compress(args) -> None:
    if not os.path.isfile(args.transcript_file):
        print(f"Error: {args.transcript_file} not found", file=sys.stderr)
        sys.exit(1)

    with open(args.transcript_file, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Accept either a list of messages or {"messages": [...]}
    if isinstance(raw, dict) and "messages" in raw:
        messages = raw["messages"]
    elif isinstance(raw, list):
        messages = raw
    else:
        print("Error: transcript must be a JSON list of messages or {\"messages\": [...]}", file=sys.stderr)
        sys.exit(1)

    from aleph.memory.compressor import compress_transcript, serialize_memory
    from aleph.memory.session_memory import save_memory

    memory = compress_transcript(messages)

    if args.json:
        _json_print({
            "message_count": memory.message_count,
            "entries": len(memory.entries),
            "symbols": len(memory.symbol_dict),
            "original_tokens": memory.original_token_estimate,
            "compressed_tokens": memory.compressed_token_estimate,
            "reduction_percent": round(memory.reduction_percent, 1),
        })
    else:
        print(serialize_memory(memory))

    # Save to epistemic layer if project dir specified
    if args.project_dir:
        path = save_memory(args.project_dir, memory, session_id=args.session_id)
        if not args.json:
            print(f"\nMemory saved to {path}")


def _handle_memory_resume(args) -> None:
    project_dir = os.path.abspath(args.project_dir)
    from aleph.memory.session_memory import resume_session_briefing

    briefing = resume_session_briefing(project_dir)
    if briefing is None:
        print("No prior session memory found.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        _json_print({
            "context_summary": briefing.context_summary,
            "inferences": [
                {"symbol_id": i.symbol_id, "conclusion": i.conclusion, "confidence": i.confidence}
                for i in briefing.inferences
            ],
            "flags": [
                {"symbol_id": f.symbol_id, "reason": f.reason, "verified": f.verified}
                for f in briefing.flags
            ],
            "patches": [
                {"patch_id": p.patch_id, "symbol_id": p.symbol_id, "intent": p.intent}
                for p in briefing.patches
            ],
            "decisions": briefing.decisions,
            "learned": briefing.learned,
        })
        return

    print(briefing.to_prompt())


def _handle_bench(args) -> None:
    """Dispatch aleph bench resume."""
    sub = args.bench_command
    if sub == "resume":
        _handle_bench_resume(args)
    else:
        print(f"Error: unknown bench command '{sub}'", file=sys.stderr)
        print("Available: resume", file=sys.stderr)
        sys.exit(1)


def _handle_bench_resume(args) -> None:
    from aleph.memory.bench import run_bench_resume

    result = run_bench_resume(verbose=getattr(args, "verbose", False))

    if args.json:
        _json_print({
            "fidelity": round(result.fidelity, 4),
            "passed": result.passed,
            "total": result.total,
            "found": result.found,
            "category_scores": {
                cat: {"found": f, "total": t}
                for cat, (f, t) in result.category_scores.items()
            },
        })
    else:
        print(result.summary())


def _handle_patch(args) -> None:
    """Dispatch aleph patch propose|list|apply|reject."""
    from aleph.patch.manager import PatchManager

    sub = args.patch_command
    project_dir = os.path.abspath(args.project_dir)
    mgr = PatchManager(project_dir)

    if sub == "propose":
        if len(args.patch_args) < 2:
            print("Usage: aleph patch propose <symbol_id> \"<intent>\" [--file <file>]", file=sys.stderr)
            sys.exit(1)
        symbol_id = args.patch_args[0]
        intent = " ".join(args.patch_args[1:])
        try:
            record = mgr.propose(symbol_id, intent, file=args.file)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            _json_print(record.to_dict())
        else:
            print(f"Patch {record.patch_id} created for {record.symbol_id}.")
            print(f"  Intent: {record.intent}")
            print(f"  File: {record.file or '(unknown)'}")
            print(f"  Semantic hash: {record.semantic_hash or '(none)'}")

    elif sub == "list":
        patches = mgr.list_patches(status="pending")
        if args.json:
            _json_print([p.to_dict() for p in patches])
        elif not patches:
            print("No pending patches.")
        else:
            print(f"Pending patches ({len(patches)}):")
            for p in patches:
                print(f"  {p.patch_id} {p.symbol_id} [{p.status}] hash={p.semantic_hash or '?'} file={p.file or '?'}")
                print(f"    Intent: {p.intent}")

    elif sub == "apply":
        if len(args.patch_args) < 1:
            print("Usage: aleph patch apply <patch_id> [--force]", file=sys.stderr)
            sys.exit(1)
        patch_id = args.patch_args[0]
        result = mgr.apply(patch_id, force=args.force)
        if args.json:
            _json_print({
                "success": result.success,
                "patch_id": result.patch_id,
                "message": result.message,
                "file_path": result.file_path,
                "hash_changed": result.hash_changed,
            })
        else:
            print(result.message)

    elif sub == "reject":
        if len(args.patch_args) < 1:
            print("Usage: aleph patch reject <patch_id>", file=sys.stderr)
            sys.exit(1)
        patch_id = args.patch_args[0]
        message = mgr.reject(patch_id)
        if args.json:
            _json_print({"patch_id": patch_id, "message": message})
        else:
            print(message)

    else:
        print(f"Error: unknown patch command '{sub}'", file=sys.stderr)
        print("Available: propose, list, apply, reject", file=sys.stderr)
        sys.exit(1)


def _handle_setup(args) -> None:
    """Generate MCP server configs for IDE integration."""
    root = os.path.abspath(args.path)
    python_path = sys.executable

    config = json.dumps({
        "mcpServers": {
            "aleph": {
                "command": python_path,
                "args": ["-m", "aleph.cli", "serve", "."],
            }
        }
    }, indent=2) + "\n"

    editor_configs = {
        "cursor": (".cursor/mcp.json", "Cursor"),
        "claude-code": (".mcp.json", "Claude Code"),
        "vscode": (".vscode/mcp.json", "VS Code (Copilot)"),
        "windsurf": (".windsurf/mcp.json", "Windsurf"),
    }

    created = []
    for editor in args.editors:
        editor = editor.lower()
        if editor not in editor_configs:
            print(f"Unknown editor: {editor}. Available: {', '.join(editor_configs)}", file=sys.stderr)
            continue

        rel_path, display_name = editor_configs[editor]
        full_path = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if os.path.exists(full_path):
            print(f"  [skip] {rel_path} already exists")
            continue

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(config)
        created.append((rel_path, display_name))
        print(f"  [created] {rel_path} ({display_name})")

    # Also generate CLAUDE.md if it doesn't exist (instructs Claude to use Aleph first)
    claude_md_path = os.path.join(root, "CLAUDE.md")
    if not os.path.exists(claude_md_path):
        claude_md = _get_claude_md_template()
        with open(claude_md_path, "w", encoding="utf-8") as f:
            f.write(claude_md)
        print(f"  [created] CLAUDE.md (instructs Claude to use Aleph-first navigation)")

    if created:
        print(f"\nAleph MCP server configured for {len(created)} editor(s).")
        print(f"Python: {python_path}")
        print(f"\nNext: run `aleph build {args.path}` to generate .aleph/ artifacts.")
    elif not args.editors:
        print("No editors specified.")
    else:
        print("All configs already exist. Delete and re-run to regenerate.")


_CLAUDE_MD_TEMPLATE = """# Aleph — Instructions for Claude

## Use Aleph first for symbol-shaped navigation.

This project has Aleph MCP tools available. **Use Aleph first for symbol-shaped navigation** — resolve / callers / impact / structure. On a measured 26-task benchmark, Aleph answers these with a ~5.7x median token advantage over a grep+read baseline at equal-or-fewer tool calls (methodology: bench/BENCHMARK.md in the Aleph repo). Use Grep/Read for content discovery ("find the code that does X") — grep currently wins that task shape.

**DO THIS:**
- `aleph_brief "your task"` — start here. One call replaces 5.
- `aleph_search "keyword"` — find symbols by name; prefer it over Grep for symbol/identifier lookups.
- `aleph_resolve <id>` — look up a symbol definition without reading files.
- `aleph_callers <id>` — who calls a function; exact, no grepping.
- `aleph_context <id>` — a symbol's neighborhood in one call.
- `aleph_impact <id>` — check blast radius BEFORE modifying any function.
- `aleph_struct "file"` — file architecture without reading the whole file.

**AVOID:**
- Reading entire files when `aleph_resolve` or `aleph_expand` can give you what you need
- Guessing at callers when `aleph_callers` tells you exactly
- Skipping `aleph_impact` before modifying critical functions

## Planning

When entering plan mode or exploring the codebase:
1. **Start with `aleph_brief`** — relevant symbols, call context, impact risk, next steps
2. **Use `aleph_attention`** to know what matters most
3. Fall back to Read/Grep for content search or when Aleph doesn't have what you need

## Before Modifying Code

**ALWAYS** call `aleph_impact <id>` before changing any function.

## Session End

Call `aleph_session_summary` to save your review trail.
"""


def _get_claude_md_template() -> str:
    return _CLAUDE_MD_TEMPLATE.lstrip()


def _check_artifact_version(aleph_dir: str) -> None:
    """Warn if artifacts were built by a different Aleph version."""
    from aleph.__version__ import __version__
    map_path = os.path.join(aleph_dir, "project.aleph.map")
    if not os.path.isfile(map_path):
        return
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("[ALEPH_VERSION:"):
                    artifact_version = line.strip().split(":")[1].rstrip("]")
                    if artifact_version != __version__:
                        print(
                            f"[aleph] Warning: artifacts built with v{artifact_version}, "
                            f"running v{__version__}. Run `aleph build --full` to rebuild.",
                            file=sys.stderr,
                        )
                    return
        # No version tag found — very old artifacts
        print(
            f"[aleph] Warning: artifacts have no version stamp (pre-v0.5). "
            f"Run `aleph build --full` to rebuild with v{__version__}.",
            file=sys.stderr,
        )
    except OSError:
        pass


# Serve guard thresholds: refuse to serve a directory that is clearly a
# collection of projects rather than one project.
_SERVE_GUARD_MAX_FILES = 2000
_SERVE_GUARD_SCAN_DEPTH = 3

_GUARD_SKIP_DIRS = frozenset({
    "node_modules", "venv", ".venv", "__pycache__", "target", "build", "dist",
})


def _find_nested_git_repos(root: str, max_depth: int = _SERVE_GUARD_SCAN_DEPTH,
                           limit: int = 10) -> list[str]:
    """Find subdirectories (not root) that are git repositories."""
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    repos: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath != root and (".git" in dirnames or ".git" in filenames):
            repos.append(dirpath)
            dirnames[:] = []  # don't descend into a repo
            if len(repos) >= limit:
                break
            continue
        if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _GUARD_SKIP_DIRS
        ]
    return repos


def _count_files_up_to(root: str, limit: int) -> int:
    """Count files under root, stopping early once `limit` is exceeded."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d not in _GUARD_SKIP_DIRS
        ]
        count += len(filenames)
        if count > limit:
            return count
    return count


def _workspace_guard_reason(root: str) -> str | None:
    """Why `aleph serve <root>` should refuse, or None when it's fine.

    Triggers when root looks like a collection of projects:
      - it contains 2+ nested git repositories (and is not a repo itself), or
      - it holds >2000 files with no .git at its root.
    """
    if os.path.exists(os.path.join(root, ".git")):
        return None
    nested = _find_nested_git_repos(root)
    if len(nested) >= 2:
        names = ", ".join(os.path.relpath(r, root) for r in nested[:5])
        more = " ..." if len(nested) > 5 else ""
        return f"it contains {len(nested)} git repositories ({names}{more})"
    file_count = _count_files_up_to(root, _SERVE_GUARD_MAX_FILES)
    if file_count > _SERVE_GUARD_MAX_FILES:
        return (
            f"it holds more than {_SERVE_GUARD_MAX_FILES} files "
            f"with no .git at its root"
        )
    return None


def _stdio_client_attached() -> bool:
    """True when stdin is a pipe — i.e. an MCP client launched us over stdio.

    Interactive terminal runs (stdin is a TTY) return False, keeping the
    refuse-and-hint behavior. When stdin is unusable, assume a client is
    attached: exiting mid-handshake is the worse failure (issue #1).
    """
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def _detect_project_for_cwd(root: str) -> str | None:
    """If cwd is inside one of root's nested git repos, return that repo.

    Lets `aleph serve <multi-repo-parent>` unambiguously pick the project
    the client is actually working in.
    """
    try:
        cwd = os.path.realpath(os.getcwd())
    except OSError:
        return None
    for repo in _find_nested_git_repos(root):
        repo_real = os.path.realpath(repo)
        if cwd == repo_real or cwd.startswith(repo_real + os.sep):
            return repo
    return None


def _workspace_guard_tool_message(root: str, reason: str) -> str:
    """Tool-call response for the degraded (multi-repo parent) MCP server."""
    return (
        f"Aleph is running in degraded mode: cannot serve {root} as a "
        f"single project because {reason}.\n"
        f"This directory is a collection of projects, so Aleph has nothing "
        f"to index here. To get working Aleph tools, do one of:\n"
        f"  1. Point the MCP server at one project: `aleph serve "
        f"/path/to/project` (or launch your client from inside that "
        f"project and run `aleph build` there).\n"
        f"  2. Create {os.path.join(root, '.aleph-workspace.json')} with "
        f'{{"projects": {{"name-a": "repo-a", "name-b": "repo-b"}}}}, run '
        f"`aleph workspace build {root}`, then restart the MCP server "
        f"(workspace tools will be available).\n"
        f"  3. Re-run `aleph serve {root} --force` to index everything as "
        f"one project anyway."
    )


def _print_workspace_guard_message(root: str, reason: str) -> None:
    print(
        f"[aleph] Refusing to serve {root}: {reason}.\n"
        f"[aleph] This looks like a workspace of multiple projects, not a "
        f"single project.\n"
        f"[aleph] To serve it as a workspace:\n"
        f"[aleph]   1. Create {os.path.join(root, '.aleph-workspace.json')} "
        f"with:\n"
        f'[aleph]      {{"projects": {{"name-a": "repo-a", "name-b": "repo-b"}}}}\n'
        f"[aleph]   2. Run `aleph workspace build {root}` to build every project.\n"
        f"[aleph]   3. Re-run `aleph serve {root}` (workspace tools will be "
        f"available).\n"
        f"[aleph] Or pass --force to serve this directory as a single project "
        f"anyway.",
        file=sys.stderr,
    )


def _handle_serve(args) -> None:
    # PRE-HANDSHAKE PURITY: everything in this function runs before the MCP
    # handshake, so it must be cheap and infallible — header reads, license
    # file reads, config parsing, and the bounded workspace guard scan only.
    # Slow/fallible work (auto-migrate heal, auto-build, rebuild watcher)
    # belongs in aleph.mcp.server._deferred_startup. Enforced by
    # tests/unit/test_handshake_purity.py.
    from aleph.__version__ import __version__
    root = os.path.abspath(args.path)
    aleph_dir = os.path.join(root, ".aleph")

    print(f"[aleph] v{__version__}", file=sys.stderr)

    # Workspace guard: don't treat a multi-repo / huge unscoped directory
    # as one project (unless --force). Interactive runs refuse with a hint;
    # MCP stdio runs must NEVER exit mid-handshake (issue #1) — they fall
    # back to an auto-detected project or a degraded-but-alive server.
    skip_root_build = False
    degraded_message: str | None = None
    if not getattr(args, "force", False):
        guard_reason = _workspace_guard_reason(root)
        if guard_reason is not None:
            from aleph.query.workspace import find_workspace_file
            if find_workspace_file(root) is not None:
                # Workspace configured: serve it, but don't auto-build the
                # whole directory as a single project.
                skip_root_build = True
                print(
                    "[aleph] Workspace detected — serving workspace tools; "
                    "skipping single-project auto-build of the root.",
                    file=sys.stderr,
                )
            elif not _stdio_client_attached():
                # Interactive terminal: refuse with setup instructions.
                _print_workspace_guard_message(root, guard_reason)
                sys.exit(1)
            elif (detected := _detect_project_for_cwd(root)) is not None:
                # MCP stdio + cwd inside one nested repo: serve that project.
                print(
                    f"[aleph] {root}: {guard_reason}; cwd is inside "
                    f"{detected} — serving that project instead.",
                    file=sys.stderr,
                )
                root = detected
                aleph_dir = os.path.join(root, ".aleph")
            else:
                # MCP stdio: complete the handshake with a degraded server
                # whose tool calls return setup instructions.
                degraded_message = _workspace_guard_tool_message(root, guard_reason)
                print(
                    f"[aleph] {root}: {guard_reason}.\n"
                    f"[aleph] Serving in degraded mode so the MCP handshake "
                    f"succeeds — tool calls return workspace setup "
                    f"instructions.",
                    file=sys.stderr,
                )

    if degraded_message is not None:
        from aleph.mcp.server import serve
        serve(root, degraded_message=degraded_message)
        return

    # Missing-artifact auto-build happens AFTER the handshake, on the
    # serve()-side deferred startup thread (a synchronous build here once
    # stalled an MCP client for 90 minutes with zero output).
    _check_artifact_version(aleph_dir)
    from aleph.mcp.server import serve
    serve(root, skip_root_build=skip_root_build)


def _workspace_projects_for(args) -> dict[str, str]:
    """Load workspace projects from a path (dir with config, or config file)."""
    from aleph.query.workspace import (
        WORKSPACE_FILENAME, find_workspace_file, load_workspace_projects,
    )
    target = os.path.abspath(args.path)
    if os.path.isfile(target):
        ws_path = target
    else:
        ws_path = find_workspace_file(target)
    if ws_path is None:
        print(
            f"Error: no {WORKSPACE_FILENAME} found in {target}.\n"
            f'Create one with {{"projects": {{"name": "relative/or/abs/path"}}}}.',
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return load_workspace_projects(ws_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _handle_workspace(args) -> None:
    """Dispatch aleph workspace build|status.

    Free like everything else — the team-tier license gate was removed
    when Aleph went Apache-2.0 (2026-07-05): all features, no license.
    """
    sub = args.workspace_command
    if sub == "build":
        _handle_workspace_build(args)
    elif sub == "status":
        _handle_workspace_status(args)
    else:
        print(f"Error: unknown workspace command '{sub}'", file=sys.stderr)
        print("Available: build, status", file=sys.stderr)
        sys.exit(1)


def _handle_workspace_build(args) -> None:
    from aleph.query.workspace import workspace_build

    projects = _workspace_projects_for(args)
    reports = workspace_build(projects, full=getattr(args, "full", False))

    if args.json:
        _json_print({
            "projects": reports,
            "succeeded": sum(1 for r in reports if r["success"]),
            "failed": sum(1 for r in reports if not r["success"]),
        })
    else:
        print(f"Workspace build: {len(reports)} projects")
        for r in reports:
            if r["success"]:
                line = (
                    f"  [ok]   {r['name']}: {r['files']} files, "
                    f"{r['symbols']} symbols, {r['reduction_percent']:.1f}% reduction"
                )
                if r["error"]:
                    line += f" ({r['error']})"
                print(line)
            else:
                print(f"  [FAIL] {r['name']}: {r['error']}")
        failed = [r for r in reports if not r["success"]]
        print(
            f"Summary: {len(reports) - len(failed)} succeeded, "
            f"{len(failed)} failed."
        )
    if any(not r["success"] for r in reports):
        sys.exit(1)


def _handle_workspace_status(args) -> None:
    from aleph.query.workspace import workspace_status

    projects = _workspace_projects_for(args)
    statuses = workspace_status(projects)

    if args.json:
        _json_print({"projects": [s.to_dict() for s in statuses]})
        return

    print(f"Workspace status: {len(statuses)} projects")
    for s in statuses:
        if s.error:
            state = f"ERROR ({s.error})"
        elif not s.built:
            state = "NOT BUILT"
        elif s.stale:
            state = f"STALE ({s.stale_files} source files newer than artifacts)"
        else:
            state = "fresh"
        print(f"  [{s.name}] {state}")
        details = f"    path={s.path} files={s.source_files}"
        if s.last_build:
            details += f" last_build={s.last_build}"
        print(details)
    if any(s.stale or not s.built for s in statuses if not s.error):
        print("Run `aleph workspace build` to refresh stale projects.")


def _handle_diff(args) -> None:
    if not os.path.isfile(args.file):
        print(f"Error: {args.file} not found", file=sys.stderr)
        sys.exit(1)
    result = run_pipeline(args.file)
    idx_path, index = _load_required_index(args.index_file, os.path.dirname(args.file) or ".")
    prior = index.get("files", {}).get(os.path.abspath(args.file)) or index.get("files", {}).get(args.file)

    # Use SemanticDiff if we have prior data
    diff_engine = SemanticDiff()
    report = diff_engine.diff(prior, result)

    if args.json:
        _json_print(report.to_dict())
        return

    print(f"Changed:    {report.semantic_hash_changed}")
    print(f"Sem hash:   {report.previous_hash} -> {report.current_hash}")
    if report.symbols_added:
        print(f"Added:      {', '.join(report.symbols_added)}")
    if report.symbols_removed:
        print(f"Removed:    {', '.join(report.symbols_removed)}")
    if report.signatures_changed:
        print(f"Sig changed: {', '.join(report.signatures_changed)}")
    if report.bodies_changed:
        print(f"Body changed: {', '.join(report.bodies_changed)}")
    if report.calls_added:
        print(f"Calls added: {len(report.calls_added)}")
    if report.calls_removed:
        print(f"Calls removed: {len(report.calls_removed)}")
    print(f"Intents:    {'changed' if report.intents_changed else 'unchanged'}")
    print(f"Errors:     {'changed' if report.errors_changed else 'unchanged'}")
    print(f"Coverage:   {'changed' if report.coverage_changed else 'unchanged'}")


# ── selftest: the RESPONSIVENESS CONTRACT (release gate) ──
#
# Two enforced budgets (see docs/RESPONSIVENESS_CONTRACT.md — `aleph
# selftest` exiting 0 is the release gate):
#
#   * HANDSHAKE BUDGET — `aleph serve` must answer MCP `initialize` within
#     --handshake-budget seconds (default 10) on each of the three boot
#     paths that have each shipped a hang: (a) a built project, (b) an
#     unbuilt directory, (c) a multi-repo parent (degraded mode).
#   * PER-TOOL BUDGET — every registered MCP tool answers within --budget
#     seconds (default 10) against a tiny pre-built fixture project.
#
# ALEPH_SELFTEST_BUDGET_MULT scales both budgets (slow CI runners). Any
# TIMEOUT or FAIL exits nonzero. A hung tool is abandoned — its server is
# killed, a fresh one is spawned — and the run continues, so one hang
# never hides the status of the remaining tools.

_SELFTEST_SAMPLE_SOURCE = (
    '"""Sample module for the aleph selftest."""\n'
    "\n"
    "\n"
    "def greet(name):\n"
    '    """Return a greeting for name."""\n'
    '    return "hello " + name\n'
    "\n"
    "\n"
    "def add(a, b):\n"
    '    """Add two numbers."""\n'
    "    return a + b\n"
    "\n"
    "\n"
    "class Sample:\n"
    '    """A small sample class."""\n'
    "\n"
    "    def run(self):\n"
    "        return greet(self.name)\n"
    "\n"
    "    def total(self, values):\n"
    "        result = 0\n"
    "        for v in values:\n"
    "            result = add(result, v)\n"
    "        return result\n"
)


def _selftest_budget_multiplier() -> float:
    """Budget scale factor from ALEPH_SELFTEST_BUDGET_MULT (>=, default 1).

    CI runners are slower than dev laptops; the multiplier loosens every
    selftest budget uniformly without changing the contract itself.
    """
    try:
        mult = float(os.environ.get("ALEPH_SELFTEST_BUDGET_MULT", "1"))
    except ValueError:
        return 1.0
    return mult if mult > 0 else 1.0


def _selftest_tool_calls(sid: str) -> list[tuple[str, dict]]:
    """One call per registered MCP tool, in a sensible exercise order.

    ``sid`` is a symbol id harvested from the fixture (placeholder when
    unavailable — handlers answer unknown ids with error *strings*, which
    still proves responsiveness). Completeness against the live tool
    registry is enforced by tests/unit/test_selftest_contract.py: adding
    an MCP tool without adding it here fails the unit suite.
    """
    return [
        ("aleph_map", {"limit": 50}),
        ("aleph_fs", {}),
        ("aleph_struct", {"file": "sample.py"}),
        ("aleph_bodies", {"file": "sample.py"}),
        ("aleph_errors", {"file": "sample.py"}),
        ("aleph_intents", {"file": "sample.py"}),
        ("aleph_tests", {"file": "sample.py"}),
        ("aleph_coverage", {}),
        ("aleph_search", {"term": "greet"}),
        ("aleph_expand", {"symbol_id": sid}),
        ("aleph_resolve", {"symbol_id": sid}),
        ("aleph_callers", {"symbol_id": sid}),
        ("aleph_context", {"symbol_id": sid}),
        ("aleph_attention", {}),
        ("aleph_salience", {}),
        ("aleph_temporal", {}),
        ("aleph_epistemic", {}),
        ("aleph_infer", {"symbol_id": sid, "conclusion": "selftest probe",
                         "confidence": 0.5}),
        ("aleph_flag", {"symbol_id": sid, "reason": "selftest probe"}),
        ("aleph_verify", {"symbol_id": sid}),
        ("aleph_memory_resume", {}),
        ("aleph_brief", {"task": "how does sample work"}),
        ("aleph_impact", {"symbol_id": sid}),
        ("aleph_patch_propose", {"symbol_id": sid, "intent": "selftest no-op"}),
        ("aleph_patch", {"symbol_id": sid, "patch_body": "selftest no-op"}),
        ("aleph_patch_list", {}),
        # Nonexistent patch id on purpose: apply/reject must answer fast
        # with a not-found message, never mutate the fixture source.
        ("aleph_patch_apply", {"patch_id": "selftest-missing"}),
        ("aleph_patch_reject", {"patch_id": "selftest-missing"}),
        ("aleph_workspace_search", {"term": "greet"}),
        ("aleph_workspace_status", {}),
        ("aleph_workspace_brief", {"task": "selftest"}),
        ("aleph_session_summary", {}),
        ("aleph_rebuild", {}),
    ]


class _McpSelftestSession:
    """One `aleph serve` child driven over real stdio pipes.

    Hardened by construction: every read is deadline-bounded (`_await`),
    writes trap BrokenPipeError, and close() always terminate()s then
    kill()s the child — a hung or wedged server can never hang the
    selftest itself. (Popen allowlisted in
    tests/unit/test_subprocess_hygiene.py for exactly these reasons.)
    """

    def __init__(self, project: str) -> None:
        import subprocess
        import threading
        from collections import deque

        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        # Bounded tail of server stderr — drained continuously (so the
        # server never blocks on a full pipe), printed on failure.
        self.stderr_tail: deque[str] = deque(maxlen=50)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "aleph.cli", "serve", project],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ},
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    # ── pipe plumbing ──

    def _read_stdout(self) -> None:
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and "id" in obj:
                with self._lock:
                    self._responses[obj["id"]] = obj

    def _drain_stderr(self) -> None:
        for line in self._proc.stderr:
            self.stderr_tail.append(line.rstrip("\n"))

    def _send(self, obj: dict) -> bool:
        try:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def _await(self, msg_id: int, timeout: float) -> dict | None:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if msg_id in self._responses:
                    return self._responses[msg_id]
            if self._proc.poll() is not None:
                # Server died; one last check then give up.
                with self._lock:
                    return self._responses.get(msg_id)
            time.sleep(0.02)
        return None

    def alive(self) -> bool:
        return self._proc.poll() is None

    # ── protocol ──

    def initialize(self, timeout: float) -> tuple[float, str]:
        """Run the MCP handshake. Returns (elapsed_seconds, status).

        Status: OK (answered in time), TIMEOUT (alive but silent past the
        budget), FAIL (pipe death / process exit / JSON-RPC error).
        """
        import time
        msg_id = self._next_id
        self._next_id += 1
        start = time.monotonic()
        sent = self._send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "selftest", "version": "1"},
            },
        })
        if not sent:
            return time.monotonic() - start, "FAIL"
        resp = self._await(msg_id, timeout)
        elapsed = time.monotonic() - start
        if resp is None:
            return elapsed, ("TIMEOUT" if self.alive() else "FAIL")
        if "error" in resp:
            return elapsed, "FAIL"
        self._send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        return elapsed, "OK"

    def call(self, name: str, arguments: dict, timeout: float) -> tuple[float, dict | None]:
        """Call one tool. Returns (elapsed_seconds, response-or-None)."""
        import time
        msg_id = self._next_id
        self._next_id += 1
        start = time.monotonic()
        sent = self._send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if not sent:
            return time.monotonic() - start, {"error": {"message": "pipe closed"}}
        resp = self._await(msg_id, timeout)
        return time.monotonic() - start, resp

    def print_stderr_tail(self) -> None:
        if self.stderr_tail:
            print(
                f"\n[selftest] server stderr (last {len(self.stderr_tail)} lines):",
                file=sys.stderr,
            )
            for line in self.stderr_tail:
                print(f"  {line}", file=sys.stderr)

    def close(self) -> None:
        import subprocess
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        except OSError:
            pass


def _selftest_response_texts(resp: dict) -> list[str]:
    result = resp.get("result")
    result = result if isinstance(result, dict) else {}
    return [
        item.get("text", "")
        for item in result.get("content", [])
        if isinstance(item, dict)
    ]


def _selftest_tool_status(resp: dict | None) -> str:
    """Classify one tool response (or its absence within the budget)."""
    if resp is None:
        return "TIMEOUT"
    if "error" in resp:
        return "FAIL"  # JSON-RPC error
    result = resp.get("result")
    result = result if isinstance(result, dict) else {}
    if result.get("isError"):
        return "FAIL"  # tool-level error result
    if any("degraded mode" in t for t in _selftest_response_texts(resp)):
        # Degraded-mode responses come back as *successful* results
        # carrying setup instructions (see mcp/server.py capped_tool) —
        # not OK, not FAIL.
        return "DEGRADED"
    return "OK"


def _selftest_harvest_symbol_id(session: _McpSelftestSession, budget: float) -> str:
    """Pull a real symbol id out of aleph_search for the symbol-arg tools."""
    _, resp = session.call("aleph_search", {"term": "greet"}, budget)
    if resp is not None:
        for text in _selftest_response_texts(resp):
            for line in text.splitlines()[1:]:
                stripped = line.strip()
                if stripped and not stripped.startswith("["):
                    return stripped.split()[0]
    return "S1"  # placeholder; handlers answer unknown ids with messages


def _print_budget_table(
    label: str, rows: list[tuple[str, float, float, str]]
) -> None:
    name_w = max([len(n) for n, _, _, _ in rows] + [len(label)])
    print(f"\n{label.ljust(name_w)}  {'seconds':>8}  {'budget':>7}  status")
    print(f"{'-' * name_w}  {'-' * 8}  {'-' * 7}  ------")
    for name, elapsed, budget, status in rows:
        print(f"{name.ljust(name_w)}  {elapsed:8.3f}  {budget:7.1f}  {status}")


def _handle_selftest(args) -> None:
    """RESPONSIVENESS CONTRACT check — the release gate.

    Phase 1 (handshake budget): spin up `aleph serve` over real stdio on
    the three boot paths that have each shipped a hang — a built project,
    an unbuilt directory, and a multi-repo parent — and require an
    `initialize` answer within the handshake budget on every one.

    Phase 2 (per-tool budget): drive EVERY registered MCP tool against a
    tiny pre-built fixture with a per-tool time budget. A hung tool is
    abandoned (its server killed, a fresh one spawned) and the run
    continues. Any TIMEOUT/FAIL exits 1; an all-DEGRADED-but-alive server
    exits 2; clean run exits 0.
    """
    import shutil
    import subprocess
    import tempfile

    mult = _selftest_budget_multiplier()
    budget = args.budget * mult
    handshake_budget = args.handshake_budget * mult
    if mult != 1.0:
        print(
            f"[selftest] ALEPH_SELFTEST_BUDGET_MULT={mult:g}: per-tool "
            f"budget {budget:.1f}s, handshake budget {handshake_budget:.1f}s",
            file=sys.stderr,
        )

    temp_dirs: list[str] = []

    def _mktemp(suffix: str) -> str:
        d = tempfile.mkdtemp(prefix=f"aleph-selftest-{suffix}-")
        temp_dirs.append(d)
        return d

    handshake_rows: list[tuple[str, float, float, str]] = []
    results: list[tuple[str, float, float, str]] = []
    last_session: _McpSelftestSession | None = None

    try:
        # ── Fixtures ──
        built = _mktemp("built")
        with open(os.path.join(built, "sample.py"), "w", encoding="utf-8") as f:
            f.write(_SELFTEST_SAMPLE_SOURCE)
        print(f"[selftest] Building fixture project: {built}", file=sys.stderr)
        build = subprocess.run(
            [sys.executable, "-m", "aleph.cli", "build", built, "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(120.0, budget * 6),
        )
        if build.returncode != 0 or not os.path.isdir(os.path.join(built, ".aleph")):
            print("Error: fixture project build failed.", file=sys.stderr)
            if build.stderr:
                print(build.stderr, file=sys.stderr)
            sys.exit(1)

        unbuilt = _mktemp("unbuilt")
        with open(os.path.join(unbuilt, "sample.py"), "w", encoding="utf-8") as f:
            f.write(_SELFTEST_SAMPLE_SOURCE)

        multi = _mktemp("multirepo")
        for repo in ("repo-a", "repo-b"):
            os.makedirs(os.path.join(multi, repo, ".git"))
            with open(os.path.join(multi, repo, "main.py"), "w", encoding="utf-8") as f:
                f.write("def f():\n    return 1\n")

        # ── Phase 1: handshake budget on the three boot paths ──
        for label, path in (
            ("built-project", built),
            ("unbuilt-dir", unbuilt),
            ("multi-repo-parent", multi),
        ):
            session = _McpSelftestSession(path)
            last_session = session
            try:
                elapsed, status = session.initialize(handshake_budget)
            finally:
                session.close()
            handshake_rows.append((label, elapsed, handshake_budget, status))
            if status != "OK":
                session.print_stderr_tail()

        # ── Phase 2: per-tool budget against the built fixture ──
        if args.project:
            project = os.path.abspath(args.project)
            if not os.path.isdir(os.path.join(project, ".aleph")):
                if _workspace_guard_reason(project) is not None:
                    # Multi-project parent: the server completes the MCP
                    # handshake in degraded mode — drive it anyway so the
                    # selftest reports DEGRADED instead of crashing.
                    print(
                        f"[selftest] {project} has no .aleph/ and looks like a "
                        f"multi-project parent — server will run in degraded mode.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Error: {project} has no .aleph/ — run `aleph build "
                        f"{project}` first.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            print(f"[selftest] Using existing project: {project}", file=sys.stderr)
        else:
            project = built

        def _spawn() -> _McpSelftestSession | None:
            session = _McpSelftestSession(project)
            elapsed, status = session.initialize(handshake_budget)
            if status != "OK":
                print(
                    f"Error: MCP initialize handshake {status} after "
                    f"{elapsed:.1f}s on {project}.",
                    file=sys.stderr,
                )
                session.print_stderr_tail()
                session.close()
                return None
            return session

        session = _spawn()
        last_session = session
        if session is None:
            _print_budget_table("boot path", handshake_rows)
            sys.exit(1)

        sid = _selftest_harvest_symbol_id(session, budget)
        for name, arguments in _selftest_tool_calls(sid):
            if session is None:
                # Could not respawn after a hang: every remaining tool is
                # unmeasurable — report and bail out of the loop.
                results.append((name, 0.0, budget, "SKIPPED"))
                continue
            elapsed, resp = session.call(name, arguments, budget)
            status = _selftest_tool_status(resp)
            results.append((name, elapsed, budget, status))
            if status == "TIMEOUT":
                # Abandon the hung tool: kill this server, spawn a fresh
                # one, and keep measuring the remaining tools.
                print(
                    f"[selftest] {name} exceeded its {budget:.1f}s budget — "
                    f"killing server and continuing.",
                    file=sys.stderr,
                )
                session.print_stderr_tail()
                session.close()
                session = _spawn()
                last_session = session or last_session
        if session is not None:
            last_session = session
            session.close()
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    # ── Report ──
    _print_budget_table("boot path", handshake_rows)
    _print_budget_table("tool", results)

    handshake_bad = sum(1 for _, _, _, s in handshake_rows if s != "OK")
    ok = sum(1 for _, _, _, s in results if s == "OK")
    fail = sum(1 for _, _, _, s in results if s in ("FAIL", "SKIPPED"))
    timeout = sum(1 for _, _, _, s in results if s == "TIMEOUT")
    degraded = sum(1 for _, _, _, s in results if s == "DEGRADED")
    print(
        f"\nSummary: handshake {len(handshake_rows) - handshake_bad}/"
        f"{len(handshake_rows)} OK; tools {ok} OK, {fail} FAIL, "
        f"{timeout} TIMEOUT, {degraded} DEGRADED "
        f"(per-tool budget {budget:.1f}s) across {len(results)} tools."
    )
    if (fail or timeout or handshake_bad) and last_session is not None:
        last_session.print_stderr_tail()
    if fail or timeout or handshake_bad or not results:
        sys.exit(1)
    if degraded:
        # Server alive but unable to serve real tools (e.g. multi-repo
        # parent): distinct exit code from hard failure.
        sys.exit(2)
    sys.exit(0)


def main() -> None:
    from aleph.__version__ import __version__
    parser = argparse.ArgumentParser(prog="aleph", description="Aleph semantic compiler")
    parser.add_argument("--version", "-V", action="version", version=f"aleph {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # compress command
    compress_parser = subparsers.add_parser("compress", help="Compress a source file")
    compress_parser.add_argument("file", help="Source file to compress")
    compress_parser.add_argument("-o", "--output", help="Output directory", default=None)
    compress_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    compress_parser.add_argument(
        "--include-original-bodies",
        action="store_true",
        help="Persist original bodies for reversible expansion",
    )
    compress_parser.add_argument(
        "--bundle-json",
        action="store_true",
        help="Write .aleph.json combined artifact when using --output",
    )

    index_parser = subparsers.add_parser(
        "index", help="[deprecated] Alias for `aleph build`"
    )
    index_parser.add_argument("path", nargs="?", default=".", help="Project root")
    index_parser.add_argument("--index-file", default=None, help="[deprecated] ignored")
    index_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    query_parser = subparsers.add_parser(
        "query",
        help="Query project artifacts (EXPAND/RESOLVE/CALLERS/CONTEXT/SEARCH)",
    )
    query_parser.add_argument(
        "query_command",
        help="Query command: EXPAND, RESOLVE, CALLERS, CONTEXT, or SEARCH",
    )
    query_parser.add_argument(
        "query_args",
        help="Symbol ID or search term",
    )
    query_parser.add_argument(
        "-d", "--project-dir", dest="project_dir", default=".",
        help="Project directory containing .aleph artifacts (default: .)",
    )
    query_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    resolve_parser = subparsers.add_parser("resolve", help="Resolve symbol identifier/name")
    resolve_parser.add_argument("symbol", help="Symbol id/name/qualified name")
    resolve_parser.add_argument(
        "-d", "--project-dir", dest="project_dir", default=".",
        help="Project directory containing .aleph artifacts (default: .)",
    )
    resolve_parser.add_argument("--index-file", default=None, help="[deprecated] ignored")
    resolve_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    neighbors_parser = subparsers.add_parser("neighbors", help="Show graph neighbors for a symbol")
    neighbors_parser.add_argument("symbol", help="Symbol id/name/qualified name")
    neighbors_parser.add_argument(
        "-d", "--project-dir", dest="project_dir", default=".",
        help="Project directory containing .aleph artifacts (default: .)",
    )
    neighbors_parser.add_argument("--index-file", default=None, help="[deprecated] ignored")
    neighbors_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    expand_parser = subparsers.add_parser("expand", help="Expand a .aleph component artifact")
    expand_parser.add_argument("bodies_file", help="Path to .aleph.bodies (or .intents/.errors/.tests/.temporal) file")
    expand_parser.add_argument("--symbol-id", default=None, help="Expand only one symbol id")
    expand_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    build_parser = subparsers.add_parser("build", help="Build project-level Aleph artifacts")
    build_parser.add_argument("path", nargs="?", default=".", help="Project root directory")
    build_parser.add_argument("-o", "--output", help="Output directory (default: project root)", default=None)
    build_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    build_parser.add_argument("--full", action="store_true", help="Force full rebuild (ignore cache)")
    build_parser.add_argument("--per-file", action="store_true", help="Also emit per-file .aleph artifacts")
    build_parser.add_argument(
        "-q", "--quiet", action="store_true",
        help=(
            "Suppress stderr progress output (default: on when stderr is a "
            "TTY or ALEPH_PROGRESS=1)"
        ),
    )
    build_parser.add_argument(
        "--no-text-artifacts", action="store_true",
        help="Write only the SQLite store (regenerate text artifacts with `aleph export`)",
    )
    build_parser.add_argument(
        "--semantic", action="store_true",
        help=(
            "Also build the semantic embedding index (requires the optional "
            "fastembed extra: pip install 'aleph-compiler[semantic]'). "
            "Sticky: later incremental builds keep embedding changed files "
            "without re-passing the flag."
        ),
    )
    build_parser.add_argument(
        "--include-vendor", action="store_true",
        help=(
            "Index vendored third-party code (vendor/, third_party/, "
            "thirdparty/ — skipped by default). Equivalent to "
            "ALEPH_INCLUDE_VENDOR=1; a '!vendor' line in .alephignore "
            "does the same per-project."
        ),
    )

    export_parser = subparsers.add_parser(
        "export", help="Regenerate text artifacts from the SQLite store (aleph.db)"
    )
    export_parser.add_argument("path", nargs="?", default=".", help="Project root directory")
    export_parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory (default: <root>/.aleph)",
    )
    export_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    diff_parser = subparsers.add_parser("diff", help="Semantic diff: compare current file against indexed version")
    diff_parser.add_argument("file", help="Source file to compare")
    diff_parser.add_argument("--index-file", default=None, help="Existing index file")
    diff_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    serve_parser = subparsers.add_parser("serve", help="Start MCP server for a built Aleph project")
    serve_parser.add_argument("path", nargs="?", default=".", help="Project root directory (must contain .aleph/)")
    serve_parser.add_argument(
        "--force", action="store_true",
        help="Serve even if the directory looks like a multi-project workspace",
    )

    # selftest command
    selftest_parser = subparsers.add_parser(
        "selftest",
        help=(
            "Responsiveness contract check (release gate): handshake budget "
            "on the three boot paths + per-tool budget for every MCP tool"
        ),
    )
    selftest_parser.add_argument(
        "--project", default=None,
        help="Selftest an already-built project (default: build a tiny temp project)",
    )
    selftest_parser.add_argument(
        "--budget", type=float, default=10.0,
        help=(
            "Per-tool budget in seconds; no answer within it = TIMEOUT, "
            "nonzero exit (default: 10.0; scaled by ALEPH_SELFTEST_BUDGET_MULT)"
        ),
    )
    selftest_parser.add_argument(
        "--handshake-budget", type=float, default=10.0,
        help=(
            "Seconds `aleph serve` gets to answer MCP initialize on each "
            "boot path (default: 10.0; scaled by ALEPH_SELFTEST_BUDGET_MULT)"
        ),
    )

    # workspace command
    workspace_parser = subparsers.add_parser(
        "workspace", help="Multi-project workspace operations (.aleph-workspace.json)"
    )
    workspace_parser.add_argument(
        "workspace_command",
        help="Workspace command: build or status",
    )
    workspace_parser.add_argument(
        "path", nargs="?", default=".",
        help="Workspace root directory or path to .aleph-workspace.json",
    )
    workspace_parser.add_argument(
        "--full", action="store_true",
        help="Force full rebuilds (ignore caches)",
    )
    workspace_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    # memory command
    memory_parser = subparsers.add_parser("memory", help="Conversation memory compression")
    memory_parser.add_argument(
        "memory_command",
        help="Memory command: compress or resume",
    )
    memory_parser.add_argument(
        "transcript_file",
        nargs="?",
        default=None,
        help="JSON transcript file (for compress)",
    )
    memory_parser.add_argument(
        "-d", "--project-dir", dest="project_dir", default=None,
        help="Project directory for saving/loading memory",
    )
    memory_parser.add_argument(
        "--session-id", default=None,
        help="Session identifier for the memory entry",
    )
    memory_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    # bench command
    bench_parser = subparsers.add_parser("bench", help="Run benchmarks")
    bench_parser.add_argument(
        "bench_command",
        help="Benchmark command: resume",
    )
    bench_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    bench_parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")

    # patch command
    patch_parser = subparsers.add_parser("patch", help="Semantic patching workflow")
    patch_parser.add_argument(
        "patch_command",
        help="Patch command: propose, list, apply, reject",
    )
    patch_parser.add_argument(
        "patch_args",
        nargs="*",
        default=[],
        help="Arguments for the patch command",
    )
    patch_parser.add_argument(
        "-d", "--project-dir", dest="project_dir", default=".",
        help="Project directory containing .aleph artifacts (default: .)",
    )
    patch_parser.add_argument(
        "--file", default=None,
        help="Source file override (for propose)",
    )
    patch_parser.add_argument(
        "--force", action="store_true",
        help="Force apply even if semantic hash changed",
    )
    patch_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    # watch command
    watch_parser = subparsers.add_parser("watch", help="Watch for changes and rebuild incrementally")
    watch_parser.add_argument("path", nargs="?", default=".", help="Project root directory")
    watch_parser.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds (default: 2)")

    # migrate-ids command
    migrate_parser = subparsers.add_parser(
        "migrate-ids",
        help="Migrate symbol IDs to the portable root-relative scheme "
             "(rewrites epistemic inferences/flags/pending patches)",
    )
    migrate_parser.add_argument("project", help="Project root directory")
    migrate_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the old->new mapping count and samples without writing",
    )
    migrate_parser.add_argument(
        "--old-root", default=None,
        help="Old project root the artifacts were built from "
             "(default: the artifact [ROOT:...] line)",
    )

    # setup command
    setup_parser = subparsers.add_parser("setup", help="Generate MCP configs for IDE integration")
    setup_parser.add_argument("path", nargs="?", default=".", help="Project root directory")
    setup_parser.add_argument(
        "--editors", nargs="*",
        default=["cursor", "claude-code", "vscode", "windsurf"],
        help="Editors to configure (default: all)",
    )

    mcp_parser = subparsers.add_parser("mcp", help="MCP server introspection")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_sub.add_parser("tiers", help="Show tool tier manifest (core/frequent/occasional/rare)")

    args = parser.parse_args()
    if args.command == "compress":
        _handle_compress(args)
    elif args.command == "index":
        _handle_index(args)
    elif args.command == "query":
        _handle_query(args)
    elif args.command == "resolve":
        _handle_resolve(args)
    elif args.command == "neighbors":
        _handle_neighbors(args)
    elif args.command == "expand":
        _handle_expand(args)
    elif args.command == "build":
        _handle_build(args)
    elif args.command == "export":
        _handle_export(args)
    elif args.command == "diff":
        _handle_diff(args)
    elif args.command == "serve":
        _handle_serve(args)
    elif args.command == "selftest":
        _handle_selftest(args)
    elif args.command == "workspace":
        _handle_workspace(args)
    elif args.command == "memory":
        _handle_memory(args)
    elif args.command == "patch":
        _handle_patch(args)
    elif args.command == "bench":
        _handle_bench(args)
    elif args.command == "setup":
        _handle_setup(args)
    elif args.command == "watch":
        _handle_watch(args)
    elif args.command == "migrate-ids":
        _handle_migrate_ids(args)
    elif args.command == "mcp":
        if args.mcp_command == "tiers":
            from aleph.mcp.server import TOOL_TIERS
            print("Aleph MCP tool tiers:")
            print()
            for tier in ("core", "frequent", "occasional", "rare"):
                tools = TOOL_TIERS.get(tier, ())
                print(f"  {tier.upper()} ({len(tools)}):")
                for t in tools:
                    print(f"    {t}")
                print()
        else:
            mcp_parser.print_help()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
