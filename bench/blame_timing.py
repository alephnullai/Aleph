"""Measure git-blame's share of a full aleph build (P2 perf-track item a).

Builds a deterministic fixture repo (~100 Python files, ~300 commits),
then times ``auto_build`` twice on identical copies:

  1. as-is (per-file ``git blame`` in the temporal layer), and
  2. with ``GitHistory.blame`` stubbed out (returns ``{}``),

and reports total build time for each, the blame wall-clock time and
call count (measured by wrapping the real blame), and blame's share of
the full build.

This is intentionally NOT a unit test — it shells out to git a few
hundred times and its wall-clock numbers are machine-dependent. Run it
manually:

    PYTHONPATH=src .venv/bin/python bench/blame_timing.py

Options:
    --files N    number of fixture files   (default 100)
    --commits N  number of fixture commits (default 300)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aleph.pipeline import auto_build  # noqa: E402
from aleph.temporal.git_history import GitHistory  # noqa: E402
from aleph.util.progress import ProgressReporter  # noqa: E402

EPOCH = 1735689600  # 2025-01-01T00:00:00Z — fixed for determinism


def _git_env(home: str, when: int) -> dict[str, str]:
    stamp = f"{when} +0000"
    return {
        "GIT_AUTHOR_NAME": "Bench", "GIT_AUTHOR_EMAIL": "b@example.com",
        "GIT_COMMITTER_NAME": "Bench", "GIT_COMMITTER_EMAIL": "b@example.com",
        "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
        "PATH": os.environ["PATH"],
        "HOME": home,  # isolate from user git config
    }


def _file_body(index: int, revision: int) -> str:
    """~60 deterministic lines of valid Python per file."""
    lines = [f'"""Fixture module {index} (rev {revision})."""', ""]
    for fn in range(10):
        lines.append(f"def func_{index}_{fn}(x):")
        lines.append(f'    """Function {fn} of module {index}."""')
        lines.append(f"    y = x + {fn} + {revision if fn == revision % 10 else 0}")
        lines.append(f"    return y * {fn + 1}")
        lines.append("")
    return "\n".join(lines) + "\n"


def make_fixture(root: str, home: str, n_files: int, n_commits: int) -> None:
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
            f.write(_file_body(i, 0))
    git(["add", "-A"], EPOCH)
    git(["commit", "-q", "-m", "seed"], EPOCH)

    for c in range(1, n_commits):
        when = EPOCH + c * 3600
        for k in range(3):
            i = (c * 3 + k) % n_files
            with open(os.path.join(root, f"mod_{i:03d}.py"), "w") as f:
                f.write(_file_body(i, c))
        git(["add", "-A"], when)
        git(["commit", "-q", "-m", f"commit {c}"], when)


def timed_build(root: str) -> float:
    start = time.perf_counter()
    auto_build(root, full=True, text_artifacts=False,
               progress=ProgressReporter(quiet=True))
    return time.perf_counter() - start


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=100)
    ap.add_argument("--commits", type=int, default=300)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="aleph_blame_bench_") as tmp:
        repo_a = os.path.join(tmp, "repo_blame_on")
        repo_b = os.path.join(tmp, "repo_blame_off")
        print(f"building fixtures ({args.files} files, {args.commits} commits)...")
        make_fixture(repo_a, tmp, args.files, args.commits)
        make_fixture(repo_b, tmp, args.files, args.commits)

        # Warmup: initialize tree-sitter parsers etc. so run A doesn't
        # pay one-time costs that run B then skips.
        warm = os.path.join(tmp, "warmup")
        make_fixture(warm, tmp, 2, 2)
        timed_build(warm)

        # Run A: blame as-is, wrapped to accumulate wall time + calls.
        real_blame = GitHistory.blame
        stats = {"calls": 0, "secs": 0.0}

        def counting_blame(self, source_file):
            t0 = time.perf_counter()
            try:
                return real_blame(self, source_file)
            finally:
                stats["calls"] += 1
                stats["secs"] += time.perf_counter() - t0

        GitHistory.blame = counting_blame
        try:
            total_a = timed_build(repo_a)
        finally:
            GitHistory.blame = real_blame

        # Run B: blame stubbed out entirely.
        GitHistory.blame = lambda self, source_file: {}
        try:
            total_b = timed_build(repo_b)
        finally:
            GitHistory.blame = real_blame

        delta = total_a - total_b
        share = 100.0 * delta / total_a if total_a else 0.0
        wrapped_share = 100.0 * stats["secs"] / total_a if total_a else 0.0
        print(f"build with blame   : {total_a:8.2f}s")
        print(f"build blame stubbed: {total_b:8.2f}s")
        print(f"delta (blame cost) : {delta:8.2f}s  ({share:.1f}% of build)")
        print(f"blame wall (wrap)  : {stats['secs']:8.2f}s over "
              f"{stats['calls']} calls ({wrapped_share:.1f}% of build)")


if __name__ == "__main__":
    main()
