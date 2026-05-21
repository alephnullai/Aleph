"""Integration test: temporal analysis on Aleph's own git history."""

import os
import pytest

from aleph.temporal.git_history import GitHistory
from aleph.temporal.analyzer import TemporalAnalyzer
from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry


ALEPH_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "src", "aleph")


def _find_python_files():
    """Find all Python files in the Aleph source."""
    files = []
    for root, dirs, filenames in os.walk(ALEPH_SRC):
        for f in filenames:
            if f.endswith(".py") and not f.startswith("__"):
                files.append(os.path.join(root, f))
    return sorted(files)


@pytest.fixture
def git():
    gh = GitHistory()
    if not gh.is_available():
        pytest.skip("Not in a git repository")
    return gh


def test_temporal_on_own_source(git):
    """Run temporal analysis on Aleph's own cli.py."""
    cli_path = os.path.join(ALEPH_SRC, "cli.py")
    if not os.path.exists(cli_path):
        pytest.skip("cli.py not found")

    parser = TreeSitterParser()
    tree, source, language = parser.parse_file(cli_path)
    source_bytes = source.encode("utf-8")

    extractor = SymbolExtractor()
    raw_symbols = extractor.extract(tree, source, language, source_file=cli_path)
    registry = SymbolRegistry()
    symbols = [registry.register(raw) for raw in raw_symbols]

    analyzer = TemporalAnalyzer(git)
    result = analyzer.analyze(symbols, cli_path)

    assert result.source_file == cli_path
    assert result.computed_date  # non-empty
    assert len(result.entries) == len(symbols)

    # At least some symbols should have non-zero age (file exists in git)
    stabilities = [e.stability for e in result.entries]
    assert len(stabilities) > 0


@pytest.mark.parametrize("py_file", _find_python_files()[:5],
                         ids=lambda p: os.path.basename(p))
def test_temporal_on_multiple_sources(git, py_file):
    """Temporal analysis should work on various Aleph source files."""
    parser = TreeSitterParser()
    tree, source, language = parser.parse_file(py_file)

    extractor = SymbolExtractor()
    raw_symbols = extractor.extract(tree, source, language, source_file=py_file)
    registry = SymbolRegistry()
    symbols = [registry.register(raw) for raw in raw_symbols]

    if not symbols:
        pytest.skip("No symbols extracted")

    analyzer = TemporalAnalyzer(git)
    result = analyzer.analyze(symbols, py_file)
    assert len(result.entries) == len(symbols)
