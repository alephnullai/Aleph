"""Tests for project-level serialization and deserialization roundtrips."""

from __future__ import annotations

import pytest

from aleph.model.components import (
    ProjectMapComponent, ProjectFileEntry,
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectFSComponent, ProjectFSEntry, ProjectModuleDep,
    ProjectStructComponent, ProjectCrossRef, ProjectFileDep,
    ProjectSalienceComponent, ProjectSalienceEntry,
    ProjectAttentionComponent, ProjectAttentionEntry,
    ProjectTemporalComponent, ProjectTemporalEntry,
)
from aleph.model.enums import AttentionLevel
from aleph.emit.serializer import AlephSerializer
from aleph.emit.loader import AlephLoader


@pytest.fixture
def serializer():
    return AlephSerializer()


@pytest.fixture
def loader():
    return AlephLoader()


class TestProjectMapRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectMapComponent(root="/project", files=[])
        text = serializer.serialize_project_map(comp)
        assert text.startswith("[ALEPH:MAP:1.0]")
        assert "[ROOT:/project]" in text

    def test_roundtrip(self, serializer, loader):
        comp = ProjectMapComponent(root="/project", files=[
            ProjectFileEntry(
                path="src/main.cpp", language="cpp", semantic_hash="abc123",
                symbol_count=10, call_edge_count=5,
                original_tokens=500, compressed_tokens=150,
                reduction_percent=70.0,
            ),
            ProjectFileEntry(
                path="src/util.rs", language="rust", semantic_hash="def456",
                symbol_count=3, call_edge_count=1,
                original_tokens=200, compressed_tokens=80,
                reduction_percent=60.0,
            ),
        ])
        text = serializer.serialize_project_map(comp)
        loaded = loader.deserialize_project_map(text)

        assert loaded.root == "/project"
        assert len(loaded.files) == 2
        assert loaded.files[0].path == "src/main.cpp"
        assert loaded.files[0].semantic_hash == "abc123"
        assert loaded.files[0].symbol_count == 10
        assert loaded.files[0].original_tokens == 500
        assert loaded.files[0].compressed_tokens == 150
        assert loaded.files[1].language == "rust"

    def test_empty_files(self, serializer, loader):
        comp = ProjectMapComponent(root="/empty", files=[])
        text = serializer.serialize_project_map(comp)
        loaded = loader.deserialize_project_map(text)
        assert loaded.root == "/empty"
        assert loaded.files == []


class TestProjectDictRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectDictComponent(root="/project", symbols=[])
        text = serializer.serialize_project_dict(comp)
        assert text.startswith("[ALEPH:DICT:1.0]")

    def test_roundtrip(self, serializer, loader):
        comp = ProjectDictComponent(root="/project", symbols=[
            ProjectSymbolEntry(
                symbol_id="f_abc123", name="process",
                qualified_name="Engine::process", kind="f",
                scope="Engine", file="src/engine.cpp",
                signature_hash="sig12345",
            ),
            ProjectSymbolEntry(
                symbol_id="t_def456", name="Config",
                qualified_name="Config", kind="t",
                scope="", file="src/config.rs",
            ),
        ])
        text = serializer.serialize_project_dict(comp)
        loaded = loader.deserialize_project_dict(text)

        assert loaded.root == "/project"
        assert len(loaded.symbols) == 2
        assert loaded.symbols[0].symbol_id == "f_abc123"
        assert loaded.symbols[0].qualified_name == "Engine::process"
        assert loaded.symbols[0].kind == "f"
        assert loaded.symbols[0].file == "src/engine.cpp"
        assert loaded.symbols[0].signature_hash == "sig12345"
        assert loaded.symbols[1].kind == "t"
        assert loaded.symbols[1].scope == ""


class TestProjectFSRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectFSComponent(root="/project", files=[], module_deps=[])
        text = serializer.serialize_project_fs(comp)
        assert text.startswith("[ALEPH:FS:1.0]")

    def test_roundtrip(self, serializer, loader):
        comp = ProjectFSComponent(root="/project", files=[
            ProjectFSEntry(path="src/main.cpp", language="cpp", symbol_count=10),
            ProjectFSEntry(path="src/util.rs", language="rust", symbol_count=3),
        ], module_deps=[
            ProjectModuleDep(source="src/main.cpp", target="src/util.rs", symbol_count=2),
        ])
        text = serializer.serialize_project_fs(comp)
        loaded = loader.deserialize_project_fs(text)

        assert loaded.root == "/project"
        assert len(loaded.files) == 2
        assert loaded.files[0].path == "src/main.cpp"
        assert loaded.files[0].symbol_count == 10
        assert len(loaded.module_deps) == 1
        assert loaded.module_deps[0].source == "src/main.cpp"
        assert loaded.module_deps[0].target == "src/util.rs"
        assert loaded.module_deps[0].symbol_count == 2

    def test_no_deps(self, serializer, loader):
        comp = ProjectFSComponent(root="/project", files=[
            ProjectFSEntry(path="solo.py", language="python", symbol_count=1),
        ], module_deps=[])
        text = serializer.serialize_project_fs(comp)
        assert "[DEPS]" not in text
        loaded = loader.deserialize_project_fs(text)
        assert loaded.module_deps == []


class TestProjectStructRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectStructComponent(root="/project")
        text = serializer.serialize_project_struct(comp)
        assert text.startswith("[ALEPH:STRUCT:PROJECT:1.0]")

    def test_roundtrip(self, serializer, loader):
        comp = ProjectStructComponent(root="/project", cross_refs=[
            ProjectCrossRef(
                caller_id="f_abc123", callee_id="f_def456",
                source_file="src/a.cpp", target_file="src/b.cpp",
            ),
        ], file_deps=[
            ProjectFileDep(source="src/a.cpp", target="src/b.cpp", symbol_refs=3),
        ])
        text = serializer.serialize_project_struct(comp)
        loaded = loader.deserialize_project_struct(text)

        assert loaded.root == "/project"
        assert len(loaded.cross_refs) == 1
        assert loaded.cross_refs[0].caller_id == "f_abc123"
        assert loaded.cross_refs[0].target_file == "src/b.cpp"
        assert len(loaded.file_deps) == 1
        assert loaded.file_deps[0].symbol_refs == 3

    def test_empty_project(self, serializer, loader):
        comp = ProjectStructComponent(root="/empty")
        text = serializer.serialize_project_struct(comp)
        assert "[XREFS]" not in text
        assert "[FILEDEPS]" not in text
        loaded = loader.deserialize_project_struct(text)
        assert loaded.cross_refs == []
        assert loaded.file_deps == []


class TestProjectSalienceRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectSalienceComponent(root="/project", entries=[])
        text = serializer.serialize_project_salience(comp)
        assert text.startswith("[ALEPH:SALIENCE:PROJECT:1.0]")

    def test_roundtrip(self, serializer, loader):
        comp = ProjectSalienceComponent(root="/project", entries=[
            ProjectSalienceEntry(
                symbol_id="f_abc123", qualified_name="Engine::process",
                file="src/engine.cpp", score=0.85,
                local_fan_in=3, cross_file_fan_in=2, total_fan_in=5,
            ),
            ProjectSalienceEntry(
                symbol_id="f_def456", qualified_name="helper",
                file="src/util.cpp", score=0.1,
                local_fan_in=1, cross_file_fan_in=0, total_fan_in=1,
            ),
        ])
        text = serializer.serialize_project_salience(comp)
        loaded = loader.deserialize_project_salience(text)

        assert loaded.root == "/project"
        assert len(loaded.entries) == 2
        assert loaded.entries[0].symbol_id == "f_abc123"
        assert loaded.entries[0].score == 0.85
        assert loaded.entries[0].local_fan_in == 3
        assert loaded.entries[0].cross_file_fan_in == 2
        assert loaded.entries[1].qualified_name == "helper"

    def test_empty(self, serializer, loader):
        comp = ProjectSalienceComponent(root="/empty", entries=[])
        text = serializer.serialize_project_salience(comp)
        loaded = loader.deserialize_project_salience(text)
        assert loaded.entries == []


class TestProjectAttentionRoundtrip:
    def test_serialize_has_header(self, serializer):
        comp = ProjectAttentionComponent(root="/project", entries=[], budget={})
        text = serializer.serialize_project_attention(comp)
        assert text.startswith("[ALEPH:ATTENTION:1.0]")

    def test_roundtrip(self, serializer, loader):
        comp = ProjectAttentionComponent(root="/project", entries=[
            ProjectAttentionEntry(
                symbol_id="f_abc123", qualified_name="Engine::process",
                file="src/engine.cpp", level=AttentionLevel.CRITICAL, score=0.85,
            ),
            ProjectAttentionEntry(
                symbol_id="f_def456", qualified_name="helper",
                file="src/util.cpp", level=AttentionLevel.SKIP, score=0.01,
            ),
        ], budget={"critical": 1, "important": 0, "peripheral": 0, "skip": 1})
        text = serializer.serialize_project_attention(comp)
        loaded = loader.deserialize_project_attention(text)

        assert loaded.root == "/project"
        assert len(loaded.entries) == 2
        assert loaded.entries[0].level == AttentionLevel.CRITICAL
        assert loaded.entries[0].score == 0.85
        assert loaded.entries[1].level == AttentionLevel.SKIP
        assert loaded.budget["critical"] == 1
        assert loaded.budget["skip"] == 1

    def test_empty(self, serializer, loader):
        comp = ProjectAttentionComponent(root="/empty", entries=[], budget={})
        text = serializer.serialize_project_attention(comp)
        loaded = loader.deserialize_project_attention(text)
        assert loaded.entries == []


