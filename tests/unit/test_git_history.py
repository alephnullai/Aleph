"""Tests for git history subprocess wrapper."""

import io
import ntpath
import posixpath
import sys

from unittest.mock import patch, MagicMock
from datetime import datetime

from aleph.temporal.git_history import GitHistory, repo_relative_posix


def _fake_proc(stdout_text: str, returncode: int = 0) -> MagicMock:
    """Fake subprocess.Popen result whose stdout is stream-iterable."""
    proc = MagicMock()
    proc.stdout = io.StringIO(stdout_text)
    proc.wait.return_value = returncode
    proc.returncode = returncode
    return proc


class TestRepoRelativePosix:
    """Pure-function coverage with explicit Windows-style inputs.

    Reproduces the windows-latest CI condition: the checkout (and thus
    the cwd-detected git root) lives on D: while pytest tmp projects
    live on C: — os.path.relpath across drives raises ValueError there.
    """

    def test_windows_cross_drive_returns_none(self):
        assert repo_relative_posix(
            "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\proj\\module.py",
            "D:/a/Aleph/Aleph",
            pathmod=ntpath,
        ) is None

    def test_windows_same_drive_returns_posix_rel(self):
        assert repo_relative_posix(
            "D:\\a\\Aleph\\Aleph\\src\\aleph\\cli.py",
            "D:/a/Aleph/Aleph",
            pathmod=ntpath,
        ) == "src/aleph/cli.py"

    def test_windows_relative_input_is_repo_relative(self):
        assert repo_relative_posix(
            "src\\aleph\\cli.py", "D:/a/Aleph/Aleph", pathmod=ntpath,
        ) == "src/aleph/cli.py"

    def test_windows_path_escaping_root_returns_none(self):
        assert repo_relative_posix(
            "D:\\other\\file.py", "D:\\a\\Aleph", pathmod=ntpath,
        ) is None

    def test_posix_inside_root(self):
        assert repo_relative_posix(
            "/repo/src/m.py", "/repo", pathmod=posixpath,
        ) == "src/m.py"

    def test_posix_outside_root_returns_none(self):
        assert repo_relative_posix(
            "/tmp/elsewhere/m.py", "/repo", pathmod=posixpath,
        ) is None

    def test_posix_relative_passthrough(self):
        assert repo_relative_posix(
            "src/m.py", "/repo", pathmod=posixpath,
        ) == "src/m.py"


def test_file_log_file_outside_repo_returns_empty(tmp_path):
    """A source file that cannot be inside the repo root must yield no
    history (and must not raise — the windows-latest failure mode)."""
    f = tmp_path / "module.py"
    f.write_text("x = 1\n")
    gh = GitHistory(repo_root="D:/a/Aleph/Aleph")
    assert gh.file_log(str(f)) == []


def test_detect_root_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="/repo\n")
        root = GitHistory._detect_root()
        assert root == "/repo"


def test_detect_root_not_git():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        root = GitHistory._detect_root()
        assert root is None


def test_blame_parses_dates():
    blame_output = (
        "abc1234567890123456789012345678901234567 1 1 1\n"
        "author Test User\n"
        "author-mail <test@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "committer Test User\n"
        "committer-mail <test@example.com>\n"
        "committer-time 1700000000\n"
        "committer-tz +0000\n"
        "summary Initial commit\n"
        "filename test.py\n"
        "\tdef hello():\n"
        "abc1234567890123456789012345678901234567 2 2\n"
        "author-time 1700000000\n"
        "\t    return 1\n"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=blame_output)
        gh = GitHistory(repo_root="/repo")
        dates = gh.blame("test.py")
        assert 0 in dates  # line 1 → 0-based
        assert isinstance(dates[0], datetime)


def test_blame_fails_gracefully():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        gh = GitHistory(repo_root="/repo")
        dates = gh.blame("nonexistent.py")
        assert dates == {}


def test_blame_no_repo():
    gh = GitHistory(repo_root=None)
    dates = gh.blame("test.py")
    assert dates == {}


