"""Measure parallel parsing speedup on a first build (P2 perf-track item b).

Builds a deterministic fixture repo (~200 Python files of ~40 functions
each, ~100 commits — same shape as bench/blame_timing.py but with a
configurable per-file size, since per-file parse cost is what the pool
amortizes), then times ``auto_build`` on it with different ALEPH_JOBS
values, removing ``.aleph`` between runs so every run is a cold first
build of the same tree (artifacts embed the project root, so one tree
also makes the determinism diff meaningful). A discarded warmup build
pays the one-time costs (tree-sitter init, page cache) before any
timed run.

As a sanity check the project text artifacts of every parallel run are
diffed byte-for-byte against the sequential run's — the determinism
guarantee the unit suite also enforces.

This is intentionally NOT a unit test — it spawns worker processes,
shells out to git, and its wall-clock numbers are machine-dependent.
Run it manually:

    PYTHONPATH=src .venv/bin/python bench/parallel_timing.py

Options:
    --files N      number of fixture files          (default 200)
    --commits N    number of fixture commits        (default 100)
    --functions N  functions per fixture file       (default 40)
    --jobs A B C   ALEPH_JOBS values to time        (default 1 2 4 8)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blame_timing import EPOCH, _git_env  # noqa: E402
from aleph.pipeline import auto_build  # noqa: E402
from aleph.util.progress import ProgressReporter  # noqa: E402


def _file_body(index: int, revision: int, functions: int) -> str:
    """Deterministic Python module: *functions* functions + one class."""
    lines = [f'"""Fixture module {index} (rev {revision})."""', ""]
    for fn in range(functions):
        bump = revision if fn == revision % max(1, functions) else 0
        lines.append(f"def func_{index}_{fn}(x, y={fn}):")
        lines.append(f'    """Function {fn} of module {index}."""')
        lines.append(f"    total = x + y + {bump}")
        lines.append(f"    if total > {fn * 3}:")
        lines.append(f"        total = func_{index}_{max(0, fn - 1)}(total, {fn})")
        lines.append(f"    return total * {fn + 1}")
        lines.append("")
    lines.append(f"class Fixture{index}:")
    lines.append(f"    def run(self):")
    lines.append(f"        return func_{index}_0(1)")
    lines.append("")
    return "\n".join(lines)


def make_fixture(
    root: str, home: str, n_files: int, n_commits: int, functions: int,
) -> None:
    """Deterministic repo: commit 0 seeds all files; each later commit
    rewrites 3 files round-robin; commit dates advance 1h per commit."""
    os.makedirs(root, exist_ok=True)

    def git(args: list[str], when: int) -> None:
        subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True,
            env=_git_env(home, when),
        )

    git(["init", "-q"], EPOCH)
    for i in range(n_files):
        with open(os.path.join(root, f"mod_{i:03d}.py"), "w") as f:
            f.write(_file_body(i, 0, functions))
    git(["add", "-A"], EPOCH)
    git(["commit", "-q", "-m", "seed"], EPOCH)

    for c in range(1, n_commits):
        when = EPOCH + c * 3600
        for k in range(3):
            i = (c * 3 + k) % n_files
            with open(os.path.join(root, f"mod_{i:03d}.py"), "w") as f:
                f.write(_file_body(i, c, functions))
        git(["add", "-A"], when)
        git(["commit", "-q", "-m", f"commit {c}"], when)


def _read_artifacts(root: str) -> dict[str, bytes]:
    aleph_dir = os.path.join(root, ".aleph")
    artifacts: dict[str, bytes] = {}
    for name in sorted(os.listdir(aleph_dir)):
        if name.startswith("project.aleph.") or name == ".aleph.index.json":
            with open(os.path.join(aleph_dir, name), "rb") as f:
                artifacts[name] = f.read()
    return artifacts


def timed_build(root: str, jobs: int) -> tuple[float, object]:
    aleph_dir = os.path.join(root, ".aleph")
    if os.path.isdir(aleph_dir):
        shutil.rmtree(aleph_dir)
    os.environ["ALEPH_JOBS"] = str(jobs)
    start = time.perf_counter()
    result = auto_build(root, full=True, progress=ProgressReporter(quiet=True))
    return time.perf_counter() - start, result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=200)
    ap.add_argument("--commits", type=int, default=100)
    ap.add_argument("--functions", type=int, default=40)
    ap.add_argument("--jobs", type=int, nargs="+", default=[1, 2, 4, 8])
    args = ap.parse_args()

    jobs_list = list(dict.fromkeys([1] + args.jobs))  # baseline first, deduped

    with tempfile.TemporaryDirectory(prefix="aleph_parallel_bench_") as tmp:
        print(f"building fixture ({args.files} files x {args.functions} "
              f"functions, {args.commits} commits); timing jobs={jobs_list}...")
        repo = os.path.join(tmp, "repo")
        make_fixture(repo, tmp, args.files, args.commits, args.functions)

        # Warmup (discarded): one-time costs — tree-sitter parser init,
        # interpreter imports, page cache — so the sequential baseline
        # isn't unfairly penalized.
        timed_build(repo, 1)

        results: dict[int, float] = {}
        artifacts: dict[int, dict[str, bytes]] = {}
        for jobs in jobs_list:
            secs, build = timed_build(repo, jobs)
            results[jobs] = secs
            artifacts[jobs] = _read_artifacts(repo)
            if build.stats.errors:
                print(f"  jobs={jobs}: BUILD ERRORS: {build.stats.errors[:3]}")
            print(f"  jobs={jobs}: {secs:8.2f}s "
                  f"({build.stats.rebuilt_files} files rebuilt)")

        base = results[jobs_list[0]]
        print()
        print(f"{'ALEPH_JOBS':>10}  {'wall':>8}  {'speedup':>8}  identical")
        for jobs in jobs_list:
            same = artifacts[jobs] == artifacts[jobs_list[0]]
            speedup = base / results[jobs] if results[jobs] else 0.0
            print(f"{jobs:>10}  {results[jobs]:7.2f}s  {speedup:7.2f}x  "
                  f"{'yes' if same else 'NO — ARTIFACTS DIFFER'}")


if __name__ == "__main__":
    main()
