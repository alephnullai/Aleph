"""Project-level build: compile all files and produce project components."""

from __future__ import annotations

import multiprocessing
import os

from aleph.project.paths import rel_posix
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Callable

from aleph.ingest.node_types import merge_unknown_node_types
from aleph.model.components import (
    ProjectMapComponent, ProjectFileEntry,
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectFSComponent, ProjectFSEntry, ProjectModuleDep,
    ProjectStructComponent, ProjectCrossRef, ProjectFileDep,
    ProjectSalienceComponent, ProjectAttentionComponent,
    ProjectTemporalComponent,
    ProjectCoverageComponent, ProjectCoverageEntry,
)
from aleph.project.discovery import discover_source_files
from aleph.project.cache import (
    BuildCache, CachedFileResult, reconstruct_build_result,
    cache_from_pipeline_result,
)
from aleph.project.parallel import (
    MIN_AUTO_PARALLEL_FILES, ParallelBuildContext,
    error_payload, worker_init, worker_run_file,
)
from aleph.link.project_salience import compute_project_salience, compute_attention_budget, _is_vendor_file
from aleph.temporal.git_analyzer import compute_project_temporal
from aleph.util.hashing import byte_hash
from aleph.util.progress import ProgressReporter


@dataclass
class BuildStats:
    total_files: int = 0
    total_symbols: int = 0
    total_call_edges: int = 0
    total_cross_refs: int = 0
    total_original_tokens: int = 0
    total_compressed_tokens: int = 0
    rebuilt_files: int = 0
    reused_files: int = 0
    removed_files: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class BuildResult:
    map_component: ProjectMapComponent
    dict_component: ProjectDictComponent
    fs_component: ProjectFSComponent
    struct_component: ProjectStructComponent
    salience_component: ProjectSalienceComponent
    attention_component: ProjectAttentionComponent
    temporal_component: ProjectTemporalComponent
    coverage_component: ProjectCoverageComponent
    stats: BuildStats
    file_results: dict[str, dict]
    cache: BuildCache | None = None