def test_file_log_parses_commits():
    # Batched `git log --numstat` output: newest first; file_log reverses
    # to oldest first per file. The log is stream-parsed from Popen stdout.
    log_output = (
        "\x01def456|1700086400|Second commit\n"
        "\n"
        "1\t1\ttest.py\n"
        "3\t0\tother.py\n"
        "\x01abc123|1700000000|First commit\n"
        "\n"
        "2\t0\ttest.py\n"
    )
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = _fake_proc(log_output)
        gh = GitHistory(repo_root="/repo")
        commits = gh.file_log("test.py")
        assert len(commits) == 2
        assert commits[0].summary == "First commit"  # oldest first
        assert commits[1].summary == "Second commit"
        # The batched log is reused for other files — no second subprocess
        other = gh.file_log("other.py")
        assert len(other) == 1
        assert other[0].summary == "Second commit"
        assert mock_popen.call_count == 1


def test_repo_log_passes_pathspecs_cap_and_streams():
    """Pathspecs land after `--`, the cap lands as --max-count."""
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = _fake_proc("")
        gh = GitHistory(
            repo_root="/repo",
            pathspecs=["*.py", ":(exclude)node_modules"],
            max_commits=123,
        )
        gh.file_log("test.py")
        cmd = mock_popen.call_args[0][0]
        assert "--max-count=123" in cmd
        sep = cmd.index("--")
        assert cmd[sep + 1:] == ["*.py", ":(exclude)node_modules"]
        # Streamed: stdout must be a pipe, not capture_output buffering
        assert mock_popen.call_args[1]["stdout"] is not None


def test_file_log_fails_gracefully():
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = _fake_proc("", returncode=128)
        gh = GitHistory(repo_root="/repo")
        commits = gh.file_log("nonexistent.py")
        assert commits == []


def test_file_log_no_repo():
    gh = GitHistory(repo_root=None)
    commits = gh.file_log("test.py")
    assert commits == []


def test_is_available():
    gh = GitHistory(repo_root="/repo")
    assert gh.is_available()


# ── Real fixture repo (P1-C: batched log produces same fields as before) ──


def _init_fixture_repo(tmp_path):
    import subprocess as sp

    repo = tmp_path / "fixture_repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": __import__("os").environ["PATH"],
        "HOME": str(tmp_path),  # isolate from user git config
    }

    def git(*args):
        sp.run(["git", *args], cwd=str(repo), check=True,
               capture_output=True, env=env)

    git("init", "-q")
    f = repo / "mod.py"
    f.write_text("def a():\n    return 1\n")
    git("add", "mod.py")
    git("commit", "-q", "-m", "first commit")
    f.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
    git("add", "mod.py")
    git("commit", "-q", "-m", "second commit")
    return repo


def test_batched_log_matches_per_file_fields(tmp_path):
    repo = _init_fixture_repo(tmp_path)
    gh = GitHistory(repo_root=str(repo))

    commits = gh.file_log(str(repo / "mod.py"))
    assert len(commits) == 2
    assert commits[0].summary == "first commit"  # oldest first
    assert commits[1].summary == "second commit"
    for c in commits:
        assert len(c.sha) == 40
        assert isinstance(c.author_date, datetime)
    assert commits[0].author_date <= commits[1].author_date

    # Blame still gives per-line dates
    dates = gh.blame(str(repo / "mod.py"))
    assert 0 in dates

    # Commit count served (and cached) from the batched log
    assert gh.repo_commit_count(str(repo / "mod.py")) == 2


def test_root_detected_from_file_path_not_cwd(tmp_path):
    """Repo root comes from the FILE's path, even when cwd detection fails."""
    repo = _init_fixture_repo(tmp_path)
    with patch.object(GitHistory, "_detect_root", return_value=None):
        gh = GitHistory()
        target = str(repo / "mod.py")
        assert gh.is_available(target)
        commits = gh.file_log(target)
        assert len(commits) == 2
        # Per-directory cache populated
        assert gh._root_cache.get(str(repo)) == str(repo)


def test_file_log_unknown_file_in_repo(tmp_path):
    repo = _init_fixture_repo(tmp_path)
    gh = GitHistory(repo_root=str(repo))
    assert gh.file_log(str(repo / "never_committed.py")) == []


def test_is_not_available():
    # Must prevent __init__ from auto-detecting
    with patch.object(GitHistory, "_detect_root", return_value=None):
        gh2 = GitHistory(repo_root=None)
        assert not gh2.is_available()


# ── Temporal scaling: pathspec scoping, commit cap, timeout degrade ──


def _git_env(tmp_path):
    import os
    return {
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),  # isolate from user git config
    }


def _make_repo(tmp_path, name="scaling_repo"):
    """Tiny fixture repo + a `git(...)` helper bound to it."""
    import subprocess as sp

    repo = tmp_path / name
    repo.mkdir()
    env = _git_env(tmp_path)

    def git(*args):
        sp.run(["git", *args], cwd=str(repo), check=True,
               capture_output=True, env=env)

    git("init", "-q")
    return repo, git


def test_pathspec_scoping_excludes_binary_and_vendor(tmp_path):
    """Scoped log only carries indexed-source paths: binary churn and
    vendor dirs never reach the per-file commit dict."""
    repo, git = _make_repo(tmp_path)
    (repo / "mod.py").write_text("def a():\n    return 1\n")
    (repo / "capture.bin").write_bytes(b"\x00\x01\x02" * 100)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    (repo / "capture.bin").write_bytes(b"\x03\x04" * 200)
    (repo / "mod.py").write_text("def a():\n    return 2\n")
    git("add", "-A")
    git("commit", "-q", "-m", "second")

    gh = GitHistory(
        repo_root=str(repo),
        pathspecs=["*.py", ":(exclude)node_modules", ":(exclude)*/node_modules/*"],
    )
    log = gh._repo_log(str(repo))
    assert "mod.py" in log
    assert "capture.bin" not in log
    assert "node_modules/dep.py" not in log
    assert len(gh.file_log(str(repo / "mod.py"))) == 2


def test_commit_cap_truncates_and_warns(tmp_path):
    """The cap keeps the newest N commits and emits one warning."""
    repo, git = _make_repo(tmp_path)
    f = repo / "mod.py"
    for i in range(5):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "mod.py")
        git("commit", "-q", "-m", f"commit {i}")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), max_commits=2,
                    on_warning=warnings.append)
    commits = gh.file_log(str(repo / "mod.py"))
    assert len(commits) == 2
    # Newest two commits survive the cap (oldest-first ordering preserved)
    assert [c.summary for c in commits] == ["commit 3", "commit 4"]
    assert len(warnings) == 1
    assert "capped at 2" in warnings[0]
    assert "ALEPH_TEMPORAL_MAX_COMMITS" in warnings[0]
    # The capped count must NOT poison the repo commit-count cache
    assert gh.repo_commit_count(str(repo / "mod.py")) == 5


def test_no_cap_warning_when_under_cap(tmp_path):
    repo, git = _make_repo(tmp_path)
    (repo / "mod.py").write_text("def a():\n    return 1\n")
    git("add", "mod.py")
    git("commit", "-q", "-m", "only commit")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), max_commits=100,
                    on_warning=warnings.append)
    assert len(gh.file_log(str(repo / "mod.py"))) == 1
    assert warnings == []


def test_cap_above_history_size_never_warns(tmp_path):
    """cap > N commits: nothing truncated, nothing to warn about."""
    repo, git = _make_repo(tmp_path, "cap_above_repo")
    f = repo / "mod.py"
    for i in range(4):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "mod.py")
        git("commit", "-q", "-m", f"commit {i}")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), max_commits=10,
                    on_warning=warnings.append)
    assert len(gh.file_log(str(repo / "mod.py"))) == 4
    assert warnings == []


def test_cap_equal_to_history_size_does_not_warn(tmp_path):
    """Reaching the cap is not truncation when the repo has exactly cap
    commits — the warning used to fire whenever --max-count was filled."""
    repo, git = _make_repo(tmp_path, "exact_cap_repo")
    f = repo / "mod.py"
    for i in range(3):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "mod.py")
        git("commit", "-q", "-m", f"commit {i}")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), max_commits=3,
                    on_warning=warnings.append)
    assert len(gh.file_log(str(repo / "mod.py"))) == 3
    assert warnings == []


def test_cap_warning_names_repo_on_actual_truncation(tmp_path):
    """cap < N commits: one warning, naming the truncated repo (builds can
    span nested repos, so the repo must be attributable)."""
    repo, git = _make_repo(tmp_path, "truncated_repo")
    f = repo / "mod.py"
    for i in range(5):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "mod.py")
        git("commit", "-q", "-m", f"commit {i}")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), max_commits=2,
                    on_warning=warnings.append)
    assert len(gh.file_log(str(repo / "mod.py"))) == 2
    assert len(warnings) == 1
    assert str(repo) in warnings[0]
    assert "capped at 2" in warnings[0]
    assert "~5" in warnings[0]


