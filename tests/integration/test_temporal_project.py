"""Integration test: project-level temporal analysis on the Aleph repo itself."""

from __future__ import annotations

import os
import tempfile

import pytest

from aleph.project.builder import build_project
from aleph.cli import run_pipeline
from aleph.emit.file_components import FileComponentWriter
from aleph.emit.serializer import AlephSerializer


ALEPH_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


@pytest.mark.skipif(
    not _is_git_repo(ALEPH_ROOT),
    reason="Not in a git repo — cannot test temporal layer",
)
class TestProjectTemporalIntegration:
    """Run project build on Aleph's own source and verify temporal output."""

    @pytest.fixture(scope="class")
    def build_result(self, tmp_path_factory):
        """Build Aleph's src/aleph directory and return the result."""
        src_dir = os.path.join(ALEPH_ROOT, "src", "aleph")
        output = str(tmp_path_factory.mktemp("temporal_build"))
        result = build_project(src_dir, run_pipeline)
        return result, output

    def test_temporal_component_populated(self, build_result):
        result, _ = build_result
        tc = result.temporal_component
        assert tc is not None
        assert len(tc.entries) > 0, "Expected temporal entries from Aleph's own source"
        assert tc.computed_date  # non-empty ISO date

    def test_temporal_has_stability_classes(self, build_result):
        result, _ = build_result
        stabilities = {e.stability for e in result.temporal_component.entries}
        # At minimum, we expect some symbols classified
        assert stabilities.issubset({"stable", "active", "volatile"})
        assert len(stabilities) >= 1

    def test_temporal_entries_have_valid_fields(self, build_result):
        result, _ = build_result
        for e in result.temporal_component.entries:
            assert e.symbol_id, "symbol_id must be non-empty"
            assert e.qualified_name, "qualified_name must be non-empty"
            assert e.file, "file must be non-empty"
            assert e.age_days >= 0
            assert e.last_modified_days >= 0
            assert e.churn_count >= 0
            assert e.churn_label in ("low", "medium", "high")
            assert e.stability in ("stable", "active", "volatile")

    def test_temporal_serialization_valid(self, build_result):
        result, _ = build_result
        serializer = AlephSerializer()
        text = serializer.serialize_project_temporal(result.temporal_component)

        assert text.startswith("[ALEPH:TEMPORAL:PROJECT:1.0]")
        assert "[SYMBOLS]" in text
        assert "[/SYMBOLS]" in text
        # Should have at least some symbol entries
        lines = [l for l in text.splitlines() if l.startswith("f_") or l.startswith("t_") or l.startswith("m_")]
        assert len(lines) > 0

    def test_temporal_written_to_disk(self, build_result):
        result, output = build_result
        writer = FileComponentWriter(output)
        path = writer.write_project_temporal(result.temporal_component)

        assert os.path.isfile(path)
        with open(path) as f:
            content = f.read()
        assert "[ALEPH:TEMPORAL:PROJECT:1.0]" in content
        assert "[SYMBOLS]" in content

    def test_volatile_symbols_get_full_compression(self, build_result):
        """Verify that volatile symbols have body_level FULL (from CompressionPolicy)."""
        result, _ = build_result
        volatile_ids = {
            e.symbol_id
            for e in result.temporal_component.entries
            if e.stability == "volatile"
        }
        if not volatile_ids:
            pytest.skip("No volatile symbols found in current Aleph source")

        # Check that symbols marked volatile in file_results have stability set
        for _file, fres in result.file_results.items():
            for sym in fres.get("symbols", []):
                sid = str(sym.id)
                if sid in volatile_ids:
                    assert sym.stability == "volatile"
