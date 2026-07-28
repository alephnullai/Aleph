"""Aleph build pipeline — single-file compilation and project auto-build.

This module owns the canonical build entry points:

  run_pipeline(source_path)   — full single-file pipeline (parse → extract
                                → structure → compress → serialize)
  auto_build(root)            — incremental project build that writes all
                                project-level artifacts plus the query index

Both used to live in aleph.cli, which forced an inverted dependency
(mcp.server importing the CLI). cli.py now keeps thin wrappers around
these functions.
"""

from __future__ import annotations

import json
import os

from aleph.project.paths import rel_posix
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
from aleph.link.salience import SalienceScorer
from aleph.model.components import StructComponent, BodiesComponent
from aleph.model.graph import SemanticGraph
from aleph.util.hashing import byte_hash
from aleph.util.tokens import compare_tokens
from aleph.project.builder import build_project, BuildResult
from aleph.project.cache import (
    BuildCache, load_build_cache, CACHE_FILENAME,
)
from aleph.project.discovery import temporal_pathspecs
from aleph.project.parallel import ParallelBuildContext, resolve_jobs
from aleph.temporal.git_history import GitHistory
from aleph.util.progress import ProgressReporter, human_size
from aleph.temporal.analyzer import TemporalAnalyzer
from aleph.inference.intent_inference import IntentInferrer
from aleph.inference.error_flow import ErrorFlowAnalyzer
from aleph.inference.test_coverage import TestCoverageMapper


INDEX_FILENAME = ".aleph.index.json"

# Heavy pipeline components are stateless across files (the parser caches
# tree-sitter Language parsers internally) — construct once, reuse for every
# file instead of rebuilding them inside the per-file pipeline (P1-C).
_PIPELINE_PARSER: TreeSitterParser | None = None
_PIPELINE_EXTRACTOR: SymbolExtractor | None = None
_PIPELINE_COMPRESSOR: BodyCompressor | None = None


def _pipeline_components() -> tuple[TreeSitterParser, SymbolExtractor, BodyCompressor]:
    global _PIPELINE_PARSER, _PIPELINE_EXTRACTOR, _PIPELINE_COMPRESSOR
    if _PIPELINE_PARSER is None:
        _PIPELINE_PARSER = TreeSitterParser()
        _PIPELINE_EXTRACTOR = SymbolExtractor()
        _PIPELINE_COMPRESSOR = BodyCompressor()
    return _PIPELINE_PARSER, _PIPELINE_EXTRACTOR, _PIPELINE_COMPRESSOR


def run_pipeline(
    source_path: str,
    output_dir: str | None = None,
    project_root: str | None = None,
    git_history: GitHistory | None = None,
) -> dict:
    """Run the full single-file Aleph pipeline.

    Args:
        source_path: File to compile.
        output_dir: Optional directory to write per-file components to.
        project_root: Project root for portable symbol IDs (scheme v2 —
            IDs hash root-relative paths and survive repo moves). When
            None, the legacy absolute-path ID scheme is used (deprecated).
        git_history: Shared GitHistory for the temporal layer. Project
            builds MUST pass one instance for all files: its batched
            ``git log --numstat`` cache is per-instance, so a fresh
            GitHistory per file re-runs the (expensive) repo log every
            time. When None a private instance is created (single-file
            CLI use).

    Returns a dict with pipeline results including token comparison.
    """
    parser, extractor, compressor = _pipeline_components()

    # Step 3: Parse
    tree, source, language = parser.parse_file(source_path)
    source_bytes = source.encode("utf-8")

    # Step 4: Extract symbols
    raw_symbols = extractor.extract(tree, source, language, source_file=source_path)

    # Register symbols (root-relative portable IDs when project_root given)
    registry = SymbolRegistry(project_root=project_root)
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
    git = git_history or GitHistory()
    temporal_analyzer = TemporalAnalyzer(git)
    temporal_component = temporal_analyzer.analyze(symbols, source_path)

    # Intent inference
    intents_component = IntentInferrer().infer(tree, source_bytes, language, symbols)

    # Error flow analysis
    errors_component = ErrorFlowAnalyzer().analyze(tree, source_bytes, language, symbols)

    # Test coverage mapping
    tests_component = TestCoverageMapper().map(symbols, call_edges, language, source_path)

    # Step 6: Body compression (now with temporal + coverage overrides)
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


# ── Query index (.aleph.index.json) ──