def test_scoped_estimate_prevents_false_cap_warning(tmp_path):
    """The truncation check and progress denominator use the SAME pathspec
    scoping as the scan: 3 unmatched data commits + 2 .py commits with
    cap=2 fills --max-count exactly but truncates nothing."""
    repo, git = _make_repo(tmp_path, "scoped_repo")
    data = repo / "data.txt"
    for i in range(3):
        data.write_text(f"{i}\n")
        git("add", "-A")
        git("commit", "-q", "-m", f"data {i}")
    f = repo / "mod.py"
    for i in range(2):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "-A")
        git("commit", "-q", "-m", f"py {i}")

    warnings: list[str] = []
    progress: list[tuple[int, int]] = []
    gh = GitHistory(repo_root=str(repo), pathspecs=["*.py"], max_commits=2,
                    on_warning=warnings.append,
                    on_progress=lambda d, t: progress.append((d, t)))
    assert len(gh.file_log(str(repo / "mod.py"))) == 2
    # Unscoped count is 5; the scoped scan saw all 2 matching commits —
    # an unscoped estimate would have claimed truncation here.
    assert warnings == []
    assert progress[-1] == (2, 2)  # denominator scoped like the scan


def test_scoped_truncation_still_warns(tmp_path):
    """When the scoped history really exceeds the cap, the warning fires
    with the scoped total."""
    repo, git = _make_repo(tmp_path, "scoped_trunc_repo")
    f = repo / "mod.py"
    for i in range(4):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "-A")
        git("commit", "-q", "-m", f"py {i}")

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(repo), pathspecs=["*.py"], max_commits=2,
                    on_warning=warnings.append)
    assert len(gh.file_log(str(repo / "mod.py"))) == 2
    assert len(warnings) == 1
    assert "capped at 2" in warnings[0]
    assert "~4" in warnings[0]