def build_project(
    root: str,
    runner: Callable[[str], dict],
    cache: BuildCache | None = None,
    progress: ProgressReporter | None = None,
    parallel: ParallelBuildContext | None = None,
) -> BuildResult:
    """Run the pipeline on every source file and produce project-level components.

    When a BuildCache is provided, unchanged files are skipped and their
    cached results are reused.  The returned BuildResult includes an updated
    cache that the caller should persist for the next build.

    Args:
        root: Project root directory.
        runner: Single-file pipeline function (e.g. run_pipeline). Also
            the sequential fallback when a parallel worker pool breaks.
        cache: Optional previous build cache for incremental builds.
        progress: Optional ProgressReporter for stderr phase/tick lines
            (auto_build passes one; direct API callers default to silent).
        parallel: Optional ParallelBuildContext (see aleph.project.parallel).
            When given with jobs > 1, stale files are parsed in a worker
            pool; results are assembled in submission order so the output
            is identical to a sequential build. None (or jobs <= 1) takes
            the exact sequential path.

    Returns:
        BuildResult with all project components, stats, and updated cache.
    """
    if progress is None:
        progress = ProgressReporter(quiet=True)
    root = os.path.abspath(root)
    progress.phase("discovery")
    source_files = discover_source_files(root, on_warning=progress.warn)
    stats = BuildStats(total_files=len(source_files))

    # Prepare cache (create fresh if none provided)
    updated_cache = BuildCache(root=root)
    if cache is not None:
        # Remove entries for files that no longer exist
        current_set = set(source_files)
        removed = cache.remove_stale(current_set)
        stats.removed_files = len(removed)

    file_results: dict[str, dict] = {}
    map_entries: list[ProjectFileEntry] = []
    dict_entries: list[ProjectSymbolEntry] = []
    fs_entries: list[ProjectFSEntry] = []

    # Per-file: symbol_id -> file/name mapping for cross-ref detection
    symbol_to_file: dict[str, str] = {}
    symbol_to_name: dict[str, str] = {}

    # Aggregation sinks shared by the sequential loop and the parallel
    # assembly (bundled so per-file processing is one call in both paths).
    sinks = _AggregationSinks(
        root=root,
        stats=stats,
        file_results=file_results,
        map_entries=map_entries,
        dict_entries=dict_entries,
        fs_entries=fs_entries,
        symbol_to_file=symbol_to_file,
        symbol_to_name=symbol_to_name,
    )

    progress.phase("parse/compress", total=len(source_files), unit="files")

    # P2-b: decide whether the parallel path is worth engaging. The
    # freshness split is computed up front (the same checks the
    # sequential loop performs per file); a fully-fresh incremental
    # rebuild or a small auto-mode build never pays worker spawn costs.
    par = parallel if (parallel is not None and parallel.jobs > 1) else None
    fresh_entries: dict[int, CachedFileResult] = {}
    stale_indices: list[int] = []
    if par is not None:
        for index, source_file in enumerate(source_files):
            if cache is not None and cache.is_fresh(source_file):
                fresh_entries[index] = cache.get_cached(source_file)
            else:
                stale_indices.append(index)
        if not stale_indices or (
            par.auto and len(stale_indices) < MIN_AUTO_PARALLEL_FILES
        ):
            par = None

    if par is not None:
        _parse_files_parallel(
            source_files, runner, cache, updated_cache, progress, par,
            fresh_entries, stale_indices, sinks,
        )
    else:
        for file_index, source_file in enumerate(source_files, start=1):
            progress.tick(file_index, len(source_files), "files")

            # Incremental: try to reuse cached result
            if cache is not None and cache.is_fresh(source_file):
                cached_entry = cache.get_cached(source_file)
                result = reconstruct_build_result(cached_entry, source_file)
                updated_cache.files[source_file] = cached_entry
                stats.reused_files += 1
            else:
                try:
                    result = runner(source_file)
                except Exception as e:
                    stats.errors.append(f"{source_file}: {e}")
                    continue
                # Cache the new result
                updated_cache.update(source_file, result)
                stats.rebuilt_files += 1

            sinks.aggregate(source_file, result)

    # Cross-file analysis: detect cross-references from resolved call edges
    progress.phase("graph")
    cross_refs: list[ProjectCrossRef] = []
    # Track file-level deps: (src_file, dst_file) -> count
    file_dep_counts: dict[tuple[str, str], int] = defaultdict(int)
    # Module deps for FS component
    module_dep_counts: dict[tuple[str, str], int] = defaultdict(int)

    for source_file, result in file_results.items():
        rel_path = rel_posix(source_file, root)
        struct = result["struct_component"]
        for caller_id, callee_id in struct.call_edges:
            callee_file = symbol_to_file.get(callee_id)
            if callee_file and callee_file != rel_path:
                cross_refs.append(ProjectCrossRef(
                    caller_id=caller_id,
                    callee_id=callee_id,
                    source_file=rel_path,
                    target_file=callee_file,
                    caller_name=symbol_to_name.get(caller_id, ""),
                    callee_name=symbol_to_name.get(callee_id, ""),
                ))
                file_dep_counts[(rel_path, callee_file)] += 1
                module_dep_counts[(rel_path, callee_file)] += 1

    # Phase 2.8: Cross-file call resolution for unresolved calls
    name_index = _build_global_name_index(file_results, root)
    import_graph = _build_import_graph(file_results, root)
    new_cross_refs, new_dep_counts = _resolve_cross_file_calls(
        file_results, root, name_index, import_graph, symbol_to_file, symbol_to_name,
    )
    cross_refs.extend(new_cross_refs)
    for key, count in new_dep_counts.items():
        file_dep_counts[key] += count
        module_dep_counts[key] += count

    stats.total_cross_refs = len(cross_refs)

    # Build file deps
    file_deps = [
        ProjectFileDep(source=src, target=tgt, symbol_refs=count)
        for (src, tgt), count in sorted(file_dep_counts.items())
    ]

    # Build module deps
    module_deps = [
        ProjectModuleDep(source=src, target=tgt, symbol_count=count)
        for (src, tgt), count in sorted(module_dep_counts.items())
    ]

    # Sort entries for deterministic output
    map_entries.sort(key=lambda e: e.path)
    dict_entries.sort(key=lambda e: (e.file, e.symbol_id))
    fs_entries.sort(key=lambda e: e.path)
    cross_refs.sort(key=lambda x: (x.source_file, x.caller_id, x.callee_id))

    # Phase 2.2: Compute project-wide salience and attention budget
    # Pass only the newly-resolved cross-refs (not the ones already in call_edges)
    progress.phase("salience")
    salience_component = compute_project_salience(root, file_results, cross_refs=new_cross_refs)
    progress.phase("attention")
    attention_component = compute_attention_budget(salience_component)

    # Phase 2.5: Compute project-level temporal data from git history
    progress.phase("temporal")
    temporal_component = compute_project_temporal(root, file_results)

    # Phase 2.6 + P1-B: Aggregate test coverage across all files, then
    # recompute at project level using resolved cross-file call edges so
    # tests in tests/ actually cover symbols in src/.
    progress.phase("coverage")
    coverage_component = _compute_project_coverage(
        root, file_results, dict_entries, cross_refs=cross_refs,
    )

    return BuildResult(
        map_component=ProjectMapComponent(root=root, files=map_entries),
        dict_component=ProjectDictComponent(root=root, symbols=dict_entries),
        fs_component=ProjectFSComponent(root=root, files=fs_entries, module_deps=module_deps),
        struct_component=ProjectStructComponent(root=root, cross_refs=cross_refs, file_deps=file_deps),
        salience_component=salience_component,
        attention_component=attention_component,
        temporal_component=temporal_component,
        coverage_component=coverage_component,
        stats=stats,
        file_results=file_results,
        cache=updated_cache,
    )


