"""Aleph CLI for single-file and project-level workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys

from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry
from aleph.symbols.fingerprint import SemanticFingerprint
from aleph.structure.signatures import SignatureExtractor
from aleph.structure.hierarchy import HierarchyBuilder
from aleph.structure.callgraph import CallGraphBuilder
from aleph.compress.body_compressor import BodyCompressor
from aleph.emit.serializer import AlephSerializer
from aleph.emit.file_components import FileComponentWriter
from aleph.emit.loader import AlephLoader
from aleph.link.salience import SalienceScorer
from aleph.model.components import StructComponent, BodiesComponent
from aleph.model.graph import SemanticGraph
from aleph.util.tokens import compare_tokens
from aleph.project.indexer import build_index, load_index, query_symbols, save_index
from aleph.project.builder import build_project
from aleph.project.cache import (
    BuildCache, load_build_cache, save_build_cache, CACHE_FILENAME,
)
from aleph.query.engine import QueryEngine
from aleph.temporal.git_history import GitHistory
from aleph.temporal.analyzer import TemporalAnalyzer
from aleph.inference.intent_inference import IntentInferrer
from aleph.inference.error_flow import ErrorFlowAnalyzer
from aleph.inference.test_coverage import TestCoverageMapper
from aleph.diff.semantic_diff import SemanticDiff


def run_pipeline(source_path: str, output_dir: str | None = None) -> dict:
    """Run the full single-file Aleph pipeline.

    Returns a dict with pipeline results including token comparison.
    """
    # Step 3: Parse
    parser = TreeSitterParser()
    tree, source, language = parser.parse_file(source_path)
    source_bytes = source.encode("utf-8")

    # Step 4: Extract symbols
    extractor = SymbolExtractor()
    raw_symbols = extractor.extract(tree, source, language, source_file=source_path)

    # Register symbols
    registry = SymbolRegistry()
    symbols = [registry.register(raw) for raw in raw_symbols]

    # Step 5: Structure extraction
    sig_extractor = SignatureExtractor()
    signatures = [sig_extractor.extract(sym) for sym in symbols]

    hierarchy_builder = HierarchyBuilder()
    hierarchy = hierarchy_builder.build(symbols)
    hierarchy_builder.assign_parents(symbols)

    callgraph_builder = CallGraphBuilder()
    call_edges, call_edge_metadata = callgraph_builder.build_with_metadata(
        tree, source_bytes, language, symbols
    )
    callgraph_builder.apply_to_symbols(call_edges, symbols)

    # Step 9: Salience scoring
    scorer = SalienceScorer()
    _salience_scores = scorer.score(symbols)

    # ── Phase 1: New component layers ──

    # Temporal (optional, requires git)
    git = GitHistory()
    temporal_analyzer = TemporalAnalyzer(git)
    temporal_component = temporal_analyzer.analyze(symbols, source_path)

    # Intent inference
    intents_component = IntentInferrer().infer(tree, source_bytes, language, symbols)

    # Error flow analysis
    errors_component = ErrorFlowAnalyzer().analyze(tree, source_bytes, language, symbols)

    # Test coverage mapping
    tests_component = TestCoverageMapper().map(symbols, call_edges, language, source_path)

    # Step 6: Body compression (now with temporal + coverage overrides)
    compressor = BodyCompressor()
    symbol_dict = registry.symbol_dict()
    body_entries = [compressor.compress(sym, symbol_dict) for sym in symbols]

    # Step 8: Semantic fingerprint
    fingerprint = SemanticFingerprint()
    sem_hash = fingerprint.compute(symbols)

    # Build components
    semantic_graph = _build_semantic_graph(symbols, call_edges)
    nav_symbols = {
        str(s.id): s
        for s in symbols
        if s.raw.kind.value in {"f", "t", "m"}
    }
    nav_ids = set(nav_symbols.keys())
    nav_call_edges = [
        (caller, callee)
        for caller, callee in call_edges
        if caller in nav_ids and callee in nav_ids
    ]
    nav_call_meta = [
        m for m in call_edge_metadata if m.get("caller_id") in nav_ids
    ]

    struct_component = StructComponent(
        source_file=source_path,
        signatures=signatures,
        hierarchy=hierarchy,
        call_edges=nav_call_edges,
        call_edge_metadata=nav_call_meta,
        symbols=nav_symbols,
    )

    bodies_component = BodiesComponent(
        source_file=source_path,
        entries=body_entries,
        symbol_dict=symbol_dict,
    )

    # Step 7: Serialize
    serializer = AlephSerializer()
    struct_text = serializer.serialize_struct(struct_component)
    bodies_text = serializer.serialize_bodies(bodies_component)

    # Token comparison uses a minimal navigation profile (map-first loading).
    comparison = compare_tokens(source, _navigation_profile_text(struct_component))

    # Write files if output_dir specified
    if output_dir:
        writer = FileComponentWriter(output_dir)
        writer.write_struct(struct_component)
        writer.write_bodies(bodies_component)
        writer.write_temporal(temporal_component)
        writer.write_intents(intents_component)
        writer.write_errors(errors_component)
        writer.write_tests(tests_component)

    return {
        "source_file": source_path,
        "language": language,
        "symbols_extracted": len(symbols),
        "call_edges": len(call_edges),
        "semantic_hash": sem_hash,
        "struct_text": struct_text,
        "bodies_text": bodies_text,
        "original_tokens": comparison.original_tokens,
        "compressed_tokens": comparison.compressed_tokens,
        "token_reduction_percent": comparison.reduction_percent,
        "struct_component": struct_component,
        "bodies_component": bodies_component,
        "temporal_component": temporal_component,
        "intents_component": intents_component,
        "errors_component": errors_component,
        "tests_component": tests_component,
        "symbols": symbols,
        "registry": registry,
        "semantic_graph": semantic_graph,
        "call_edge_metadata": call_edge_metadata,
    }


def _build_semantic_graph(symbols, call_edges) -> SemanticGraph:
    graph = SemanticGraph()
    by_id = {str(s.id): s for s in symbols}
    for sym in symbols:
        graph.add_node(
            sym.id,
            kind=sym.raw.kind.value,
            qualified_name=sym.raw.qualified_name,
            scope=sym.raw.scope,
            language=sym.raw.language,
            source_file=sym.raw.source_file,
        )
        if sym.parent:
            graph.add_edge(sym.parent, sym.id, "contains")

    for caller_id, callee_id in call_edges:
        caller = by_id.get(caller_id)
        callee = by_id.get(callee_id)
        if caller and callee:
            graph.add_edge(caller.id, callee.id, "calls")
    return graph


def _navigation_profile_text(struct_component: StructComponent) -> str:
    lines = ["[ALEPH:NAV:1.0]", "[SYMBOLS]"]
    for sid, sym in sorted(struct_component.symbols.items()):
        lines.append(f"{sid} {sym.raw.qualified_name}")
    lines.append("[/SYMBOLS]")
    return "\n".join(lines) + "\n"


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
    root = os.path.abspath(args.path)
    index_path = args.index_file or _default_index_path(root)
    previous = load_index(index_path)
    payload, stats = build_index(root, run_pipeline, previous=previous)
    save_index(index_path, payload)
    if args.json:
        _json_print(
            {
                "index_file": index_path,
                "indexed_files": stats.indexed_files,
                "reused_files": stats.reused_files,
                "total_files": len(payload["files"]),
            }
        )
        return
    print(f"Index written: {index_path}")
    print(f"Indexed: {stats.indexed_files}, Reused: {stats.reused_files}, Total: {len(payload['files'])}")


def _load_required_index(index_file: str | None, path_hint: str = ".") -> tuple[str, dict]:
    idx_path = index_file or _default_index_path(os.path.abspath(path_hint))
    if not os.path.isfile(idx_path):
        print(f"Error: index not found at {idx_path}. Run `aleph index` first.", file=sys.stderr)
        sys.exit(1)
    return idx_path, load_index(idx_path)


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
    body = engine.expand(args.query_args)
    if body is None:
        print(f"No body found for {args.query_args}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        _json_print({"symbol_id": args.query_args, "body": body})
        return
    print(body)


def _query_resolve(engine: QueryEngine, args) -> None:
    result = engine.resolve(args.query_args)
    if result is None:
        print(f"No symbol found for {args.query_args}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        _json_print(result.to_dict())
        return
    print(f"ID:        {result.symbol_id}")
    print(f"Name:      {result.name}")
    print(f"Qualified: {result.qualified_name}")
    print(f"Kind:      {result.kind}")
    print(f"Scope:     {result.scope}")
    print(f"File:      {result.file}")
    if result.signature_hash:
        print(f"Sig hash:  {result.signature_hash}")


def _query_callers(engine: QueryEngine, args) -> None:
    results = engine.callers(args.query_args)
    if args.json:
        _json_print({"symbol_id": args.query_args, "callers": [c.to_dict() for c in results]})
        return
    if not results:
        print(f"No callers found for {args.query_args}")
        return
    print(f"Callers of {args.query_args}: {len(results)}")
    for c in results:
        print(f"  {c.caller_id} {c.caller_name} ({c.caller_file})")


def _query_context(engine: QueryEngine, args) -> None:
    result = engine.context(args.query_args)
    if result is None:
        print(f"No symbol found for {args.query_args}", file=sys.stderr)
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
        _json_print({"query": args.query_args, "results": [r.to_dict() for r in results]})
        return
    if not results:
        print(f"No matches for '{args.query_args}'")
        return
    print(f"Matches for '{args.query_args}': {len(results)}")
    for r in results:
        print(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")


def _handle_resolve(args) -> None:
    idx_path, index = _load_required_index(args.index_file)
    results = query_symbols(index, args.symbol)
    if args.json:
        _json_print({"index_file": idx_path, "symbol": args.symbol, "matches": results})
        return
    if not results:
        print(f"No symbol match for {args.symbol}")
        return
    for match in results:
        print(f"{match['id']} -> {match['qualified_name']} ({match['file']})")


def _handle_neighbors(args) -> None:
    idx_path, index = _load_required_index(args.index_file)
    matches = query_symbols(index, args.symbol)
    if not matches:
        if args.json:
            _json_print({"index_file": idx_path, "neighbors": []})
        else:
            print(f"No symbol match for {args.symbol}")
        return

    target_ids = {m["id"] for m in matches}
    neighbors: set[tuple[str, str, str]] = set()
    for file_entry in index.get("files", {}).values():
        for caller, callee in file_entry.get("calls", []):
            if caller in target_ids:
                neighbors.add((caller, callee, "out"))
            if callee in target_ids:
                neighbors.add((callee, caller, "in"))

    payload = [
        {"symbol_id": src, "neighbor_id": dst, "direction": direction}
        for src, dst, direction in sorted(neighbors)
    ]
    if args.json:
        _json_print({"index_file": idx_path, "neighbors": payload})
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


def _build_index_from_result(root: str, result) -> dict:
    """Build query index from BuildResult without re-running the pipeline."""
    files = {}
    for source_file, file_result in result.file_results.items():
        rel_path = os.path.relpath(source_file, root)
        symbols = []
        for sym in file_result.get("symbols", []):
            symbols.append({
                "id": str(sym.id),
                "name": sym.raw.name,
                "qualified_name": sym.raw.qualified_name,
                "kind": sym.raw.kind.value,
                "scope": sym.raw.scope,
                "file": rel_path,
                "signature_hash": sym.raw.signature_text[:8] if sym.raw.signature_text else "",
            })
        calls = []
        struct = file_result.get("struct_component")
        if struct:
            calls = [(str(a), str(b)) for a, b in struct.call_edges]
        files[os.path.abspath(source_file)] = {
            "symbols": symbols,
            "calls": calls,
            "semantic_hash": file_result.get("semantic_hash", ""),
        }
    return {"version": "2.0", "root": root, "files": files}


def _auto_build(root: str, full: bool = False, per_file: bool = False):
    """Run an incremental build and write all artifacts. Returns BuildResult."""
    # Clear per-build state to prevent accumulation in long-running servers
    from aleph.ingest.node_types import clear_unknown_node_types
    clear_unknown_node_types()

    output_dir = os.path.join(root, ".aleph")
    cache_path = os.path.join(output_dir, CACHE_FILENAME)

    prev_cache: BuildCache | None = None
    if not full:
        prev_cache = load_build_cache(cache_path)
        if not prev_cache.files:
            prev_cache = None

    result = build_project(root, run_pipeline, cache=prev_cache)

    if result.cache is not None:
        save_build_cache(cache_path, result.cache)

    writer = FileComponentWriter(output_dir)
    writer.write_project_map(result.map_component)
    writer.write_project_dict(result.dict_component)
    writer.write_project_fs(result.fs_component)
    writer.write_project_struct(result.struct_component)
    writer.write_project_salience(result.salience_component)
    writer.write_project_attention(result.attention_component)
    writer.write_project_temporal(result.temporal_component)
    writer.write_project_coverage(result.coverage_component, salience=result.salience_component)

    if per_file:
        for source_file, file_result in result.file_results.items():
            rel_path = os.path.relpath(source_file, root)
            if "struct_component" not in file_result or "bodies_component" not in file_result:
                continue
            per_file_dir = os.path.join(output_dir, os.path.dirname(rel_path))
            per_file_writer = FileComponentWriter(per_file_dir)
            per_file_writer.write_struct(file_result["struct_component"])
            per_file_writer.write_bodies(file_result["bodies_component"])
            if file_result.get("temporal_component"):
                per_file_writer.write_temporal(file_result["temporal_component"])
            if file_result.get("intents_component"):
                per_file_writer.write_intents(file_result["intents_component"])
            if file_result.get("errors_component"):
                per_file_writer.write_errors(file_result["errors_component"])
            if file_result.get("tests_component"):
                per_file_writer.write_tests(file_result["tests_component"])

    # Build index from result (no re-running pipeline)
    index_path = os.path.join(output_dir, ".aleph.index.json")
    payload = _build_index_from_result(root, result)
    save_index(index_path, payload)

    # Report unknown node types (potential extraction gaps)
    from aleph.ingest.node_types import get_unknown_node_types, clear_unknown_node_types
    unknown = get_unknown_node_types()
    if unknown:
        print("[aleph] Unmapped node types (may need mapping):", file=sys.stderr)
        for lang, types in sorted(unknown.items()):
            sample = ", ".join(sorted(types)[:10])
            extra = f" (+{len(types) - 10} more)" if len(types) > 10 else ""
            print(f"  {lang}: {sample}{extra}", file=sys.stderr)

    return result


def _handle_build(args) -> None:
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output or os.path.join(root, ".aleph")
    result = _auto_build(root, full=args.full, per_file=getattr(args, "per_file", False))
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
            "artifacts": [
                "project.aleph.map",
                "project.aleph.dict",
                "project.aleph.fs",
                "project.aleph.struct",
                "project.aleph.salience",
                "project.aleph.attention",
                "project.aleph.temporal",
                "project.aleph.coverage",
                CACHE_FILENAME,
                ".aleph.index.json",
            ],
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
    print("  project.aleph.map")
    print("  project.aleph.dict")
    print("  project.aleph.fs")
    print("  project.aleph.struct")
    print("  project.aleph.salience")
    print("  project.aleph.attention")
    print("  project.aleph.temporal")
    print("  project.aleph.coverage")
    print(f"  {CACHE_FILENAME} (not committed)")
    print("  .aleph.index.json (not committed)")


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
    from aleph.project.indexer import discover_source_files
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
                    current = FileStamp.from_file(f)
                    if not prev_stamps[f].matches(current):
                        changed.append(f)
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
        record = mgr.propose(symbol_id, intent, file=args.file)
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


_CLAUDE_MD_TEMPLATE = """# Aleph — MANDATORY Instructions for Claude

