"""Tests for temporal analyzer: stability classification."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind, StabilityClass
from aleph.temporal.analyzer import TemporalAnalyzer
from aleph.temporal.git_history import GitHistory


def _make_symbol(name, start_line=0, end_line=5, sid="f_abc123"):
    raw = RawSymbol(
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        scope="",
        span=Span(start_line, 0, end_line, 0),
        language="python",
        source_file="test.py",
    )
    return Symbol(id=SymbolID.from_string(sid), raw=raw)


def test_classify_stable():
    assert TemporalAnalyzer._classify(0) == StabilityClass.STABLE


def test_classify_active():
    assert TemporalAnalyzer._classify(1) == StabilityClass.ACTIVE
    assert TemporalAnalyzer._classify(2) == StabilityClass.ACTIVE


def test_classify_volatile():
    assert TemporalAnalyzer._classify(3) == StabilityClass.VOLATILE
    assert TemporalAnalyzer._classify(10) == StabilityClass.VOLATILE


def test_fallback_when_no_git():
    from unittest.mock import patch
    with patch.object(GitHistory, "_detect_root", return_value=None):
        sym = _make_symbol("func")
        analyzer = TemporalAnalyzer(git_history=GitHistory(repo_root=None))
        result = analyzer.analyze([sym], "test.py")
        assert len(result.entries) == 1
        assert result.entries[0].stability == "active"
        assert sym.stability == "active"


def test_analyze_with_mock_git():
    now = datetime(2025, 1, 15)
    old_date = now - timedelta(days=100)
    recent_date = now - timedelta(days=5)

    sym1 = _make_symbol("old_func", 0, 5, "f_aaa111")
    sym2 = _make_symbol("new_func", 10, 15, "f_bbb222")

    git = MagicMock(spec=GitHistory)
    git.is_available.return_value = True
    git.blame.return_value = {
        # old_func lines: all old dates
        0: old_date, 1: old_date, 2: old_date, 3: old_date, 4: old_date, 5: old_date,
        # new_func lines: recent dates (3 unique days → volatile)
        10: recent_date,
        11: recent_date - timedelta(days=1),
        12: recent_date - timedelta(days=2),
        13: recent_date,
        14: recent_date,
        15: recent_date,
    }
    git.file_log.return_value = []

    analyzer = TemporalAnalyzer(git_history=git)
    result = analyzer.analyze([sym1, sym2], "test.py", reference_date=now)

    assert len(result.entries) == 2
    # sym1: old, no recent changes → stable
    assert result.entries[0].stability == "stable"
    assert result.entries[0].age_days == 100
    # sym2: recent, 3 unique recent dates → volatile
    assert result.entries[1].stability == "volatile"
    assert result.entries[1].churn_count == 3


def test_temporal_component_structure():
    sym = _make_symbol("func")
    analyzer = TemporalAnalyzer(git_history=GitHistory(repo_root=None))
    result = analyzer.analyze([sym], "test.py")

    assert result.source_file == "test.py"
    assert result.computed_date  # non-empty ISO date
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert str(entry.symbol_id) == "f_abc123"
    assert entry.age_days >= 0
    assert entry.last_modified_days >= 0
    assert entry.churn_count >= 0


def test_symbol_fields_populated():
    from unittest.mock import patch
    sym = _make_symbol("func")
    assert sym.stability is None
    assert sym.churn is None

    with patch.object(GitHistory, "_detect_root", return_value=None):
        analyzer = TemporalAnalyzer(git_history=GitHistory(repo_root=None))
        analyzer.analyze([sym], "test.py")

    assert sym.stability == "active"


def test_insufficient_history_single_commit():
    """With < 3 commits and uniform blame dates → stability='stable', not 'active'."""
    now = datetime(2025, 1, 15)
    single_date = now - timedelta(days=1)

    sym = _make_symbol("func", 0, 5, "f_aaa111")

    git = MagicMock(spec=GitHistory)
    git.is_available.return_value = True
    git.blame.return_value = {
        0: single_date, 1: single_date, 2: single_date,
        3: single_date, 4: single_date, 5: single_date,
    }
    git.file_log.return_value = []
    git.repo_commit_count.return_value = 1

    analyzer = TemporalAnalyzer(git_history=git)
    result = analyzer.analyze([sym], "test.py", reference_date=now)

    assert len(result.entries) == 1
    assert result.entries[0].stability == "stable"
    assert result.entries[0].churn_count == 0
    assert sym.stability == "stable"


def test_uniform_blame_with_many_commits_uses_file_log():
    """Repo has 5+ commits but same blame date → file commit count differentiates."""
    now = datetime(2025, 1, 15)
    single_date = now - timedelta(days=10)

    sym = _make_symbol("func", 0, 5, "f_bbb222")

    git = MagicMock(spec=GitHistory)
    git.is_available.return_value = True
    git.blame.return_value = {
        0: single_date, 1: single_date, 2: single_date,
        3: single_date, 4: single_date, 5: single_date,
    }
    # 5 commits touching this file → should classify as volatile (churn >= 3)
    from aleph.temporal.git_history import CommitInfo
    git.file_log.return_value = [
        CommitInfo(sha=f"abc{i}", author_date=single_date, summary=f"commit {i}")
        for i in range(5)
    ]
    git.repo_commit_count.return_value = 10

    analyzer = TemporalAnalyzer(git_history=git)
    result = analyzer.analyze([sym], "test.py", reference_date=now)

    assert len(result.entries) == 1
    # 5 file commits → volatile
    assert result.entries[0].stability == "volatile"
    assert result.entries[0].churn_count == 5


def test_normal_multi_date_blame_unchanged():
    """Normal multi-date blame → no behavior change (regression guard)."""
    now = datetime(2025, 1, 15)
    old_date = now - timedelta(days=100)
    recent_date = now - timedelta(days=5)

    sym = _make_symbol("func", 0, 5, "f_ccc333")

    git = MagicMock(spec=GitHistory)
    git.is_available.return_value = True
    git.blame.return_value = {
        0: old_date, 1: old_date, 2: old_date,
        3: recent_date, 4: recent_date, 5: recent_date,
    }
    git.file_log.return_value = []

    analyzer = TemporalAnalyzer(git_history=git)
    result = analyzer.analyze([sym], "test.py", reference_date=now)

    assert len(result.entries) == 1
    assert result.entries[0].age_days == 100
    # 1 unique recent date → active
    assert result.entries[0].stability == "active"