class TestDictImportsSeparation:
    """Phase 2.9: d_ symbols in [IMPORTS], not [SYMBOLS]."""

    def test_imports_in_separate_section(self, serializer):
        comp = ProjectDictComponent(root="/project", symbols=[
            ProjectSymbolEntry(
                symbol_id="f_abc123", name="process",
                qualified_name="Engine::process", kind="f",
                scope="Engine", file="src/engine.py",
            ),
            ProjectSymbolEntry(
                symbol_id="d_xyz789", name="import os",
                qualified_name="import os", kind="d",
                scope="", file="src/engine.py",
            ),
        ])
        text = serializer.serialize_project_dict(comp)
        # d_ should be in [IMPORTS], not [SYMBOLS]
        symbols_section = text.split("[SYMBOLS]")[1].split("[/SYMBOLS]")[0]
        imports_section = text.split("[IMPORTS]")[1].split("[/IMPORTS]")[0]
        assert "f_abc123" in symbols_section
        assert "d_xyz789" not in symbols_section
        assert "d_xyz789" in imports_section

    def test_imports_roundtrip(self, serializer, loader):
        comp = ProjectDictComponent(root="/project", symbols=[
            ProjectSymbolEntry(
                symbol_id="f_abc123", name="process",
                qualified_name="process", kind="f",
                scope="", file="src/a.py",
            ),
            ProjectSymbolEntry(
                symbol_id="d_xyz789", name="import os",
                qualified_name="import os", kind="d",
                scope="", file="src/a.py",
            ),
        ])
        text = serializer.serialize_project_dict(comp)
        loaded = loader.deserialize_project_dict(text)
        assert len(loaded.symbols) == 2
        kinds = {s.kind for s in loaded.symbols}
        assert "f" in kinds
        assert "d" in kinds


class TestStructXrefNames:
    """Phase 2.9: human-readable names in XREFS."""

    def test_names_in_xref_output(self, serializer):
        comp = ProjectStructComponent(root="/project", cross_refs=[
            ProjectCrossRef(
                caller_id="f_abc123", callee_id="f_def456",
                source_file="src/a.py", target_file="src/b.py",
                caller_name="do_stuff", callee_name="helper",
            ),
        ])
        text = serializer.serialize_project_struct(comp)
        assert "f_abc123(do_stuff)->f_def456(helper)" in text

    def test_names_roundtrip(self, serializer, loader):
        comp = ProjectStructComponent(root="/project", cross_refs=[
            ProjectCrossRef(
                caller_id="f_abc123", callee_id="f_def456",
                source_file="src/a.py", target_file="src/b.py",
                caller_name="do_stuff", callee_name="Engine::helper",
            ),
        ], file_deps=[])
        text = serializer.serialize_project_struct(comp)
        loaded = loader.deserialize_project_struct(text)
        assert loaded.cross_refs[0].caller_id == "f_abc123"
        assert loaded.cross_refs[0].callee_id == "f_def456"
        assert loaded.cross_refs[0].caller_name == "do_stuff"
        assert loaded.cross_refs[0].callee_name == "Engine::helper"

    def test_no_names_backward_compat(self, serializer, loader):
        """Old format without names still parses."""
        text = """[ALEPH:STRUCT:PROJECT:1.0]
[ROOT:/project]
[XREFS]
f_abc123->f_def456 src=src/a.py dst=src/b.py
[/XREFS]
"""
        loaded = loader.deserialize_project_struct(text)
        assert loaded.cross_refs[0].caller_id == "f_abc123"
        assert loaded.cross_refs[0].callee_id == "f_def456"
        assert loaded.cross_refs[0].caller_name == ""


class TestTemporalHistoryHeader:
    """Phase 2.9: insufficient_history in temporal header."""

    def test_insufficient_history_in_output(self, serializer):
        comp = ProjectTemporalComponent(
            root="/project", computed_date="2026-03-17",
            entries=[], insufficient_history=True,
        )
        text = serializer.serialize_project_temporal(comp)
        assert "[HISTORY:insufficient]" in text

    def test_sufficient_history_in_output(self, serializer):
        comp = ProjectTemporalComponent(
            root="/project", computed_date="2026-03-17",
            entries=[], insufficient_history=False,
        )
        text = serializer.serialize_project_temporal(comp)
        assert "[HISTORY:sufficient]" in text
