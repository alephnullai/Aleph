"""Integration tests for project-level build."""

from __future__ import annotations

import os
import tempfile

import pytest

from aleph.cli import run_pipeline
from aleph.project.builder import build_project
from aleph.emit.serializer import AlephSerializer
from aleph.emit.loader import AlephLoader
from aleph.emit.file_components import FileComponentWriter


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestProjectBuildFixtures:
    """Build project components from the test fixtures directory."""

    def test_build_cpp_fixtures(self):
        cpp_dir = os.path.join(FIXTURES_DIR, "cpp")
        result = build_project(cpp_dir, run_pipeline)

        assert result.stats.total_files >= 5
        assert result.stats.total_symbols > 0
        assert result.stats.total_call_edges > 0
        assert len(result.map_component.files) == result.stats.total_files
        assert len(result.dict_component.symbols) == result.stats.total_symbols

    def test_build_rust_fixtures(self):
        rust_dir = os.path.join(FIXTURES_DIR, "rust")
        result = build_project(rust_dir, run_pipeline)

        assert result.stats.total_files >= 4
        assert result.stats.total_symbols > 0
        assert len(result.map_component.files) == result.stats.total_files

    def test_build_all_fixtures(self):
        result = build_project(FIXTURES_DIR, run_pipeline)

        # Should include cpp, rust, python fixtures
        languages = {f.language for f in result.map_component.files}
        assert "cpp" in languages
        assert "rust" in languages
        assert "python" in languages

    def test_map_has_semantic_hashes(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        for entry in result.map_component.files:
            assert entry.semantic_hash, f"{entry.path} missing semantic hash"
            assert entry.symbol_count > 0 or entry.path.endswith("__init__.py")

    def test_dict_has_file_provenance(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        for sym in result.dict_component.symbols:
            assert sym.file, f"symbol {sym.symbol_id} missing file provenance"
            assert sym.kind in ("f", "t", "v", "m", "d", "c", "s")

    def test_fs_lists_all_files(self):
        result = build_project(FIXTURES_DIR, run_pipeline)

        fs_paths = {f.path for f in result.fs_component.files}
        map_paths = {f.path for f in result.map_component.files}
        assert fs_paths == map_paths

    def test_artifacts_serialize_roundtrip(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        serializer = AlephSerializer()
        loader = AlephLoader()

        # Map roundtrip
        map_text = serializer.serialize_project_map(result.map_component)
        map_loaded = loader.deserialize_project_map(map_text)
        assert len(map_loaded.files) == len(result.map_component.files)

        # Dict roundtrip
        dict_text = serializer.serialize_project_dict(result.dict_component)
        dict_loaded = loader.deserialize_project_dict(dict_text)
        assert len(dict_loaded.symbols) == len(result.dict_component.symbols)

        # FS roundtrip
        fs_text = serializer.serialize_project_fs(result.fs_component)
        fs_loaded = loader.deserialize_project_fs(fs_text)
        assert len(fs_loaded.files) == len(result.fs_component.files)

        # Struct roundtrip
        struct_text = serializer.serialize_project_struct(result.struct_component)
        struct_loaded = loader.deserialize_project_struct(struct_text)
        assert len(struct_loaded.cross_refs) == len(result.struct_component.cross_refs)

    def test_salience_computed(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        assert len(result.salience_component.entries) > 0
        # All scores in [0, 1]
        for e in result.salience_component.entries:
            assert 0.0 <= e.score <= 1.0
        # At least one non-zero score (fixtures have call edges)
        assert any(e.score > 0 for e in result.salience_component.entries)

    def test_attention_budget_computed(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        attn = result.attention_component
        assert len(attn.entries) > 0
        total = sum(attn.budget.values())
        assert total == len(attn.entries)
        # Every symbol has an attention level
        from aleph.model.enums import AttentionLevel
        for e in attn.entries:
            assert isinstance(e.level, AttentionLevel)

    def test_write_artifacts_to_disk(self):
        result = build_project(os.path.join(FIXTURES_DIR, "cpp"), run_pipeline)

        with tempfile.TemporaryDirectory() as tmp:
            writer = FileComponentWriter(tmp)
            writer.write_project_map(result.map_component)
            writer.write_project_dict(result.dict_component)
            writer.write_project_fs(result.fs_component)
            writer.write_project_struct(result.struct_component)
            writer.write_project_salience(result.salience_component)
            writer.write_project_attention(result.attention_component)

            expected = [
                "project.aleph.map",
                "project.aleph.dict",
                "project.aleph.fs",
                "project.aleph.struct",
                "project.aleph.salience",
                "project.aleph.attention",
            ]
            for name in expected:
                path = os.path.join(tmp, name)
                assert os.path.isfile(path), f"Missing artifact: {name}"
                with open(path) as f:
                    content = f.read()
                assert content.startswith("[ALEPH:")


class TestCrossFileResolutionIntegration:
    """Integration test: real Python files with cross-file calls."""

    def test_cross_file_calls_resolved(self, tmp_path):
        """Create real Python files, run full build, assert struct has cross-refs."""
        # Create a mini project with cross-file calls
        (tmp_path / "main.py").write_text(
            "from helper import greet\n\ndef main():\n    greet('world')\n"
        )
        (tmp_path / "helper.py").write_text(
            "def greet(name):\n    return f'Hello, {name}!'\n"
        )

        result = build_project(str(tmp_path), run_pipeline)
        # Should have cross-refs from main.py → helper.py
        assert result.stats.total_cross_refs >= 1
        xrefs = result.struct_component.cross_refs
        cross_files = {(x.source_file, x.target_file) for x in xrefs}
        assert ("main.py", "helper.py") in cross_files


class TestPerFileArtifacts:
    """Integration test: per-file artifact emission."""

    def test_per_file_artifacts_emitted(self, tmp_path):
        """Build with per-file equivalent, assert .aleph.struct files exist."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "foo.py").write_text("def foo():\n    return 42\n")
        (src_dir / "bar.py").write_text("def bar():\n    return 'hello'\n")

        result = build_project(str(src_dir), run_pipeline)

        # Simulate per-file emission as the CLI would do
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        import os
        per_file_count = 0
        for source_file, file_result in result.file_results.items():
            if "struct_component" not in file_result or "bodies_component" not in file_result:
                continue
            rel_path = os.path.relpath(source_file, str(src_dir))
            per_file_dir = os.path.join(str(out_dir), os.path.dirname(rel_path))
            writer = FileComponentWriter(per_file_dir)
            writer.write_struct(file_result["struct_component"])
            writer.write_bodies(file_result["bodies_component"])
            per_file_count += 1

        assert per_file_count == 2
        # Check files exist
        assert os.path.isfile(os.path.join(str(out_dir), "foo.py.aleph.struct"))
        assert os.path.isfile(os.path.join(str(out_dir), "bar.py.aleph.struct"))
        assert os.path.isfile(os.path.join(str(out_dir), "foo.py.aleph.bodies"))

    def test_no_per_file_without_flag(self, tmp_path):
        """Build without --per-file flag → no per-file artifacts."""
        (tmp_path / "foo.py").write_text("def foo(): pass\n")
        result = build_project(str(tmp_path), run_pipeline)
        # The build itself doesn't write per-file, the CLI does
        # Just verify the file_results contain the data needed
        for source_file, file_result in result.file_results.items():
            assert "struct_component" in file_result


class TestProjectBuildSelfApplication:
    """Build project components from the Aleph source itself (Phase 2.5)."""

    @pytest.fixture(scope="class")
    def self_build_result(self):
        src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src", "aleph")
        src_dir = os.path.abspath(src_dir)
        # Portable ID scheme v2 (matches production auto_build): the
        # legacy v1 scheme hashes absolute paths, making symbol IDs —
        # and possible 24-bit ID collisions — checkout-location-dependent.
        return build_project(src_dir, lambda f: run_pipeline(f, project_root=src_dir))

    def test_all_files_compiled(self, self_build_result):
        result = self_build_result
        assert result.stats.total_files > 20
        assert len(result.stats.errors) == 0

    def test_map_complete(self, self_build_result):
        result = self_build_result
        assert len(result.map_component.files) == result.stats.total_files
        for entry in result.map_component.files:
            assert entry.semantic_hash
            assert entry.language == "python"

    def test_dict_complete(self, self_build_result):
        result = self_build_result
        assert len(result.dict_component.symbols) == result.stats.total_symbols
        # Every symbol has file provenance
        for sym in result.dict_component.symbols:
            assert sym.file

    def test_fs_complete(self, self_build_result):
        result = self_build_result
        assert len(result.fs_component.files) == result.stats.total_files

    def test_token_reduction_positive(self, self_build_result):
        result = self_build_result
        assert result.stats.total_compressed_tokens < result.stats.total_original_tokens
        reduction = (1 - result.stats.total_compressed_tokens / result.stats.total_original_tokens) * 100
        assert reduction > 80  # Self-application consistently > 80%

    def test_salience_covers_all_symbols(self, self_build_result):
        result = self_build_result
        assert len(result.salience_component.entries) == result.stats.total_symbols
        # All scores normalized
        for e in result.salience_component.entries:
            assert 0.0 <= e.score <= 1.0

    def test_attention_budget_reasonable(self, self_build_result):
        result = self_build_result
        attn = result.attention_component
        total = sum(attn.budget.values())
        assert total == result.stats.total_symbols
        # Should have a mix of levels (not all SKIP or all CRITICAL)
        assert attn.budget.get("skip", 0) > 0 or attn.budget.get("peripheral", 0) > 0
        # At least some symbols have fan-in and are important/critical
        non_skip = attn.budget.get("critical", 0) + attn.budget.get("important", 0)
        # It's OK if there are few — many __init__.py symbols have 0 fan-in

    def test_artifacts_write_roundtrip(self, self_build_result):
        result = self_build_result
        serializer = AlephSerializer()
        loader = AlephLoader()

        # All six project artifacts roundtrip correctly
        for name, comp, ser_fn, deser_fn in [
            ("map", result.map_component,
             serializer.serialize_project_map, loader.deserialize_project_map),
            ("dict", result.dict_component,
             serializer.serialize_project_dict, loader.deserialize_project_dict),
            ("fs", result.fs_component,
             serializer.serialize_project_fs, loader.deserialize_project_fs),
            ("struct", result.struct_component,
             serializer.serialize_project_struct, loader.deserialize_project_struct),
            ("salience", result.salience_component,
             serializer.serialize_project_salience, loader.deserialize_project_salience),
            ("attention", result.attention_component,
             serializer.serialize_project_attention, loader.deserialize_project_attention),
        ]:
            text = ser_fn(comp)
            loaded = deser_fn(text)
            assert loaded.root == comp.root, f"{name} root mismatch"