## IMPORTANT: Use Aleph FIRST, always.

This project has Aleph MCP tools available. **You MUST use Aleph tools for ALL codebase navigation before falling back to Grep, Glob, or Read.** Aleph is 5x faster and uses 25x fewer tokens than raw file exploration.

**DO THIS:**
- `aleph_brief "your task"` — ALWAYS start here. One call replaces 5.
- `aleph_search "keyword"` — find symbols by name. Do NOT use Grep to search for code.
- `aleph_resolve <id>` — look up a symbol. Do NOT read files to find definitions.
- `aleph_callers <id>` — find who calls a function. Do NOT grep for function names.
- `aleph_context <id>` — understand a symbol's neighborhood. Do NOT read multiple files.
- `aleph_impact <id>` — check blast radius BEFORE modifying any function.
- `aleph_struct "file"` — understand a file's architecture. Do NOT read the whole file.

**DO NOT DO THIS:**
- Do NOT use Grep/Glob to search for code when `aleph_search` is available
- Do NOT read entire files when `aleph_resolve` or `aleph_expand` can give you what you need
- Do NOT guess at callers when `aleph_callers` tells you exactly
- Do NOT skip `aleph_impact` before modifying critical functions

## Planning

When entering plan mode or exploring the codebase:
1. **Start with `aleph_brief`** — relevant symbols, call context, impact risk, next steps
2. **Use `aleph_attention`** to know what matters most
3. Only fall back to Read/Grep if Aleph doesn't have what you need

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


