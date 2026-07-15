"""Tests for parallel parsing (P2-b): determinism, the ALEPH_JOBS safety
valve, failure isolation, broken-pool degradation, and accumulator merging."""

from __future__ import annotations

import concurrent.futures
import io
import os
import shutil
import sqlite3
import subprocess

import pytest

from aleph.ingest.node_types import (
    _UNKNOWN_MAX_PER_LANG,
    clear_unknown_node_types,
    get_unknown_node_types,
    merge_unknown_node_types,
)
from aleph.pipeline import auto_build, run_pipeline
from aleph.project import builder as builder_module
from aleph.project.builder import build_project
from aleph.project.parallel import (
    MIN_AUTO_PARALLEL_FILES, ParallelBuildContext, resolve_jobs,
)
from aleph.temporal.git_history import GitHistory
from aleph.util.progress import ProgressReporter


# ── fixtures ──


def _write_fixture_tree(root: str, count: int = 10) -> None:
    """A small project with cross-file imports/calls so cross-refs,
    salience, and coverage all have real inputs."""
    for i in range(count):
        nxt = (i + 1) % count
        path = os.path.join(root, f"mod_{i}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"from mod_{nxt} import helper_{nxt}\n\n")
            f.write(f"def helper_{i}(x):\n    return x + {i}\n\n")
            f.write(f"def caller_{i}(x):\n    return helper_{nxt}(x)\n\n")
            f.write(
                f"class Thing{i}:\n"
                f"    def run(self):\n        return helper_{i}(1)\n\n"
            )
            f.write(f"def test_helper_{i}():\n    assert helper_{i}(0) == {i}\n")


def _git_commit_all(root: str) -> bool:
    """Init a repo and commit the tree; False when git is unavailable."""
    try:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "init"],
            cwd=root, check=True, capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _read_artifacts(root: str) -> dict[str, bytes]:
    aleph_dir = os.path.join(root, ".aleph")
    artifacts: dict[str, bytes] = {}
    for name in sorted(os.listdir(aleph_dir)):
        if name.startswith("project.aleph.") or name == ".aleph.index.json":
            with open(os.path.join(aleph_dir, name), "rb") as f:
                artifacts[name] = f.read()
    return artifacts


def _read_store_rows(root: str) -> dict[str, list]:
    """Store-relevant outputs: symbol set, call edges, salience inputs."""
    db = os.path.join(root, ".aleph", "aleph.db")
    conn = sqlite3.connect(db)
    try:
        symbols = sorted(conn.execute(
            "SELECT id, name, qualified_name, kind, scope, signature,"
            " span_start, span_end, body_text FROM symbols"
        ).fetchall())
        edges = sorted(conn.execute(
            "SELECT caller_id, callee_id, kind FROM call_edges"
        ).fetchall())
        salience = sorted(conn.execute(
            "SELECT symbol_id, score, local_fan_in, cross_file_fan_in,"
            " total_fan_in FROM salience"
        ).fetchall())
    finally:
        conn.close()
    return {"symbols": symbols, "edges": edges, "salience": salience}


def _build(root: str, jobs: int, monkeypatch) -> object:
    aleph_dir = os.path.join(root, ".aleph")
    if os.path.isdir(aleph_dir):
        shutil.rmtree(aleph_dir)
    monkeypatch.setenv("ALEPH_JOBS", str(jobs))
    return auto_build(str(root), progress=ProgressReporter(quiet=True))


# ── resolve_jobs / ALEPH_JOBS ──


class TestResolveJobs:
    def test_explicit_value(self):
        assert resolve_jobs({"ALEPH_JOBS": "4"}) == (4, False)

    def test_one_is_sequential_safety_valve(self):
        assert resolve_jobs({"ALEPH_JOBS": "1"}) == (1, False)

    def test_clamped_to_at_least_one(self):
        assert resolve_jobs({"ALEPH_JOBS": "0"}) == (1, False)
        assert resolve_jobs({"ALEPH_JOBS": "-3"}) == (1, False)

    def test_default_is_auto_min8_cpus_minus_one(self):
        jobs, auto = resolve_jobs({})
        assert auto is True
        cpus = os.cpu_count() or 1
        assert jobs == max(1, min(8, cpus - 1))

    def test_garbage_falls_back_to_default(self):
        jobs, auto = resolve_jobs({"ALEPH_JOBS": "lots"})
        assert auto is True
        assert jobs >= 1


# ── determinism: ALEPH_JOBS=1 vs 4 must be byte-identical ──