@dataclass
class _AggregationSinks:
    """Per-file aggregation state shared by sequential and parallel paths.

    ``aggregate`` is the body of the original sequential loop after a
    file's result dict is obtained — keeping it in one place guarantees
    both paths produce identical project aggregates.
    """
    root: str
    stats: BuildStats
    file_results: dict[str, dict]
    map_entries: list[ProjectFileEntry]
    dict_entries: list[ProjectSymbolEntry]
    fs_entries: list[ProjectFSEntry]
    symbol_to_file: dict[str, str]
    symbol_to_name: dict[str, str]

    def aggregate(self, source_file: str, result: dict) -> None:
        rel_path = rel_posix(source_file, self.root)
        self.file_results[source_file] = result

        # Map entry
        self.map_entries.append(ProjectFileEntry(
            path=rel_path,
            language=result["language"],
            semantic_hash=result["semantic_hash"],
            symbol_count=result["symbols_extracted"],
            call_edge_count=result["call_edges"],
            original_tokens=result["original_tokens"],
            compressed_tokens=result["compressed_tokens"],
            reduction_percent=result["token_reduction_percent"],
        ))

        self.stats.total_symbols += result["symbols_extracted"]
        self.stats.total_call_edges += result["call_edges"]
        self.stats.total_original_tokens += result["original_tokens"]
        self.stats.total_compressed_tokens += result["compressed_tokens"]

        # Dict entries
        for sym in result["symbols"]:
            sid = str(sym.id)
            self.symbol_to_file[sid] = rel_path
            self.symbol_to_name[sid] = sym.raw.qualified_name
            sig_hash = byte_hash(sym.raw.signature_text)[:8] if sym.raw.signature_text else ""
            span = getattr(sym.raw, "span", None)
            self.dict_entries.append(ProjectSymbolEntry(
                symbol_id=sid,
                name=sym.raw.name,
                qualified_name=sym.raw.qualified_name,
                kind=sym.raw.kind.value,
                scope=sym.raw.scope,
                file=rel_path,
                signature_hash=sig_hash,
                # tree-sitter spans are 0-based; record 1-based lines
                start_line=(span.start_line + 1) if span is not None else 0,
                end_line=(span.end_line + 1) if span is not None else 0,
                language=sym.raw.language or result["language"],
            ))

        # FS entry
        self.fs_entries.append(ProjectFSEntry(
            path=rel_path,
            language=result["language"],
            symbol_count=result["symbols_extracted"],
        ))


# Navigation symbol kinds — a fresh run_pipeline result's StructComponent
# carries call_edge_metadata for these caller kinds only, while the cache
# stores the full list; the parallel assembly re-applies the filter so a
# parallel build is byte-identical to a sequential (fresh) one.
_NAV_KINDS = ("f", "t", "m")