def load_index(path: str) -> dict:
    """Load the query index JSON written by auto_build."""
    if not os.path.isfile(path):
        return {"version": "2.0", "root": "", "files": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def build_index_from_result(root: str, result: BuildResult) -> dict:
    """Build the query index from a BuildResult without re-running the pipeline.

    Per-file entries carry symbols, within-file call edges, the semantic
    hash, and signature/body hashes (used by `aleph diff` to detect
    signature/body-level changes — migrated from the legacy v1.0 indexer).
    """
    files = {}
    for source_file, file_result in result.file_results.items():
        rel_path = rel_posix(source_file, root)
        symbols = []
        signature_hashes: dict[str, str] = {}
        body_hashes: dict[str, str] = {}
        for sym in file_result.get("symbols", []):
            sid = str(sym.id)
            symbols.append({
                "id": sid,
                "name": sym.raw.name,
                "qualified_name": sym.raw.qualified_name,
                "kind": sym.raw.kind.value,
                "scope": sym.raw.scope,
                "file": rel_path,
                "signature_hash": sym.raw.signature_text[:8] if sym.raw.signature_text else "",
            })
            if sym.raw.signature_text:
                signature_hashes[sid] = byte_hash(sym.raw.signature_text)[:8]
            if sym.raw.body_text:
                body_hashes[sid] = byte_hash(sym.raw.body_text)[:8]
        calls = []
        struct = file_result.get("struct_component")
        if struct:
            calls = [(str(a), str(b)) for a, b in struct.call_edges]
        files[os.path.abspath(source_file)] = {
            "symbols": symbols,
            "calls": calls,
            "semantic_hash": file_result.get("semantic_hash", ""),
            "signature_hashes": signature_hashes,
            "body_hashes": body_hashes,
        }
    return {"version": "2.0", "root": root, "files": files}


# ── Project auto-build ──


def auto_build(
    root: str,
    full: bool = False,
    per_file: bool = False,
    text_artifacts: bool = True,
    semantic: bool = False,
    progress: ProgressReporter | None = None,
) -> BuildResult:
    """Run an incremental build and write all artifacts. Returns BuildResult.

    The SQLite store (.aleph/aleph.db) is the canonical output: build
    cache stamps, symbols (with bodies), and call edges live there, and
    incremental rebuilds only rewrite the changed files' rows. The text
    artifacts are still written by default (dual-write) for existing
    consumers; pass text_artifacts=False (CLI: --no-text-artifacts) to
    skip them — they can be regenerated any time with `aleph export`.

    semantic=True (CLI: --semantic) additionally embeds one passage per
    symbol into the db's embeddings table (optional fastembed extra,
    see aleph.query.semantic). The choice is remembered in db meta, so
    subsequent incremental builds keep re-embedding changed files even
    without the flag. When fastembed is not installed the build prints
    one warning and degrades to lexical-only.

    progress reports build phases/liveness to stderr (stdout stays clean
    for --json). When None, a default reporter is created: on when
    stderr is a TTY or ALEPH_PROGRESS=1, off otherwise (see
    aleph.util.progress). Pass ProgressReporter(quiet=True) to silence.
    """
    # Clear per-build state to prevent accumulation in long-running servers
    from aleph.ingest.node_types import clear_unknown_node_types
    from aleph.store.sqlite_store import SqliteStore, DB_FILENAME
    clear_unknown_node_types()

    if progress is None:
        progress = ProgressReporter()

    output_dir = os.path.join(root, ".aleph")
    cache_path = os.path.join(output_dir, CACHE_FILENAME)
    db_path = os.path.join(output_dir, DB_FILENAME)

    prev_cache: BuildCache | None = None
    semantic_enabled = semantic
    if os.path.isfile(db_path):
        try:
            read_store = SqliteStore(db_path)
            try:
                # Sticky semantic flag: once a build ran with --semantic
                # the db remembers it (also honored on --full rebuilds).
                if read_store.get_meta("semantic") == "1":
                    semantic_enabled = True
                if not full:
                    prev_cache = read_store.load_build_cache(root)
            finally:
                read_store.close()
        except Exception:
            prev_cache = None
    if not full:
        if prev_cache is None or not prev_cache.files:
            # Migration path: fall back to the legacy JSON cache once so
            # the first build after upgrading stays incremental.
            prev_cache = load_build_cache(cache_path)
        if not prev_cache.files:
            prev_cache = None

    embedder = None
    if semantic_enabled:
        from aleph.query.semantic import get_passage_embedder
        embedder = get_passage_embedder()
        if embedder is None:
            print(
                "[aleph] semantic index requested but fastembed is not "
                "installed — building lexical-only. Install with: "
                "pip install 'aleph-compiler[semantic]'",
                file=sys.stderr,
            )

    # Thread the project root into the per-file pipeline so symbol IDs are
    # derived from root-relative paths (portable ID scheme v2).
    abs_root = os.path.abspath(root)

    # One GitHistory for the whole build: its batched repo log runs once
    # (not once per file), is scoped to indexed source extensions with
    # vendor/binary-dir excludes, capped, and wall-clock bounded — see
    # GitHistory / temporal_pathspecs.
    shared_git = GitHistory(
        pathspecs=temporal_pathspecs(abs_root),
        on_progress=lambda done, total: progress.subtask(
            "temporal: git history", done, total, "commits"),
        on_warning=progress.warn,
    )

    def _runner(source_path: str) -> dict:
        return run_pipeline(
            source_path, project_root=abs_root, git_history=shared_git,
        )

    # P2-b: parallel parsing. ALEPH_JOBS=1 forces the exact sequential
    # path; unset defaults to min(8, cpu_count - 1) with a small-build
    # cutoff (see aleph.project.parallel). per_file builds stay
    # sequential: per-file text artifacts need the live pipeline objects
    # (bodies/struct components), which worker payloads — the serialized
    # CachedFileResult subset — deliberately do not carry.
    jobs, auto_jobs = resolve_jobs()
    parallel_ctx: ParallelBuildContext | None = None
    if jobs > 1 and not per_file:
        # Pre-warm the batched repo log + blame gate in the PARENT and
        # ship the plain-dict caches to workers: the log must run once
        # per build, never once per worker.
        shared_git.prewarm(abs_root)
        parallel_ctx = ParallelBuildContext(
            jobs=jobs,
            project_root=abs_root,
            git_state=shared_git.export_state(),
            auto=auto_jobs,
        )

    result = build_project(
        root, _runner, cache=prev_cache, progress=progress,
        parallel=parallel_ctx,
    )

    if parallel_ctx is not None:
        # Fold worker blame instrumentation back into the shared
        # instance so the summary reports the build-wide blame cost.
        shared_git.blame_calls += parallel_ctx.blame_calls
        shared_git.blame_seconds += parallel_ctx.blame_seconds

    # Canonical store first: per-file DELETE+INSERT in one transaction.
    progress.phase("store write")
    store = SqliteStore(db_path, create=True)
    try:
        persisted = store.persist_build(root, result, prev_cache, embedder=embedder)
        if full:
            # A full rebuild deletes every prior row; SQLite leaves the
            # freed pages on the freelist and the file never shrinks
            # (observed: 41% dead space, 590MB file for 340MB of data).
            # VACUUM is ~1.5s even on a 590MB store — always worth it here.
            store.vacuum()
    finally:
        store.close()
    if embedder is not None and persisted.get("symbols_embedded"):
        print(
            f"[aleph] semantic: embedded {persisted['symbols_embedded']} "
            f"symbols ({embedder.model})",
            file=sys.stderr,
        )
    # The legacy monolithic JSON cache is superseded by the db.
    if os.path.isfile(cache_path):
        try:
            os.remove(cache_path)
        except OSError:
            pass

    progress.phase("artifact write")
    if text_artifacts:
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
            rel_path = rel_posix(source_file, root)
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

    if text_artifacts:
        # Build index from result (no re-running pipeline)
        index_path = os.path.join(output_dir, INDEX_FILENAME)
        payload = build_index_from_result(root, result)
        save_index(index_path, payload)

    # Report unknown node types (potential extraction gaps)
    from aleph.ingest.node_types import get_unknown_node_types
    unknown = get_unknown_node_types()
    if unknown:
        print("[aleph] Unmapped node types (may need mapping):", file=sys.stderr)
        for lang, types in sorted(unknown.items()):
            sample = ", ".join(sorted(types)[:10])
            extra = f" (+{len(types) - 10} more)" if len(types) > 10 else ""
            print(f"  {lang}: {sample}{extra}", file=sys.stderr)

    progress.summary(_build_summary_line(result, output_dir, db_path, shared_git))
    return result


def _build_summary_line(
    result: BuildResult, output_dir: str, db_path: str,
    git: GitHistory | None = None,
) -> str:
    """Compose the final progress summary: files, symbols, artifact sizes,
    and the temporal layer's blame cost (subprocess count + wall time)."""
    stats = result.stats
    sizes: list[str] = []
    try:
        if os.path.isfile(db_path):
            sizes.append(f"db {human_size(os.path.getsize(db_path))}")
        text_total = 0
        for name in os.listdir(output_dir):
            if name.startswith("project.aleph.") or name == INDEX_FILENAME:
                text_total += os.path.getsize(os.path.join(output_dir, name))
        if text_total:
            sizes.append(f"text artifacts {human_size(text_total)}")
    except OSError:
        pass
    size_part = f" — {', '.join(sizes)}" if sizes else ""
    blame_part = ""
    if git is not None:
        blame_part = (
            f"; temporal blame: {git.blame_calls} calls, "
            f"{git.blame_seconds:.1f}s"
        )
    return (
        f"build complete: {stats.total_files} files "
        f"({stats.rebuilt_files} rebuilt, {stats.reused_files} reused), "
        f"{stats.total_symbols} symbols{size_part}{blame_part}"
    )