class TestDeterminism:
    def test_parallel_build_matches_sequential(self, tmp_path, monkeypatch):
        """Full A/B: same tree built sequentially and with 4 workers.

        Text artifacts (symbol dict, call-edge struct, salience,
        attention, coverage, map, fs, index) must be byte-identical, and
        the store's symbol/call-edge/salience rows must match exactly.
        Excluded from byte comparison: aleph.db (SQLite page layout is
        not deterministic) and the build cache (stamps carry mtimes) —
        their store-relevant CONTENT is compared via _read_store_rows.
        """
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj))
        _git_commit_all(str(proj))

        r_seq = _build(str(proj), 1, monkeypatch)
        seq_artifacts = _read_artifacts(str(proj))
        seq_rows = _read_store_rows(str(proj))

        r_par = _build(str(proj), 4, monkeypatch)
        par_artifacts = _read_artifacts(str(proj))
        par_rows = _read_store_rows(str(proj))

        # Prove the pool actually produced the parallel results: worker
        # payloads are the serialized CachedFileResult subset, which has
        # no bodies_component — a silent degrade-to-sequential would
        # carry the live pipeline objects and fail here.
        assert all(
            not fr.get("bodies_component") for fr in r_par.file_results.values()
        ), "parallel build fell back to sequential"
        assert all(
            fr.get("bodies_component") for fr in r_seq.file_results.values()
        )

        assert r_par.stats.errors == r_seq.stats.errors == []
        assert r_par.stats.rebuilt_files == r_seq.stats.rebuilt_files
        assert r_par.stats.total_symbols == r_seq.stats.total_symbols
        assert r_par.stats.total_call_edges == r_seq.stats.total_call_edges
        assert r_par.stats.total_cross_refs == r_seq.stats.total_cross_refs

        assert set(par_artifacts) == set(seq_artifacts)
        for name in seq_artifacts:
            assert par_artifacts[name] == seq_artifacts[name], (
                f"artifact {name} differs between ALEPH_JOBS=1 and 4"
            )
        assert par_rows == seq_rows

    def test_incremental_rebuild_after_parallel_build(self, tmp_path, monkeypatch):
        """A parallel build's cache must serve a subsequent incremental
        rebuild exactly like a sequential build's cache does."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=6)

        _build(str(proj), 2, monkeypatch)
        monkeypatch.setenv("ALEPH_JOBS", "2")
        second = auto_build(str(proj), progress=ProgressReporter(quiet=True))
        assert second.stats.reused_files == 6
        assert second.stats.rebuilt_files == 0


# ── ALEPH_JOBS=1: the exact sequential path, zero parallel machinery ──


class TestSequentialSafetyValve:
    def test_jobs_1_never_touches_parallel_code(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=4)

        def _boom(*args, **kwargs):  # pragma: no cover - failure mode
            raise AssertionError("parallel path used despite ALEPH_JOBS=1")

        monkeypatch.setattr(builder_module, "_parse_files_parallel", _boom)
        result = _build(str(proj), 1, monkeypatch)
        assert result.stats.rebuilt_files == 4
        assert result.stats.errors == []

    def test_auto_mode_small_build_stays_sequential(self, tmp_path, monkeypatch):
        """Without ALEPH_JOBS, builds below the stale-file cutoff never
        pay worker spawn costs (the test suite relies on this)."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=4)
        assert 4 < MIN_AUTO_PARALLEL_FILES

        def _boom(*args, **kwargs):  # pragma: no cover - failure mode
            raise AssertionError("auto mode engaged pool for a tiny build")

        monkeypatch.setattr(builder_module, "_parse_files_parallel", _boom)
        monkeypatch.delenv("ALEPH_JOBS", raising=False)
        result = auto_build(str(proj), progress=ProgressReporter(quiet=True))
        assert result.stats.rebuilt_files == 4


# ── failure isolation ──


@pytest.mark.skipif(os.name == "nt", reason="chmod 000 is a no-op on Windows")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores file permissions",
)
class TestWorkerFailureIsolation:
    def test_unreadable_file_is_an_error_not_a_crash(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=5)
        bad = proj / "mod_2.py"
        os.chmod(bad, 0o000)
        try:
            result = _build(str(proj), 2, monkeypatch)
        finally:
            os.chmod(bad, 0o644)

        assert len(result.stats.errors) == 1
        assert str(bad) in result.stats.errors[0]
        # The other four files built normally.
        assert result.stats.rebuilt_files == 4
        assert str(bad) not in result.file_results

    def test_error_message_matches_sequential(self, tmp_path, monkeypatch):
        """The error a worker reports must read exactly like the
        sequential loop's per-file error for the same failure."""
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=5)
        bad = proj / "mod_2.py"
        os.chmod(bad, 0o000)
        try:
            r_par = _build(str(proj), 2, monkeypatch)
            r_seq = _build(str(proj), 1, monkeypatch)
        finally:
            os.chmod(bad, 0o644)
        assert r_par.stats.errors == r_seq.stats.errors


# ── broken pool degrades to sequential ──


