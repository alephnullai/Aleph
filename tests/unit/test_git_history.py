"""Tests for git history subprocess wrapper."""

from unittest.mock import patch, MagicMock
from datetime import datetime

from aleph.temporal.git_history import GitHistory


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
    # git log returns newest first; file_log reverses to oldest first
    log_output = (
        "def456|1700086400|Second commit\n"
        "abc123|1700000000|First commit\n"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
        gh = GitHistory(repo_root="/repo")
        commits = gh.file_log("test.py")
        assert len(commits) == 2
        assert commits[0].summary == "First commit"  # oldest first
        assert commits[1].summary == "Second commit"


def test_file_log_fails_gracefully():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
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


def test_is_not_available():
    # Must prevent __init__ from auto-detecting
    with patch.object(GitHistory, "_detect_root", return_value=None):
        gh2 = GitHistory(repo_root=None)
        assert not gh2.is_available()
