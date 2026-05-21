"""Phase 2.6 CI gate: Aleph builds its own project map.

If Aleph cannot build itself, this test fails.  This is the definitive
self-application proof at the project level — every project-level component
must be emitted, every semantic hash must be present, and the output must
be internally consistent.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from aleph.cli import run_pipeline
from aleph.project.builder import build_project
from aleph.emit.file_components import FileComponentWriter
from aleph.emit.serializer import AlephSerializer


def _aleph_src_dir() -> str:
    d = os.path.join(os.path.dirname(__file__), "..", "..", "src", "aleph")
    return os.path.abspath(d)


@pytest.fixture(scope="module")
def self_build():
    """Build Aleph on its own source tree (cached across all tests in module)."""
    return build_project(_aleph_src_dir(), run_pipeline)


# ── Gate: build succeeds with zero errors ──


class TestSelfBuildSucceeds:
    def test_no_errors(self, self_build):
        assert len(self_build.stats.errors) == 0, (
            f"Build errors: {self_build.stats.errors}"
        )

    def test_all_source_files_compiled(self, self_build):
        assert self_build.stats.total_files > 20

    def test_symbols_extracted(self, self_build):
        assert self_build.stats.total_symbols > 100


# ── All project-level components emitted ──


class TestProjectComponentsEmitted:
    """Every project-level component must be non-empty."""

    def test_map_emitted(self, self_build):
        assert len(self_build.map_component.files) == self_build.stats.total_files

    def test_struct_emitted(self, self_build):
        # struct has cross-refs or file deps (may be empty if no cross-file calls)
        assert self_build.struct_component is not None

    def test_dict_emitted(self, self_build):
        assert len(self_build.dict_component.symbols) == self_build.stats.total_symbols

    def test_salience_emitted(self, self_build):
        assert len(self_build.salience_component.entries) == self_build.stats.total_symbols

    def test_temporal_emitted(self, self_build):
        assert len(self_build.temporal_component.entries) > 0

    def test_attention_emitted(self, self_build):
        attn = self_build.attention_component
        assert len(attn.entries) == self_build.stats.total_symbols
        assert sum(attn.budget.values()) == self_build.stats.total_symbols

    def test_coverage_emitted(self, self_build):
        cov = self_build.coverage_component
        assert cov.symbols_total > 0
        assert cov.symbols_total == cov.covered + cov.partial + cov.none_count
        assert len(cov.entries) == cov.symbols_total

    def test_fs_emitted(self, self_build):
        assert len(self_build.fs_component.files) == self_build.stats.total_files


# ── Semantic hashes present and valid ──


class TestSemanticHashes:
    def test_every_file_has_semantic_hash(self, self_build):
        for entry in self_build.map_component.files:
            assert entry.semantic_hash, f"{entry.path} missing semantic hash"
            assert len(entry.semantic_hash) >= 8, (
                f"{entry.path} hash too short: {entry.semantic_hash}"
            )

    def test_all_files_are_python(self, self_build):
        for entry in self_build.map_component.files:
            assert entry.language == "python", f"{entry.path} is {entry.language}"

    def test_hashes_are_distinct_per_file(self, self_build):
        hashes = [e.semantic_hash for e in self_build.map_component.files]
        # Most files should have unique hashes (some tiny __init__.py may collide)
        unique = set(hashes)
        assert len(unique) > len(hashes) * 0.5


# ── Self-consistency checks ──


class TestSelfConsistency:
    def test_dict_symbols_reference_valid_files(self, self_build):
        map_paths = {f.path for f in self_build.map_component.files}
        for sym in self_build.dict_component.symbols:
            assert sym.file in map_paths, (
                f"symbol {sym.symbol_id} references unknown file {sym.file}"
            )

    def test_fs_matches_map(self, self_build):
        fs_paths = {f.path for f in self_build.fs_component.files}
        map_paths = {f.path for f in self_build.map_component.files}
        assert fs_paths == map_paths

    def test_salience_scores_normalized(self, self_build):
        for e in self_build.salience_component.entries:
            assert 0.0 <= e.score <= 1.0, (
                f"{e.symbol_id} has score {e.score}"
            )

    def test_attention_levels_valid(self, self_build):
        from aleph.model.enums import AttentionLevel
        for e in self_build.attention_component.entries:
            assert isinstance(e.level, AttentionLevel)

    def test_temporal_stabilities_valid(self, self_build):
        valid = {"stable", "active", "volatile"}
        for e in self_build.temporal_component.entries:
            assert e.stability in valid, (
                f"{e.symbol_id} has stability '{e.stability}'"
            )

    def test_coverage_statuses_valid(self, self_build):
        valid = {"covered", "partial", "none"}
        for e in self_build.coverage_component.entries:
            assert e.status in valid, (
                f"{e.symbol_id} has status '{e.status}'"
            )


# ── Artifacts serialize and write to disk ──


class TestArtifactsSerialization:
    def test_all_artifacts_write_to_disk(self, self_build):
        with tempfile.TemporaryDirectory() as tmp:
            writer = FileComponentWriter(tmp)
            writer.write_project_map(self_build.map_component)
            writer.write_project_dict(self_build.dict_component)
            writer.write_project_fs(self_build.fs_component)
            writer.write_project_struct(self_build.struct_component)
            writer.write_project_salience(self_build.salience_component)
            writer.write_project_attention(self_build.attention_component)
            writer.write_project_temporal(self_build.temporal_component)
            writer.write_project_coverage(self_build.coverage_component)

            expected_artifacts = [
                "project.aleph.map",
                "project.aleph.dict",
                "project.aleph.fs",
                "project.aleph.struct",
                "project.aleph.salience",
                "project.aleph.attention",
                "project.aleph.temporal",
                "project.aleph.coverage",
            ]
            for name in expected_artifacts:
                path = os.path.join(tmp, name)
                assert os.path.isfile(path), f"Missing artifact: {name}"
                with open(path) as f:
                    content = f.read()
                assert content.startswith("[ALEPH:"), (
                    f"{name} has wrong header: {content[:40]}"
                )
                assert len(content) > 20, f"{name} is suspiciously small"

    def test_serialized_map_contains_all_files(self, self_build):
        serializer = AlephSerializer()
        text = serializer.serialize_project_map(self_build.map_component)
        for entry in self_build.map_component.files:
            assert entry.path in text
            assert f"hash={entry.semantic_hash}" in text
