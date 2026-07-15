"""Git subprocess wrapper for blame and log data.

Performance notes (P1-C):
  * The repo root is detected from each FILE's path (walk up to .git),
    cached per directory — not from the process cwd, which may be a
    different repo entirely when serving another project.
  * ``git log`` runs ONCE per repo (``--numstat`` over the full history)
    and is parsed into a per-file commit dict reused for every file in
    that repo, replacing one ``git log --follow`` subprocess per file.
  * ``git blame`` stays per-file: its per-line dates are what give
    per-symbol (span-level) age/churn granularity in TemporalAnalyzer,
    which a file-level log cannot provide. Because each blame is one
    subprocess against the full history, it is GATED to the top-N most
    recently modified files per repo (ranked from the already-shared
    numstat log — see :meth:`GitHistory.should_blame`); measured at 87%
    of a full build when run for every file (100-file/300-commit
    fixture, bench/blame_timing.py).
    Non-hot files degrade to file-level temporal data derived from the
    numstat log (all symbols in the file share age/churn — see
    TemporalAnalyzer._from_commit_log).

EVERY git child here MUST get ``stdin=subprocess.DEVNULL``.
An MCP server's own stdin IS the client's JSON-RPC pipe. Without this, a
git child inherits that pipe as its stdin; on Windows it then blocks on it
instead of exiting, so the call burns its full timeout and gets killed. It
was silent because every call site is fail-soft — a timed-out git degrades
to "no temporal data" rather than erroring. Live cost: `git rev-parse` in
_detect_root (timeout=5) made EVERY build inside `aleph serve` take a flat
+5s on Windows — a one-file rebuild went 0.1s -> 5.1s, which blew the 10s
selftest budget on CI. POSIX doesn't block the same way, so it stayed
hidden there. Anything spawned from a serve path needs the same treatment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

# Scaling guards for the batched repo log (overridable per-instance and
# via environment). On huge repos with heavy binary churn an unscoped,
# uncapped `git log --numstat` is effectively unbounded.
_DEFAULT_MAX_COMMITS = 5000          # ALEPH_TEMPORAL_MAX_COMMITS
_DEFAULT_LOG_TIMEOUT_SECS = 120.0    # ALEPH_TEMPORAL_TIMEOUT (seconds)
_PROGRESS_EVERY_COMMITS = 200        # progress callback granularity
# Per-file blame gating: only the N most recently modified files (per
# repo, from the shared numstat log) get a per-line `git blame`.
# 0 disables blame entirely; negative = unlimited (blame every file).
_DEFAULT_BLAME_TOP = 200             # ALEPH_BLAME_TOP
_DEFAULT_BLAME_TIMEOUT_SECS = 30.0   # ALEPH_BLAME_TIMEOUT (seconds)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


@dataclass
class CommitInfo:
    """A commit touching a specific file."""
    sha: str
    author_date: datetime
    summary: str


def repo_relative_posix(source_file: str, root: str, pathmod=os.path) -> str | None:
    """Repo-root-relative POSIX path for *source_file*, or None.

    The batched ``git log --numstat`` output keys files by repo-relative
    POSIX paths; this converts a caller-supplied path to that convention.

    Returns None when the file cannot live inside *root*:
      * different drives on Windows (``os.path.relpath`` raises
        ValueError — e.g. a project on C: while the cwd-detected repo
        root is on D:), or
      * the relative path escapes the root (``..`` components).

    Relative inputs are treated as already repo-relative, matching the
    old behavior of running ``git log -- <path>`` with cwd=repo_root.

    ``pathmod`` exists for tests: pass ``ntpath`` to exercise Windows
    path semantics on any platform.
    """
    if not pathmod.isabs(source_file):
        return source_file.replace(pathmod.sep, "/")
    try:
        rel = pathmod.relpath(source_file, root)
    except ValueError:
        # Windows: paths on different drives have no relative path.
        return None
    rel = rel.replace(pathmod.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    return rel


class GitHistory:
    """Extracts git history for temporal analysis via subprocess."""

    def __init__(
        self,
        repo_root: str | None = None,
        pathspecs: list[str] | None = None,
        max_commits: int | None = None,
        log_timeout: float | None = None,
        blame_top: int | None = None,
        blame_timeout: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        """Args:
            repo_root: Explicit repo root (tests / callers that know better);
                otherwise resolved per file.
            pathspecs: Git pathspecs appended after ``--`` to scope the
                batched ``git log --numstat`` (e.g. ``*.py`` includes and
                ``:(exclude)vendor`` excludes). Empty/None = whole history.
            max_commits: Cap on commits read by the repo log (newest first;
                ``--max-count``). 0 disables the cap. Default from
                ALEPH_TEMPORAL_MAX_COMMITS, else 5000.
            log_timeout: Wall-clock seconds for the repo-log subprocess; on
                expiry the child is killed and a PARTIAL log is kept (never
                blocks the whole build). 0 disables. Default from
                ALEPH_TEMPORAL_TIMEOUT, else 120.
            blame_top: Only the N most recently modified files per repo
                (ranked by last-commit date from the shared numstat log)
                get a per-line ``git blame``; other files degrade to
                file-level temporal data from the log. 0 disables blame
                entirely; negative = blame every file (legacy behavior).
                Default from ALEPH_BLAME_TOP, else 200.
            blame_timeout: Wall-clock seconds for each blame subprocess;
                on expiry the child is killed, a warning is emitted, and
                the file degrades to log-derived temporal data (a blame
                never blocks the build). <= 0 disables. Default from
                ALEPH_BLAME_TIMEOUT, else 30.
            on_progress: Called as ``(commits_read, total_commits)`` while
                streaming the repo log (total from ``git rev-list --count``
                under the same pathspec scoping as the log itself).
            on_warning: Called with a message when history is actually
                truncated by the cap or the timeout. Defaults to a stderr
                print.
        """
        # Explicit root pins all queries to that repo (tests / callers
        # that know better). Otherwise the root is resolved per file.
        self._explicit_root = repo_root
        self.repo_root = repo_root or self._detect_root()
        self.pathspecs = list(pathspecs) if pathspecs else []
        self.max_commits = (
            max_commits if max_commits is not None
            else _env_int("ALEPH_TEMPORAL_MAX_COMMITS", _DEFAULT_MAX_COMMITS)
        )
        self.log_timeout = (
            log_timeout if log_timeout is not None
            else _env_float("ALEPH_TEMPORAL_TIMEOUT", _DEFAULT_LOG_TIMEOUT_SECS)
        )
        self.blame_top = (
            blame_top if blame_top is not None
            else _env_int("ALEPH_BLAME_TOP", _DEFAULT_BLAME_TOP)
        )
        self.blame_timeout = (
            blame_timeout if blame_timeout is not None
            else _env_float("ALEPH_BLAME_TIMEOUT", _DEFAULT_BLAME_TIMEOUT_SECS)
        )
        self.on_progress = on_progress
        self.on_warning = on_warning
        # Instrumentation: blame subprocess count + accumulated wall time
        # (surfaced in the build's final progress summary).
        self.blame_calls = 0
        self.blame_seconds = 0.0
        # Cache: directory -> repo root (or None) for per-file detection
        self._root_cache: dict[str, str | None] = {}
        # Cache: repo root -> set of repo-relative paths hot enough to blame
        self._hot_cache: dict[str, set[str]] = {}
        # Cache: repo root -> {rel_path: [CommitInfo oldest-first]}
        self._log_cache: dict[str, dict[str, list[CommitInfo]]] = {}
        # Cache: repo root -> total commit count
        self._commit_count_cache: dict[str, int] = {}
        # Cache: repo root -> commit count under self.pathspecs scoping
        self._scoped_count_cache: dict[str, int] = {}

    def _warn(self, message: str) -> None:
        if self.on_warning is not None:
            self.on_warning(message)
        else:
            print(f"[aleph] warning: {message}", file=sys.stderr)

    def is_available(self, source_file: str | None = None) -> bool:
        """Check if git history is available (for a specific file if given)."""
        if self.repo_root is not None:
            return True
        if source_file:
            return self._root_for(source_file) is not None
        return False

    # ── Repo root resolution ──

    def _root_for(self, source_file: str) -> str | None:
        """Resolve the repo root for a file by walking up to .git.

        Results are cached per directory so a build over thousands of
        files in one repo does a handful of stat calls, not subprocesses.
        """
        if self._explicit_root:
            return self._explicit_root
        abs_file = os.path.abspath(source_file)
        if not os.path.isfile(abs_file):
            # Nonexistent path: nothing to walk up from — use the
            # cwd-detected root (back-compat with relative-path callers).
            return self.repo_root
        return self._root_for_dir(os.path.dirname(abs_file))

    def _root_for_dir(self, directory: str) -> str | None:
        """Resolve the repo root for a directory (cached walk to .git)."""
        if self._explicit_root:
            return self._explicit_root
        if directory in self._root_cache:
            return self._root_cache[directory]

        walked: list[str] = []
        current = directory
        root: str | None = None
        while True:
            if current in self._root_cache:
                root = self._root_cache[current]
                break
            walked.append(current)
            if os.path.exists(os.path.join(current, ".git")):
                root = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        for d in walked:
            self._root_cache[d] = root
        if root is None and self.repo_root:
            # Fall back to cwd-detected root (back-compat)
            root = self.repo_root
            self._root_cache[directory] = root
        return root

    # ── State shipping (parallel builds: P2-b) ──

    def prewarm(self, path: str) -> None:
        """Run the batched repo log + blame-gate ranking for *path*'s repo.

        Parallel builds call this in the PARENT before exporting state
        to worker processes, so the one-per-repo ``git log --numstat``
        runs exactly once per build (never once per worker). No-op when
        *path* is not inside a git repository.
        """
        abs_path = os.path.abspath(path)
        directory = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
        root = self._root_for_dir(directory)
        if not root:
            return
        self._repo_log(root)
        if self.blame_top > 0:
            self._hot_files(root)

    def export_state(self) -> dict:
        """Plain-data snapshot of config + caches for worker processes.

        Everything in the returned dict is picklable builtins
        (spawn-safe): CommitInfo rows become ``(sha, unix_ts, summary)``
        tuples. Callbacks and instrumentation counters are NOT part of
        the state — workers attach their own. Restore with
        :meth:`from_state`.
        """
        return {
            "explicit_root": self._explicit_root,
            "repo_root": self.repo_root,
            "pathspecs": list(self.pathspecs),
            "max_commits": self.max_commits,
            "log_timeout": self.log_timeout,
            "blame_top": self.blame_top,
            "blame_timeout": self.blame_timeout,
            "root_cache": dict(self._root_cache),
            "hot_cache": {
                root: sorted(hot) for root, hot in self._hot_cache.items()
            },
            "log_cache": {
                root: {
                    path: [
                        (c.sha, c.author_date.timestamp(), c.summary)
                        for c in commits
                    ]
                    for path, commits in per_file.items()
                }
                for root, per_file in self._log_cache.items()
            },
            "commit_count_cache": dict(self._commit_count_cache),
            "scoped_count_cache": dict(self._scoped_count_cache),
        }

    @classmethod
    def from_state(
        cls,
        state: dict,
        on_progress: Callable[[int, int], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
    ) -> GitHistory:
        """Rebuild a GitHistory from :meth:`export_state` output.

        The pre-warmed log/hot/root caches are restored verbatim, so a
        worker process answers ``file_log()`` / ``should_blame()``
        without ever re-running the batched ``git log`` (per-file
        ``git blame`` subprocesses still run locally — shipping the
        pre-ranked gate is the point). Constructor root detection is
        bypassed entirely (no subprocess at worker start).
        """
        git = cls(
            # Placeholder skips _detect_root()'s subprocess; both root
            # fields are overwritten from the state just below.
            repo_root=state["repo_root"] or ".",
            pathspecs=state["pathspecs"],
            max_commits=state["max_commits"],
            log_timeout=state["log_timeout"],
            blame_top=state["blame_top"],
            blame_timeout=state["blame_timeout"],
            on_progress=on_progress,
            on_warning=on_warning,
        )
        git._explicit_root = state["explicit_root"]
        git.repo_root = state["repo_root"]
        git._root_cache = dict(state["root_cache"])
        git._hot_cache = {
            root: set(hot) for root, hot in state["hot_cache"].items()
        }
        git._log_cache = {
            root: {
                path: [
                    CommitInfo(
                        sha=sha,
                        author_date=datetime.fromtimestamp(ts),
                        summary=summary,
                    )
                    for sha, ts, summary in commits
                ]
                for path, commits in per_file.items()
            }
            for root, per_file in state["log_cache"].items()
        }
        git._commit_count_cache = dict(state["commit_count_cache"])
        git._scoped_count_cache = dict(state["scoped_count_cache"])
        return git

    # ── Blame (per-file; provides per-line dates for symbol spans) ──

    def should_blame(self, source_file: str) -> bool:
        """True when *source_file* is hot enough for a per-line blame.

        Blame is one subprocess per file against the full history —
        measured at 87% of a full build when run for every file
        (bench/blame_timing.py). Only the ``blame_top`` most recently
        modified files per repo (ranked by last-commit date from the
        already-shared numstat log; zero extra subprocesses) keep
        per-symbol blame granularity; the rest degrade to file-level
        temporal data derived from that same log.
        """
        if self.blame_top < 0:
            return True
        if self.blame_top == 0:
            return False
        root = self._root_for(source_file)
        if not root:
            return False  # blame would fail anyway
        rel = repo_relative_posix(source_file, root)
        if rel is None:
            return False
        return rel in self._hot_files(root)

    def _hot_files(self, root: str) -> set[str]:
        """The ``blame_top`` most recently modified rel paths in *root*.

        Ranked by last-commit author date from the batched numstat log
        (newest first; path as deterministic tiebreaker). Cached per repo.
        """
        cached = self._hot_cache.get(root)
        if cached is not None:
            return cached
        per_file = self._repo_log(root)
        ranked = sorted(
            per_file.items(),
            key=lambda kv: (
                -(kv[1][-1].author_date.timestamp() if kv[1] else 0.0),
                kv[0],
            ),
        )
        hot = {path for path, _ in ranked[: self.blame_top]}
        self._hot_cache[root] = hot
        return hot

    def blame(self, source_file: str) -> dict[int, datetime]:
        """Line number (0-based) → author date from git blame.

        Bounded by ``blame_timeout`` wall-clock seconds: on expiry the
        child is killed (``subprocess.run`` kills on TimeoutExpired), a
        warning is emitted, and ``{}`` is returned so the caller degrades
        to log-derived temporal data — a blame never blocks the build.
        """
        root = self._root_for(source_file)
        if not root:
            return {}
        start = time.perf_counter()
        self.blame_calls += 1
        try:
            result = subprocess.run(
                ["git", "blame", "--line-porcelain", source_file],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.blame_timeout if self.blame_timeout > 0 else None,
                cwd=root, stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                return {}
        except subprocess.TimeoutExpired:
            self._warn(
                f"temporal: git blame timed out after "
                f"{self.blame_timeout:.0f}s on {source_file} — using "
                f"file-level history (set ALEPH_BLAME_TIMEOUT to raise)"
            )
            return {}
        except FileNotFoundError:
            return {}
        finally:
            self.blame_seconds += time.perf_counter() - start

        line_dates: dict[int, datetime] = {}
        current_line = -1
        current_date: datetime | None = None

        for line in result.stdout.splitlines():
            # Lines starting with a commit hash (40 hex chars) contain line info
            if len(line) >= 40 and line[0] != '\t' and ' ' in line:
                parts = line.split()
                if len(parts) >= 3 and len(parts[0]) == 40:
                    try:
                        current_line = int(parts[2]) - 1  # 0-based
                    except (ValueError, IndexError):
                        pass
            elif line.startswith("author-time "):
                try:
                    ts = int(line.split(" ", 1)[1])
                    current_date = datetime.fromtimestamp(ts)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("\t"):
                # Content line — finalize this entry
                if current_line >= 0 and current_date is not None:
                    line_dates[current_line] = current_date
                current_date = None

        return line_dates

    # ── Batched log (one subprocess per repo) ──

    def _repo_log(self, root: str) -> dict[str, list[CommitInfo]]:
        """Run ``git log --numstat`` once per repo, batched per file.

        The output is STREAM-parsed (never buffered whole), scoped by
        ``self.pathspecs``, capped at ``self.max_commits`` (newest
        first), and bounded by ``self.log_timeout`` wall-clock seconds.
        On cap/timeout the build degrades gracefully: whatever was read
        becomes the (partial) temporal history and a warning is emitted.

        Returns {repo-relative path: [CommitInfo oldest-first]}.
        """
        cached = self._log_cache.get(root)
        if cached is not None:
            return cached

        per_file: dict[str, list[CommitInfo]] = {}

        cmd = ["git", "log", "--numstat", "--no-renames",
               "--format=%x01%H|%at|%s"]
        if self.max_commits > 0:
            cmd.append(f"--max-count={self.max_commits}")
        if self.pathspecs:
            cmd.append("--")
            cmd.extend(self.pathspecs)

        # Cheap denominator for progress (cached; `git rev-list --count`
        # under the SAME pathspec scoping as the log, so the estimate
        # describes the scan that actually runs).
        total_commits = self._estimated_total(root) if self.on_progress else 0

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", cwd=root,
            )
        except (FileNotFoundError, OSError):
            self._log_cache[root] = per_file
            return per_file

        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass

        timer: threading.Timer | None = None
        if self.log_timeout > 0:
            timer = threading.Timer(self.log_timeout, _kill)
            timer.daemon = True
            timer.start()

        commit_count = 0
        current: CommitInfo | None = None
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip("\r\n")
                if line.startswith("\x01"):
                    commit_count += 1
                    if (self.on_progress is not None
                            and commit_count % _PROGRESS_EVERY_COMMITS == 0):
                        self.on_progress(commit_count, total_commits)
                    current = None
                    parts = line[1:].split("|", 2)
                    if len(parts) < 3:
                        continue
                    try:
                        current = CommitInfo(
                            sha=parts[0],
                            author_date=datetime.fromtimestamp(int(parts[1])),
                            summary=parts[2],
                        )
                    except (ValueError, IndexError):
                        current = None
                elif line and current is not None:
                    # numstat line: "<added>\t<deleted>\t<path>"
                    parts = line.split("\t", 2)
                    if len(parts) == 3:
                        per_file.setdefault(parts[2], []).append(current)
        finally:
            if timer is not None:
                timer.cancel()
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except OSError:
                pass
            # Authoritative bound on child exit: stdout EOF normally means
            # git is gone, but a child that lingers after EOF (or after the
            # watchdog kill) must not park the build on an unbounded wait.
            try:
                returncode = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                returncode = proc.wait()

        hit_cap = self.max_commits > 0 and commit_count >= self.max_commits
        if timed_out.is_set():
            self._warn(
                f"temporal: git log timed out after {self.log_timeout:.0f}s — "
                f"using partial history ({commit_count} commits read; set "
                f"ALEPH_TEMPORAL_TIMEOUT to raise)"
            )
        elif returncode != 0:
            # Failed log (not a repo, bad object, ...): same as before —
            # no history rather than half-parsed garbage.
            per_file = {}
        elif hit_cap:
            # Reaching the cap doesn't by itself prove truncation: a repo
            # can have exactly max_commits matching commits. Warn only
            # when this repo's scoped history really holds more commits
            # than were read (or the total is unknown). The message names
            # the repo because a build can span nested repos — the
            # progress line's estimate may describe a different repo than
            # the one that got capped.
            estimated = self._estimated_total(root)
            if estimated == 0 or estimated > commit_count:
                scope = f" of ~{estimated}" if estimated else ""
                self._warn(
                    f"temporal: {root}: history capped at {self.max_commits} "
                    f"most recent commits{scope} (set "
                    f"ALEPH_TEMPORAL_MAX_COMMITS to change)"
                )
        if self.on_progress is not None and commit_count:
            self.on_progress(commit_count, total_commits)

        # git log is newest-first; flip to oldest-first per file
        for commits in per_file.values():
            commits.reverse()

        self._log_cache[root] = per_file
        # Only a full, unscoped, uncapped log counts every commit.
        if (root not in self._commit_count_cache and not self.pathspecs
                and not hit_cap and not timed_out.is_set()
                and returncode == 0):
            self._commit_count_cache[root] = commit_count
        return per_file

    def file_log(self, source_file: str) -> list[CommitInfo]:
        """Chronological commits touching a file (oldest first).

        Served from the batched per-repo log — no per-file subprocess.
        """
        root = self._root_for(source_file)
        if not root:
            return []
        rel = repo_relative_posix(source_file, root)
        if rel is None:
            # The file cannot be inside this repo (different drive on
            # Windows, or outside the root) — no history, and no point
            # running the repo log.
            return []
        per_file = self._repo_log(root)
        return list(per_file.get(rel, []))

    def repo_commit_count(self, source_file: str | None = None) -> int:
        """Return total number of commits in the repository."""
        root = self._root_for(source_file) if source_file else self.repo_root
        if not root:
            return 0
        return self._commit_count(root)

    def _estimated_total(self, root: str) -> int:
        """Commit count for *root* under the SAME pathspec scoping as the
        batched log (cached; 0 = unknown).

        An unscoped count can disagree wildly with the scoped scan (e.g.
        binary-churn commits the log never reads), making progress
        denominators and truncation checks lie. With no pathspecs this
        falls back to the plain cached count.
        """
        if not self.pathspecs:
            return self._commit_count(root)
        if root in self._scoped_count_cache:
            return self._scoped_count_cache[root]
        count = 0
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD", "--", *self.pathspecs],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                cwd=root, stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                count = int(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError,
                OSError):
            count = 0
        self._scoped_count_cache[root] = count
        return count

    def _commit_count(self, root: str) -> int:
        """Total commit count for *root* via ``git rev-list --count`` (cached)."""
        if root in self._commit_count_cache:
            return self._commit_count_cache[root]
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                cwd=root, stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                count = int(result.stdout.strip())
                self._commit_count_cache[root] = count
                return count
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        return 0

    @staticmethod
    def _detect_root() -> str | None:
        """Auto-detect git repo root from the process cwd (fallback)."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None