def _result_from_payload(
    source_file: str, payload: dict, updated_cache: BuildCache,
) -> dict:
    """Turn a worker payload's serialized CachedFileResult into the result
    dict the aggregation consumes, registering it in the updated cache."""
    cached_entry = CachedFileResult.from_dict(payload["cached"])
    updated_cache.files[source_file] = cached_entry
    result = reconstruct_build_result(cached_entry, source_file)
    nav_ids = {
        d["id"] for d in cached_entry.symbols_data
        if d.get("kind") in _NAV_KINDS
    }
    result["struct_component"].call_edge_metadata = [
        m for m in cached_entry.call_edge_metadata
        if m.get("caller_id") in nav_ids
    ]
    return result


def _parse_files_parallel(
    source_files: list[str],
    runner: Callable[[str], dict],
    cache: BuildCache | None,
    updated_cache: BuildCache,
    progress: ProgressReporter,
    par: ParallelBuildContext,
    fresh_entries: dict[int, CachedFileResult],
    stale_indices: list[int],
    sinks: _AggregationSinks,
) -> None:
    """Worker-pool replacement for the sequential parse/compress loop.

    Determinism: payloads are ASSEMBLED strictly in submission
    (discovery) order regardless of completion order, so every
    downstream aggregate sees the same iteration order as a sequential
    build. Progress ticks fire from the parent as futures complete, and
    the wait() timeout keeps the non-TTY heartbeat alive during long
    files.

    Failure isolation: a Python exception inside a worker comes back as
    that file's error payload (same wording as the sequential loop); a
    broken pool (worker hard-crash, spawn failure) degrades to
    in-process sequential parsing for everything not yet assembled,
    with one warning.
    """
    stats = sinks.stats
    total = len(source_files)
    payloads: dict[int, dict] = {}
    next_index = 0  # next position to assemble (submission order)
    broken = False
    completed = 0

    def _consume(index: int) -> None:
        source_file = source_files[index]
        if index in fresh_entries:
            cached_entry = fresh_entries[index]
            result = reconstruct_build_result(cached_entry, source_file)
            updated_cache.files[source_file] = cached_entry
            stats.reused_files += 1
            sinks.aggregate(source_file, result)
            return
        payload = payloads.pop(index)
        for message in payload["warnings"]:
            progress.warn(message)
        merge_unknown_node_types(payload["unknown"])
        par.blame_calls += payload["blame_calls"]
        par.blame_seconds += payload["blame_seconds"]
        if payload["error"] is not None:
            stats.errors.append(f"{source_file}: {payload['error']}")
            return
        result = _result_from_payload(source_file, payload, updated_cache)
        stats.rebuilt_files += 1
        sinks.aggregate(source_file, result)

    try:
        # Spawn everywhere: matches Windows/macOS defaults, avoids
        # fork-with-threads hazards on Linux, and keeps worker behavior
        # identical across platforms. Only module-level functions and
        # plain data cross the boundary (see aleph.project.parallel).
        mp_context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=min(par.jobs, len(stale_indices)),
            mp_context=mp_context,
            initializer=worker_init,
            initargs=(par.project_root, par.git_state),
        ) as pool:
            future_to_index = {
                pool.submit(worker_run_file, source_files[index]): index
                for index in stale_indices
            }
            pending = set(future_to_index)
            while pending and not broken:
                done, pending = wait(
                    pending, timeout=0.5, return_when=FIRST_COMPLETED,
                )
                for future in done:
                    index = future_to_index[future]
                    try:
                        payloads[index] = future.result()
                    except BrokenProcessPool:
                        broken = True
                    except Exception as e:  # unpicklable result, etc.
                        payloads[index] = error_payload(
                            source_files[index], str(e))
                    completed += 1
                # Assemble whatever prefix is ready, in submission order.
                while next_index < total and (
                    next_index in fresh_entries or next_index in payloads
                ):
                    _consume(next_index)
                    next_index += 1
                progress.tick(
                    min(len(fresh_entries) + completed, total), total, "files",
                )
    except Exception as e:  # pool construction/submission failure
        broken = True
        progress.warn(f"parallel parse: worker pool failed ({e})")

    if next_index >= total:
        return

    # Degraded remainder: pool broke (or never started). Anything already
    # received is still used; the rest runs through the sequential runner
    # with the standard per-file error handling.
    if broken:
        progress.warn(
            "parallel parse: worker pool broke — finishing "
            f"{total - next_index} remaining files sequentially"
        )
    for index in range(next_index, total):
        progress.tick(index + 1, total, "files")
        source_file = source_files[index]
        if index in fresh_entries or index in payloads:
            _consume(index)
            continue
        try:
            result = runner(source_file)
        except Exception as e:
            stats.errors.append(f"{source_file}: {e}")
            continue
        updated_cache.update(source_file, result)
        stats.rebuilt_files += 1
        sinks.aggregate(source_file, result)