def test_timeout_kills_git_and_keeps_partial_history(tmp_path, monkeypatch):
    """A wall-clock timeout kills the subprocess, keeps what was parsed,
    and warns — the build never blocks indefinitely."""
    import subprocess as sp
    real_popen = sp.Popen
    # Child emits one commit then hangs forever (simulates git stuck on
    # binary churn). Only the watchdog timer can end it.
    script = (
        "import sys, time\n"
        "sys.stdout.write('\\x01' + 'a' * 40 + '|1700000000|slow commit\\n')\n"
        "sys.stdout.write('1\\t1\\tmod.py\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )

    def fake_popen(cmd, **kwargs):
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(
        "aleph.temporal.git_history.subprocess.Popen", fake_popen)

    warnings: list[str] = []
    gh = GitHistory(repo_root=str(tmp_path), max_commits=0,
                    log_timeout=0.5, on_warning=warnings.append)
    commits = gh.file_log(str(tmp_path / "mod.py"))
    # Partial history from before the kill is preserved
    assert len(commits) == 1
    assert commits[0].summary == "slow commit"
    assert len(warnings) == 1
    assert "timed out" in warnings[0]
    assert "partial history" in warnings[0]
    # The partial scan must not poison the repo commit-count cache
    assert str(tmp_path) not in gh._commit_count_cache


def test_repo_log_reports_commit_progress(tmp_path):
    repo, git = _make_repo(tmp_path)
    f = repo / "mod.py"
    for i in range(3):
        f.write_text(f"def a():\n    return {i}\n")
        git("add", "mod.py")
        git("commit", "-q", "-m", f"commit {i}")

    calls: list[tuple[int, int]] = []
    gh = GitHistory(repo_root=str(repo),
                    on_progress=lambda done, total: calls.append((done, total)))
    gh.file_log(str(repo / "mod.py"))
    # Final flush always reports the full scoped count and the cheap
    # rev-list total.
    assert calls
    assert calls[-1] == (3, 3)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ALEPH_TEMPORAL_MAX_COMMITS", "42")
    monkeypatch.setenv("ALEPH_TEMPORAL_TIMEOUT", "7.5")
    monkeypatch.setenv("ALEPH_BLAME_TOP", "13")
    monkeypatch.setenv("ALEPH_BLAME_TIMEOUT", "3.5")
    gh = GitHistory(repo_root="/repo")
    assert gh.max_commits == 42
    assert gh.log_timeout == 7.5
    assert gh.blame_top == 13
    assert gh.blame_timeout == 3.5
    # Explicit args beat the environment
    gh2 = GitHistory(repo_root="/repo", max_commits=1, log_timeout=2.0,
                     blame_top=5, blame_timeout=1.0)
    assert gh2.max_commits == 1
    assert gh2.log_timeout == 2.0
    assert gh2.blame_top == 5
    assert gh2.blame_timeout == 1.0


# ── Blame gating (top-N hot files) + blame timeout degrade ──


def test_should_blame_unlimited_and_disabled():
    gh = GitHistory(repo_root="/repo", blame_top=-1)
    assert gh.should_blame("/repo/anything.py")
    gh0 = GitHistory(repo_root="/repo", blame_top=0)
    assert not gh0.should_blame("/repo/anything.py")


def test_should_blame_no_repo_root():
    with patch.object(GitHistory, "_detect_root", return_value=None):
        gh = GitHistory(repo_root=None, blame_top=10)
        assert not gh.should_blame("orphan.py")


def _make_dated_repo(tmp_path, name="blame_top_repo"):
    """Three files committed on distinct, controlled dates (oldest:
    old.py, then mid.py, newest: hot.py)."""
    import subprocess as sp

    repo = tmp_path / name
    repo.mkdir()
    base = 1700000000

    def git(when, *args):
        env = dict(_git_env(tmp_path))
        stamp = f"{base + when} +0000"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        sp.run(["git", *args], cwd=str(repo), check=True,
               capture_output=True, env=env)

    git(0, "init", "-q")
    for offset, fname in ((0, "old.py"), (86400, "mid.py"),
                          (2 * 86400, "hot.py")):
        (repo / fname).write_text(f"# {fname}\ndef f():\n    return 1\n")
        git(offset, "add", fname)
        git(offset, "commit", "-q", "-m", f"add {fname}")
    return repo


def test_should_blame_top_n_by_recency(tmp_path):
    """Only the N most recently modified files (per the shared numstat
    log) get a per-line blame; older files are gated off."""
    repo = _make_dated_repo(tmp_path)
    gh = GitHistory(repo_root=str(repo), blame_top=2)
    assert gh.should_blame(str(repo / "hot.py"))
    assert gh.should_blame(str(repo / "mid.py"))
    assert not gh.should_blame(str(repo / "old.py"))
    # Never-committed files are never hot
    assert not gh.should_blame(str(repo / "uncommitted.py"))


def test_should_blame_top_n_larger_than_repo(tmp_path):
    repo = _make_dated_repo(tmp_path, "blame_top_all_repo")
    gh = GitHistory(repo_root=str(repo), blame_top=100)
    for fname in ("old.py", "mid.py", "hot.py"):
        assert gh.should_blame(str(repo / fname))


def test_blame_timeout_degrades_and_warns():
    """A hung blame is killed at the timeout, warns, and returns {} so
    the analyzer degrades to log-derived data — never blocks the build."""
    import subprocess as sp

    warnings: list[str] = []
    gh = GitHistory(repo_root="/repo", blame_timeout=0.5,
                    on_warning=warnings.append)
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = sp.TimeoutExpired(cmd=["git", "blame"],
                                                 timeout=0.5)
        assert gh.blame("slow.py") == {}
    assert len(warnings) == 1
    assert "blame timed out" in warnings[0]
    assert "ALEPH_BLAME_TIMEOUT" in warnings[0]
    # Instrumentation still accounts for the timed-out call
    assert gh.blame_calls == 1
    assert gh.blame_seconds >= 0.0


def test_blame_instrumentation_counters(tmp_path):
    """blame() accumulates call count + wall seconds for the build summary."""
    repo = _make_dated_repo(tmp_path, "blame_counter_repo")
    gh = GitHistory(repo_root=str(repo))
    assert gh.blame_calls == 0
    dates = gh.blame(str(repo / "hot.py"))
    assert dates  # real blame produced line dates
    assert gh.blame_calls == 1
    assert gh.blame_seconds > 0.0
    gh.blame(str(repo / "old.py"))
    assert gh.blame_calls == 2