class _ExplodingPool:
    """ProcessPoolExecutor stand-in whose futures all fail with
    BrokenProcessPool — the shape of a worker hard-crash."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args, **kwargs):
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_exception(
            concurrent.futures.process.BrokenProcessPool("worker died"))
        return future


class TestBrokenPoolDegrade:
    def _context(self, root: str) -> ParallelBuildContext:
        git = GitHistory(repo_root=str(root))
        return ParallelBuildContext(
            jobs=2, project_root=str(root), git_state=git.export_state(),
        )

    def test_degrades_to_sequential_with_warning(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=5)
        monkeypatch.setattr(
            builder_module, "ProcessPoolExecutor", _ExplodingPool)

        stream = io.StringIO()
        progress = ProgressReporter(stream=stream)
        abs_root = str(proj)

        def runner(path: str) -> dict:
            return run_pipeline(path, project_root=abs_root)

        result = build_project(
            abs_root, runner, progress=progress,
            parallel=self._context(abs_root),
        )
        assert result.stats.rebuilt_files == 5
        assert result.stats.errors == []
        assert result.stats.total_symbols > 0
        assert "finishing 5 remaining files sequentially" in stream.getvalue()

    def test_degraded_output_matches_sequential(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=5)
        abs_root = str(proj)

        def runner(path: str) -> dict:
            return run_pipeline(path, project_root=abs_root)

        baseline = build_project(
            abs_root, runner, progress=ProgressReporter(quiet=True))

        monkeypatch.setattr(
            builder_module, "ProcessPoolExecutor", _ExplodingPool)
        degraded = build_project(
            abs_root, runner, progress=ProgressReporter(quiet=True),
            parallel=self._context(abs_root),
        )
        assert degraded.stats.total_symbols == baseline.stats.total_symbols
        assert degraded.stats.total_call_edges == baseline.stats.total_call_edges
        base_ids = sorted(e.symbol_id for e in baseline.dict_component.symbols)
        deg_ids = sorted(e.symbol_id for e in degraded.dict_component.symbols)
        assert deg_ids == base_ids


# ── unknown-node-type accumulator merge ──


class TestUnknownNodeTypeMerge:
    def setup_method(self):
        clear_unknown_node_types()

    def teardown_method(self):
        clear_unknown_node_types()

    def test_merge_unions_languages_and_types(self):
        merge_unknown_node_types({"python": ["weird_node"]})
        merge_unknown_node_types({"python": {"weird_node", "other_node"},
                                  "rust": ["strange_item"]})
        unknown = get_unknown_node_types()
        assert unknown["python"] == {"weird_node", "other_node"}
        assert unknown["rust"] == {"strange_item"}

    def test_merge_is_idempotent(self):
        payload = {"python": ["weird_node"]}
        merge_unknown_node_types(payload)
        merge_unknown_node_types(payload)
        assert get_unknown_node_types()["python"] == {"weird_node"}

    def test_merge_respects_per_language_cap(self):
        merge_unknown_node_types(
            {"python": [f"t{i}" for i in range(_UNKNOWN_MAX_PER_LANG + 50)]})
        assert len(get_unknown_node_types()["python"]) == _UNKNOWN_MAX_PER_LANG


# ── GitHistory state shipping ──


class TestGitHistoryStateShipping:
    def test_round_trip_serves_log_without_subprocess(self, tmp_path, monkeypatch):
        proj = tmp_path / "repo"
        proj.mkdir()
        _write_fixture_tree(str(proj), count=3)
        if not _git_commit_all(str(proj)):
            pytest.skip("git unavailable")

        parent = GitHistory(repo_root=str(proj))
        parent.prewarm(str(proj))
        source = os.path.join(str(proj), "mod_0.py")
        expected_log = parent.file_log(source)
        expected_blame = parent.should_blame(source)
        assert expected_log, "fixture repo should have history"

        state = parent.export_state()
        # State must be plain data (what the initializer pickles).
        import pickle
        pickle.dumps(state)

        # A re-run of the repo log in the worker is the exact hazard
        # this design forbids — make any subprocess attempt explode.
        def _no_subprocess(*args, **kwargs):  # pragma: no cover - failure mode
            raise AssertionError("worker re-ran a git subprocess for the log")

        worker = GitHistory.from_state(state)
        monkeypatch.setattr(subprocess, "Popen", _no_subprocess)
        monkeypatch.setattr(subprocess, "run", _no_subprocess)

        assert worker.file_log(source) == expected_log
        assert worker.should_blame(source) == expected_blame

    def test_prewarm_outside_repo_is_noop(self, tmp_path):
        plain = tmp_path / "norepo"
        plain.mkdir()
        git = GitHistory(repo_root=None)
        git._explicit_root = None
        git.repo_root = None
        git.prewarm(str(plain))  # must not raise
        assert git.export_state()["log_cache"] == {}