# Project-level coverage: transitive reach from a test function counts as
# (weak) coverage up to this many call-graph hops. Direct calls are strong.
_COVERAGE_MAX_HOPS = 2


def _compute_project_coverage(
    root: str,
    file_results: dict[str, dict],
    dict_entries: list[ProjectSymbolEntry],
    cross_refs: list[ProjectCrossRef] | None = None,
) -> ProjectCoverageComponent:
    """Aggregate test coverage into a project-level component.

    Starts from the per-file mapping (tests covering symbols in the same
    file), then recomputes coverage at the project level: a symbol is
    covered when it is reachable from a test function through resolved
    call edges (including cross-file edges) within _COVERAGE_MAX_HOPS.
    Direct calls are strong coverage; test_count records the number of
    distinct test functions calling the symbol directly.
    """
    # Build symbol_id -> (qualified_name, file) lookup
    sym_info: dict[str, tuple[str, str]] = {}
    for entry in dict_entries:
        sym_info[entry.symbol_id] = (entry.qualified_name, entry.file)

    # Pass 1: per-file coverage entries (backward-compatible base)
    entries: list[ProjectCoverageEntry] = []
    for source_file, result in file_results.items():
        rel_path = rel_posix(source_file, root)
        tests_comp = result.get("tests_component")
        if tests_comp is None:
            continue
        for cov in tests_comp.coverage:
            sid = str(cov.symbol_id)
            qname, fpath = sym_info.get(sid, (sid, rel_path))
            entries.append(ProjectCoverageEntry(
                symbol_id=sid,
                qualified_name=qname,
                file=fpath,
                status=cov.status,
                test_count=len(cov.test_ids),
            ))

    # Pass 2 (P1-B): project-level reachability from test functions over
    # the full resolved call graph (within-file + cross-file edges).
    adjacency: dict[str, set[str]] = defaultdict(set)
    for result in file_results.values():
        struct = result.get("struct_component")
        if struct is not None:
            for caller_id, callee_id in struct.call_edges:
                adjacency[caller_id].add(callee_id)
    for xref in cross_refs or []:
        adjacency[xref.caller_id].add(xref.callee_id)

    # Test roots: every test function identified by the per-file mapper
    test_ids: set[str] = set()
    for result in file_results.values():
        tests_comp = result.get("tests_component")
        if tests_comp is not None:
            for detail in tests_comp.test_details:
                test_ids.add(str(detail.test_id))

    direct_counts: dict[str, int] = defaultdict(int)
    reachable: set[str] = set()
    for tid in test_ids:
        seen = {tid}
        frontier = [tid]
        for depth in range(1, _COVERAGE_MAX_HOPS + 1):
            next_frontier: list[str] = []
            for node in frontier:
                for callee in adjacency.get(node, ()):
                    if callee in seen:
                        continue
                    seen.add(callee)
                    next_frontier.append(callee)
                    if callee in test_ids:
                        continue  # traverse through, but don't cover tests
                    if depth == 1:
                        direct_counts[callee] += 1
                    reachable.add(callee)
            frontier = next_frontier

    # Upgrade per-file entries with project-level reachability
    for entry in entries:
        sid = entry.symbol_id
        direct = direct_counts.get(sid, 0)
        if sid in reachable and entry.status == "none":
            entry.status = "covered"
            entry.test_count = direct
        elif direct > entry.test_count:
            entry.test_count = direct

    # Tally summary from final statuses
    covered = sum(1 for e in entries if e.status == "covered")
    partial = sum(1 for e in entries if e.status == "partial")
    none_count = sum(1 for e in entries if e.status == "none")

    entries.sort(key=lambda e: (e.file, e.symbol_id))

    return ProjectCoverageComponent(
        root=root,
        symbols_total=covered + partial + none_count,
        covered=covered,
        partial=partial,
        none_count=none_count,
        entries=entries,
    )


