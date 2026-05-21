"""Git subprocess wrapper for blame and log data."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CommitInfo:
    """A commit touching a specific file."""
    sha: str
    author_date: datetime
    summary: str


class GitHistory:
    """Extracts git history for temporal analysis via subprocess."""

    def __init__(self, repo_root: str | None = None) -> None:
        self.repo_root = repo_root or self._detect_root()

    def is_available(self) -> bool:
        """Check if we're in a git repo."""
        return self.repo_root is not None

    def blame(self, source_file: str) -> dict[int, datetime]:
        """Line number (0-based) → author date from git blame."""
        if not self.repo_root:
            return {}
        try:
            result = subprocess.run(
                ["git", "blame", "--line-porcelain", source_file],
                capture_output=True, text=True, timeout=30,
                cwd=self.repo_root,
            )
            if result.returncode != 0:
                return {}
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {}

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

    def file_log(self, source_file: str) -> list[CommitInfo]:
        """Chronological commits touching a file (oldest first)."""
        if not self.repo_root:
            return []
        try:
            result = subprocess.run(
                ["git", "log", "--format=%H|%at|%s", "--follow", "--", source_file],
                capture_output=True, text=True, timeout=30,
                cwd=self.repo_root,
            )
            if result.returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        commits: list[CommitInfo] = []
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                sha = parts[0]
                author_date = datetime.fromtimestamp(int(parts[1]))
                summary = parts[2]
                commits.append(CommitInfo(sha=sha, author_date=author_date, summary=summary))
            except (ValueError, IndexError):
                continue

        commits.reverse()  # oldest first
        return commits

    def repo_commit_count(self) -> int:
        """Return total number of commits in the repository."""
        if not self.repo_root:
            return 0
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=self.repo_root,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        return 0

    @staticmethod
    def _detect_root() -> str | None:
        """Auto-detect git repo root."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None
