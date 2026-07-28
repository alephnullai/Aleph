"""P1-B: project-level test coverage via resolved cross-file call edges.

A test in tests/test_a.py that calls src/a.py:foo must mark foo as covered
in project.aleph.coverage — the old per-file-only mapping could never do this.
"""

from __future__ import annotations

import os

import pytest

from aleph.cli import run_pipeline
from aleph.project.builder import build_project


@pytest.fixture
def cross_file_project(tmp_path):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()

    (src / "a.py").write_text(
        "def helper_inner():\n"
        "    return 41\n"
        "\n"
        "def foo():\n"
        "    return helper_inner() + 1\n"
        "\n"
        "def bar():\n"
        "    return 2\n"
    )
    (tests / "test_a.py").write_text(
        "from a import foo\n"
        "\n"
        "def test_foo():\n"
        "    assert foo() == 42\n"
    )
    return str(tmp_path)


def _coverage_by_name(result):
    return {e.qualified_name: e for e in result.coverage_component.entries}


class TestProjectLevelCoverage:
    def test_cross_file_test_covers_src_symbol(self, cross_file_project):
        result = build_project(cross_file_project, run_pipeline)
        cov = _coverage_by_name(result)

        assert "foo" in cov
        assert cov["foo"].status == "covered"
        # Direct call from one distinct test function
        assert cov["foo"].test_count >= 1

    def test_uncalled_symbol_stays_uncovered(self, cross_file_project):
        result = build_project(cross_file_project, run_pipeline)
        cov = _coverage_by_name(result)

        assert "bar" in cov
        assert cov["bar"].status == "none"
        assert cov["bar"].test_count == 0

    def test_transitive_reach_is_weak_coverage(self, cross_file_project):
        """helper_inner is 2 hops from the test (test -> foo -> helper_inner)."""
        result = build_project(cross_file_project, run_pipeline)
        cov = _coverage_by_name(result)

        assert "helper_inner" in cov
        assert cov["helper_inner"].status == "covered"
        # Weak (transitive) coverage: no test calls it directly
        assert cov["helper_inner"].test_count == 0

    def test_summary_counts_reflect_upgrades(self, cross_file_project):
        result = build_project(cross_file_project, run_pipeline)
        comp = result.coverage_component

        assert comp.covered >= 2  # foo + helper_inner
        assert comp.none_count >= 1  # bar
        assert comp.symbols_total == len(comp.entries)
        assert comp.covered == sum(
            1 for e in comp.entries if e.status == "covered"
        )

    def test_serialized_artifact_lists_covered_section(self, cross_file_project, tmp_path):
        from aleph.emit.serializer import AlephSerializer

        result = build_project(cross_file_project, run_pipeline)
        text = AlephSerializer().serialize_project_coverage(
            result.coverage_component, salience=result.salience_component,
        )
        assert "[COVERED]" in text
        covered_section = text.split("[COVERED]")[1].split("[/COVERED]")[0]
        assert "foo" in covered_section
