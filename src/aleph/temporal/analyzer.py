"""Per-symbol temporal metadata analysis."""

from __future__ import annotations

from datetime import datetime, timedelta

from aleph.model.symbol import Symbol
from aleph.model.components import TemporalComponent, TemporalEntry
from aleph.model.enums import StabilityClass
from aleph.temporal.git_history import GitHistory


class TemporalAnalyzer:
    """Computes per-symbol age, churn, and stability from git history."""

    CHURN_WINDOW_DAYS = 90

    def __init__(self, git_history: GitHistory | None = None) -> None:
        self.git = git_history or GitHistory()

    def analyze(
        self, symbols: list[Symbol], source_file: str,
        reference_date: datetime | None = None,
    ) -> TemporalComponent:
        """Produce temporal metadata for all symbols in a file."""
        now = reference_date or datetime.now()
        computed_date = now.strftime("%Y-%m-%d")

        if not self.git.is_available(source_file):
            return self._fallback(symbols, source_file, computed_date)

        commits = self.git.file_log(source_file)
        # Per-line blame is gated to the hottest files (GitHistory's
        # top-N by last-commit recency): one subprocess per file against
        # the full history is ~87% of a full build when unconditional
        # (bench/blame_timing.py). Skipped files degrade to file-level
        # data from the already-shared numstat log below.
        if self.git.should_blame(source_file):
            line_dates = self.git.blame(source_file)
        else:
            line_dates = {}
        churn_cutoff = now - timedelta(days=self.CHURN_WINDOW_DAYS)

        # Detect insufficient history: all blame dates identical
        unique_dates = set(line_dates.values()) if line_dates else set()
        if len(unique_dates) <= 1:
            commit_count = self.git.repo_commit_count(source_file)
            if commit_count < 3:
                return self._insufficient_history(symbols, source_file, computed_date)
            if not line_dates and commits:
                # Blame skipped (top-N gate) or failed/timed out: derive
                # file-level temporal data from the numstat log.
                return self._from_commit_log(
                    symbols, source_file, computed_date, now,
                    commits, churn_cutoff,
                )
            elif len(commits) > 0:
                # Use per-file commit count to differentiate churn
                file_commit_count = len(commits)
                return self._from_file_commits(
                    symbols, source_file, computed_date, now,
                    unique_dates, file_commit_count,
                )

        entries: list[TemporalEntry] = []
        for sym in symbols:
            span = sym.raw.span
            # Collect dates for lines in this symbol's span
            sym_dates = [
                line_dates[line]
                for line in range(span.start_line, span.end_line + 1)
                if line in line_dates
            ]

            if sym_dates:
                earliest = min(sym_dates)
                latest = max(sym_dates)
                age_days = max(0, (now - earliest).days)
                last_modified_days = max(0, (now - latest).days)
            else:
                age_days = 0
                last_modified_days = 0

            # Count commits in churn window that touched lines in this symbol's range
            churn_count = self._count_churn(
                commits, churn_cutoff, span.start_line, span.end_line, line_dates
            )

            stability = self._classify(churn_count)

            # Populate symbol fields
            sym.stability = stability.value
            sym.churn = churn_count
            sym.last_modified_days = last_modified_days

            entries.append(TemporalEntry(
                symbol_id=sym.id,
                age_days=age_days,
                last_modified_days=last_modified_days,
                churn_count=churn_count,
                stability=stability.value,
            ))

        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )

    def _count_churn(
        self,
        commits: list,
        churn_cutoff: datetime,
        start_line: int,
        end_line: int,
        line_dates: dict[int, datetime],
    ) -> int:
        """Count modifications in the churn window for a symbol's line range."""
        # Simple approach: count unique dates in the churn window for lines in range
        recent_dates: set[str] = set()
        for line in range(start_line, end_line + 1):
            d = line_dates.get(line)
            if d and d >= churn_cutoff:
                recent_dates.add(d.strftime("%Y-%m-%d"))
        return len(recent_dates)

    @staticmethod
    def _classify(churn_count: int) -> StabilityClass:
        if churn_count >= 3:
            return StabilityClass.VOLATILE
        elif churn_count >= 1:
            return StabilityClass.ACTIVE
        return StabilityClass.STABLE

    def _insufficient_history(
        self, symbols: list[Symbol], source_file: str, computed_date: str,
    ) -> TemporalComponent:
        """Safe defaults when repo has < 3 commits (no meaningful temporal data)."""
        entries = [
            TemporalEntry(
                symbol_id=sym.id,
                age_days=0,
                last_modified_days=0,
                churn_count=0,
                stability=StabilityClass.STABLE.value,
            )
            for sym in symbols
        ]
        for sym in symbols:
            sym.stability = StabilityClass.STABLE.value
            sym.churn = 0
            sym.last_modified_days = 0
        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )

    def _from_commit_log(
        self, symbols: list[Symbol], source_file: str, computed_date: str,
        now: datetime, commits: list, churn_cutoff: datetime,
    ) -> TemporalComponent:
        """File-level temporal data from the shared numstat log (no blame).

        Used when blame is skipped by the top-N hot-file gate or when it
        fails/times out. Degradation: every symbol in the file shares the
        file's age (first commit), recency (last commit) and churn
        (unique commit days inside the churn window — consistent with
        ``_count_churn``); per-symbol-span granularity needs blame.
        """
        first = commits[0].author_date
        last = commits[-1].author_date
        age_days = max(0, (now - first).days)
        last_modified_days = max(0, (now - last).days)
        recent_days = {
            c.author_date.strftime("%Y-%m-%d")
            for c in commits
            if c.author_date >= churn_cutoff
        }
        churn_count = len(recent_days)
        stability = self._classify(churn_count)

        entries = [
            TemporalEntry(
                symbol_id=sym.id,
                age_days=age_days,
                last_modified_days=last_modified_days,
                churn_count=churn_count,
                stability=stability.value,
            )
            for sym in symbols
        ]
        for sym in symbols:
            sym.stability = stability.value
            sym.churn = churn_count
            sym.last_modified_days = last_modified_days
        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )

    def _from_file_commits(
        self, symbols: list[Symbol], source_file: str, computed_date: str,
        now: datetime, unique_dates: set[datetime], file_commit_count: int,
    ) -> TemporalComponent:
        """Use file commit count to differentiate when blame dates are uniform."""
        # With uniform blame, compute age from the single date
        if unique_dates:
            only_date = next(iter(unique_dates))
            age_days = max(0, (now - only_date).days)
            last_modified_days = age_days
        else:
            age_days = 0
            last_modified_days = 0

        # Map file commit count to churn: more commits = higher churn
        churn_count = file_commit_count
        stability = self._classify(churn_count)

        entries = [
            TemporalEntry(
                symbol_id=sym.id,
                age_days=age_days,
                last_modified_days=last_modified_days,
                churn_count=churn_count,
                stability=stability.value,
            )
            for sym in symbols
        ]
        for sym in symbols:
            sym.stability = stability.value
            sym.churn = churn_count
            sym.last_modified_days = last_modified_days
        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )

    def _fallback(
        self, symbols: list[Symbol], source_file: str, computed_date: str
    ) -> TemporalComponent:
        """All symbols get active stability when no git data."""
        entries = [
            TemporalEntry(
                symbol_id=sym.id,
                age_days=0,
                last_modified_days=0,
                churn_count=0,
                stability=StabilityClass.ACTIVE.value,
            )
            for sym in symbols
        ]
        for sym in symbols:
            sym.stability = StabilityClass.ACTIVE.value
        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )
