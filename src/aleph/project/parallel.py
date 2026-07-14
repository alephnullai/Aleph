"""Parallel per-file parsing (P2-b): worker-process plumbing.

``build_project`` fans the per-file parse/extract/compress work out to a
ProcessPoolExecutor when a :class:`ParallelBuildContext` is provided
(see ``aleph.project.builder._parse_files_parallel`` for the parent
side). This module owns everything that crosses the process boundary
and is deliberately import-light at module level — workers import the
heavy pipeline inside the initializer — so it stays spawn-safe (no
lambdas/closures cross the boundary; Windows/macOS use spawn) and free
of import cycles (``pipeline`` imports ``builder`` imports this).

Control surface:
  * ``ALEPH_JOBS=N`` — worker count. ``1`` selects the exact sequential
    code path (the safety valve and the A/B baseline). Unset defaults
    to ``min(8, cpu_count - 1)``, and in that auto mode small builds
    (fewer than :data:`MIN_AUTO_PARALLEL_FILES` stale files) stay
    sequential because worker spawn + interpreter import costs swamp
    the win.

Hazards this design encodes:
  * Workers return the SERIALIZED ``CachedFileResult`` dict — never
    live pipeline objects (tree-sitter trees and registries are neither
    picklable nor cheap to ship).
  * The parent pre-warms GitHistory's batched numstat log and ships the
    plain-dict cache via the pool initializer; workers must never
    re-run the repo log. Per-file ``git blame`` subprocesses (gated to
    the pre-ranked hot set) DO run in workers — blame parallelizes fine.
  * Unknown-node-type accumulators, GitHistory warnings, and blame
    instrumentation are per-process; every payload carries them back
    for the parent to merge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Auto-mode cutoff: builds with fewer stale (to-be-parsed) files than
# this stay sequential when the job count came from the default. An
# explicit ALEPH_JOBS>1 always engages the pool.
MIN_AUTO_PARALLEL_FILES = 32


def resolve_jobs(env: dict | None = None) -> tuple[int, bool]:
    """Resolve the parallel worker count. Returns ``(jobs, auto)``.

    ``ALEPH_JOBS`` wins when set to a valid integer (clamped to >= 1;
    ``auto`` is False). Otherwise the default is ``min(8, cpu_count - 1)``
    floored at 1, with ``auto`` True so callers apply the small-build
    cutoff. Invalid values fall back to the default.
    """
    environ = os.environ if env is None else env
    raw = str(environ.get("ALEPH_JOBS", "")).strip()
    if raw:
        try:
            return max(1, int(raw)), False
        except ValueError:
            pass
    cpus = os.cpu_count() or 1
    return max(1, min(8, cpus - 1)), True


@dataclass
class ParallelBuildContext:
    """Everything the parent needs to run a parallel parse phase.

    ``git_state`` is ``GitHistory.export_state()`` — plain data,
    pickled once into each worker via the pool initializer.
    ``blame_calls`` / ``blame_seconds`` accumulate the workers'
    instrumentation deltas so ``auto_build`` can fold them back into
    the shared GitHistory for the build summary line.
    """
    jobs: int
    project_root: str
    git_state: dict
    auto: bool = False
    blame_calls: int = 0
    blame_seconds: float = 0.0


def error_payload(source_file: str, error: str) -> dict:
    """Payload shape for a failure raised outside :func:`worker_run_file`
    (e.g. an unpicklable worker result surfacing in the parent)."""
    return {
        "file": source_file, "cached": None, "error": error,
        "warnings": [], "unknown": {}, "blame_calls": 0, "blame_seconds": 0.0,
    }


# ── Worker-process state (one set per worker, filled by worker_init) ──

_WORKER_GIT = None  # GitHistory
_WORKER_ROOT: str | None = None
_WORKER_WARNINGS: list[str] = []
_WORKER_BLAME_SNAPSHOT: tuple[int, float] = (0, 0.0)


def worker_init(project_root: str, git_state: dict) -> None:
    """Pool initializer: construct per-process pipeline singletons once.

    Spawn-safe: module-level function, plain-data args. The GitHistory
    is reconstructed from the parent's pre-warmed export, so the batched
    repo log is never re-run in a worker; blame subprocesses (per-file,
    gate decided by the imported hot set) still happen here.
    """
    global _WORKER_GIT, _WORKER_ROOT, _WORKER_BLAME_SNAPSHOT
    from aleph.pipeline import _pipeline_components
    from aleph.temporal.git_history import GitHistory
    from aleph.ingest.node_types import clear_unknown_node_types

    _WORKER_ROOT = project_root
    _WORKER_GIT = GitHistory.from_state(
        git_state, on_warning=_WORKER_WARNINGS.append,
    )
    _WORKER_BLAME_SNAPSHOT = (0, 0.0)
    clear_unknown_node_types()
    _pipeline_components()  # warm parser/extractor/compressor up front


def worker_run_file(source_file: str) -> dict:
    """Compile one file in a worker; return a picklable payload.

    The payload's ``cached`` member is ``CachedFileResult.to_dict()`` —
    the exact serialized subset the build cache persists (trees and
    live Symbol objects never cross the process boundary). A pipeline
    exception is captured as the file's error string, mirroring the
    sequential loop's per-file error handling.

    ``unknown`` is the worker's full unknown-node-type accumulator
    snapshot (idempotent to merge); ``warnings`` are GitHistory
    warnings raised while this file was processed; ``blame_calls`` /
    ``blame_seconds`` are deltas since the previous payload.
    """
    global _WORKER_BLAME_SNAPSHOT
    from aleph.pipeline import run_pipeline
    from aleph.project.cache import cache_from_pipeline_result
    from aleph.ingest.node_types import get_unknown_node_types

    payload: dict = {
        "file": source_file, "cached": None, "error": None,
        "warnings": [], "unknown": {}, "blame_calls": 0, "blame_seconds": 0.0,
    }
    try:
        result = run_pipeline(
            source_file, project_root=_WORKER_ROOT, git_history=_WORKER_GIT,
        )
        payload["cached"] = cache_from_pipeline_result(source_file, result).to_dict()
    except Exception as e:  # noqa: BLE001 — isolation: per-file error, not a crash
        payload["error"] = str(e)

    payload["warnings"] = list(_WORKER_WARNINGS)
    _WORKER_WARNINGS.clear()
    payload["unknown"] = {
        lang: sorted(types)
        for lang, types in get_unknown_node_types().items()
    }
    if _WORKER_GIT is not None:
        last_calls, last_seconds = _WORKER_BLAME_SNAPSHOT
        payload["blame_calls"] = _WORKER_GIT.blame_calls - last_calls
        payload["blame_seconds"] = _WORKER_GIT.blame_seconds - last_seconds
        _WORKER_BLAME_SNAPSHOT = (
            _WORKER_GIT.blame_calls, _WORKER_GIT.blame_seconds,
        )
    return payload