# Common builtin/stdlib names that should never be resolved cross-file.
# These cause false positives when a project symbol happens to share the name.
_BUILTIN_NAMES: set[str] = {
    # Python builtins
    "print", "len", "range", "str", "int", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "open", "close", "read", "write", "append",
    "extend", "pop", "get", "keys", "values", "items", "update", "format",
    "join", "split", "strip", "replace", "startswith", "endswith", "encode",
    "decode", "sort", "sorted", "reversed", "enumerate", "zip", "map", "filter",
    "any", "all", "min", "max", "sum", "abs", "round", "iter", "next", "hash",
    "id", "repr", "input", "exit", "vars", "dir", "help", "property",
    "staticmethod", "classmethod", "callable",
    # Common stdlib / IO
    "run", "call", "dump", "dumps", "load", "loads", "copy", "move", "remove",
    "exists", "mkdir", "makedirs", "walk", "glob", "match", "search", "find",
    "compile", "execute", "connect", "send", "recv", "accept", "listen", "bind",
    "sleep", "time", "now",
    # C/C++/Rust builtins
    "main", "malloc", "free", "sizeof", "printf", "fprintf", "scanf",
    "memcpy", "memset", "strlen", "strcmp", "strcpy",
    "push", "push_back", "emplace_back", "begin", "end", "size", "empty",
    "insert", "erase", "clear", "front", "back", "top", "data",
    "lock", "unlock", "try_lock", "clone", "drop", "into", "from",
    "unwrap", "expect", "ok", "err", "some", "none",
    # Rust std trait methods & combinators (too common to resolve cross-file)
    "unwrap_or_else", "unwrap_or", "unwrap_or_default",
    "and_then", "or_else", "ok_or_else", "ok_or",
    "map_err", "map_or", "map_or_else",
    "is_some", "is_none", "is_ok", "is_err",
    "as_ref", "as_mut", "as_slice", "as_ptr",
    "into_iter", "iter", "iter_mut",
    "to_string", "to_owned", "to_vec",
    "fmt", "eq", "ne", "cmp", "partial_cmp",
    "hash", "default", "deref", "deref_mut",
    "index", "index_mut",
    "try_from", "try_into", "from_str", "parse",
    "new", "build", "with_capacity",
    # JavaScript/TypeScript builtins & common Node.js/browser APIs
    "constructor", "prototype", "toString", "valueOf", "hasOwnProperty",
    "addEventListener", "removeEventListener", "querySelector",
    "getElementById", "createElement", "appendChild",
    "then", "catch", "finally", "resolve", "reject",
    "require", "exports",
    # Node.js fs/path/console (commonly re-defined in mocks)
    "readFileSync", "writeFileSync", "readFile", "writeFile",
    "existsSync", "mkdirSync", "readdirSync", "statSync", "unlinkSync",
    "warn", "info", "error", "debug", "log", "trace",
    "finish", "destroy", "pipe", "emit", "on", "once", "off",
    "render", "mount", "unmount", "dispose",
    # Go builtins
    "make", "cap", "panic", "recover", "string", "byte", "rune",
    "Println", "Printf", "Sprintf", "Fprintf", "Errorf",
    "Error", "String", "Close", "Read", "Write",
    "Len", "Cap", "Append", "Copy", "Delete",
}


def _build_global_name_index(
    file_results: dict[str, dict], root: str,
) -> dict[str, list[tuple[str, str]]]:
    """Index all symbols by name and qualified_name.

    Returns name → [(symbol_id, rel_path)] mapping.
    """
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source_file, result in file_results.items():
        rel_path = rel_posix(source_file, root)
        for sym in result["symbols"]:
            sid = str(sym.id)
            entry = (sid, rel_path)
            index[sym.raw.name].append(entry)
            if sym.raw.qualified_name != sym.raw.name:
                index[sym.raw.qualified_name].append(entry)
    return dict(index)