def _handle_serve(args) -> None:
    from aleph.__version__ import __version__
    root = os.path.abspath(args.path)
    aleph_dir = os.path.join(root, ".aleph")
    dict_path = os.path.join(aleph_dir, "project.aleph.dict")

    print(f"[aleph] v{__version__}", file=sys.stderr)

    # License check (non-blocking)
    from aleph.licensing import validate_license, LicenseStatus
    from aleph.licensing.validator import check_team_usage, format_license_notice
    license_info = validate_license(root)
    is_team = check_team_usage(root) if not license_info.is_valid else False
    notice = format_license_notice(license_info, is_team=is_team)
    if notice:
        print(notice, file=sys.stderr)

    if not os.path.isfile(dict_path):
        print("[aleph] No artifacts found — building project...", file=sys.stderr)
        try:
            result = _auto_build(root)
            stats = result.stats
            reduction = (
                (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
                if stats.total_original_tokens > 0 else 0.0
            )
            print(
                f"[aleph] Built: {stats.total_files} files, "
                f"{stats.total_symbols} symbols, {reduction:.1f}% reduction",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[aleph] Auto-build failed: {e}", file=sys.stderr)
            print("[aleph] Starting server anyway — tools will return errors.", file=sys.stderr)

    _check_artifact_version(aleph_dir)
    from aleph.mcp.server import serve
    serve(root)


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

    index_parser = subparsers.add_parser("index", help="Index a project directory")
    index_parser.add_argument("path", nargs="?", default=".", help="Project root")
    index_parser.add_argument("--index-file", default=None, help="Index output file")
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
    resolve_parser.add_argument("--index-file", default=None, help="Existing index file")
    resolve_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    neighbors_parser = subparsers.add_parser("neighbors", help="Show graph neighbors for a symbol")
    neighbors_parser.add_argument("symbol", help="Symbol id/name/qualified name")
    neighbors_parser.add_argument("--index-file", default=None, help="Existing index file")
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

    diff_parser = subparsers.add_parser("diff", help="Semantic diff: compare current file against indexed version")
    diff_parser.add_argument("file", help="Source file to compare")
    diff_parser.add_argument("--index-file", default=None, help="Existing index file")
    diff_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    serve_parser = subparsers.add_parser("serve", help="Start MCP server for a built Aleph project")
    serve_parser.add_argument("path", nargs="?", default=".", help="Project root directory (must contain .aleph/)")

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
    elif args.command == "diff":
        _handle_diff(args)
    elif args.command == "serve":
        _handle_serve(args)
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