def _build_import_graph(
    file_results: dict[str, dict], root: str,
) -> dict[str, dict[str, set[str]]]:
    """Parse DEPENDENCY symbols to extract import info per file.

    Returns rel_path → {imported_name → set of source module parts}.
    This allows disambiguation: if caller imports 'bar' from 'mod_b',
    we can prefer candidates from files matching 'mod_b'.
    """
    graph: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for source_file, result in file_results.items():
        rel_path = rel_posix(source_file, root)
        for sym in result["symbols"]:
            if sym.raw.kind.value == "d":
                name = sym.raw.name
                if " import " in name:
                    # "from aleph.project.builder import build_project"
                    from_part = name.split(" import ")[0]  # "from aleph.project.builder"
                    module = from_part.replace("from ", "").strip()
                    imported = name.split(" import ", 1)[1]
                    for part in imported.split(","):
                        part = part.strip()
                        if " as " in part:
                            part = part.split(" as ")[0].strip()
                        if part:
                            # Store the module parts so we can match against file paths
                            for mod_part in module.split("."):
                                graph[rel_path][part].add(mod_part)
                elif name.startswith("import "):
                    mod = name.split("import ", 1)[1].strip()
                    if " as " in mod:
                        mod = mod.split(" as ")[0].strip()
                    last = mod.split(".")[-1]
                    for mod_part in mod.split("."):
                        graph[rel_path][last].add(mod_part)
    return dict(graph)


def _resolve_cross_file_calls(
    file_results: dict[str, dict],
    root: str,
    name_index: dict[str, list[tuple[str, str]]],
    import_graph: dict[str, dict[str, set[str]]],
    symbol_to_file: dict[str, str],
    symbol_to_name: dict[str, str] | None = None,
) -> tuple[list[ProjectCrossRef], dict[tuple[str, str], int]]:
    """Resolve unresolved calls against the global name index.

    For each file's call_edge_metadata entries with status=="unresolved",
    look up callee_name in the global name index and create cross-refs.

    Returns (new_cross_refs, file_dep_counts).
    """
    cross_refs: list[ProjectCrossRef] = []
    dep_counts: dict[tuple[str, str], int] = defaultdict(int)
    seen: set[tuple[str, str]] = set()

    for source_file, result in file_results.items():
        rel_path = rel_posix(source_file, root)
        struct = result["struct_component"]
        metadata = getattr(struct, "call_edge_metadata", [])

        for edge in metadata:
            if edge.get("status") != "unresolved":
                continue

            callee_name = edge.get("callee_name", "")
            caller_id = edge.get("caller_id", "")
            if not callee_name or not caller_id:
                continue

            if callee_name in _BUILTIN_NAMES:
                continue

            candidates = name_index.get(callee_name, [])
            cross_file = [(sid, fpath) for sid, fpath in candidates if fpath != rel_path]
            if not cross_file:
                continue

            if len(cross_file) == 1:
                resolved_id, target_file = cross_file[0]
                # Don't resolve src→vendor when it's the only candidate —
                # likely a name collision, not a real dependency
                if not _is_vendor_file(rel_path) and _is_vendor_file(target_file):
                    continue
            else:
                # Disambiguate using import graph
                file_imports = import_graph.get(rel_path, {})
                module_parts = file_imports.get(callee_name, set())
                if module_parts:
                    imported_candidates = [
                        (sid, fpath) for sid, fpath in cross_file
                        if _file_matches_module(fpath, module_parts)
                    ]
                    if len(imported_candidates) == 1:
                        resolved_id, target_file = imported_candidates[0]
                    else:
                        continue
                else:
                    continue

            pair = (caller_id, resolved_id)
            if pair in seen:
                continue
            seen.add(pair)

            names = symbol_to_name or {}
            cross_refs.append(ProjectCrossRef(
                caller_id=caller_id,
                callee_id=resolved_id,
                source_file=rel_path,
                target_file=target_file,
                caller_name=names.get(caller_id, ""),
                callee_name=names.get(resolved_id, ""),
            ))
            dep_counts[(rel_path, target_file)] += 1

    return cross_refs, dict(dep_counts)


def _file_matches_module(file_path: str, module_parts: set[str]) -> bool:
    """Check if any component of file_path matches the import's module parts."""
    base = os.path.splitext(os.path.basename(file_path))[0]
    if base in module_parts:
        return True
    parts = file_path.replace(os.sep, "/").split("/")
    for part in parts:
        if part.replace(".py", "") in module_parts:
            return True
    return False
