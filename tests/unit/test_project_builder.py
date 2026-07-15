"""Tests for project-level build functionality."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from aleph.project.builder import (
    build_project, BuildResult,
    _build_global_name_index, _build_import_graph, _resolve_cross_file_calls,
)
from aleph.model.components import (
    ProjectMapComponent, ProjectDictComponent,
    ProjectFSComponent, ProjectStructComponent,
    StructComponent,
)
from aleph.model.symbol import SymbolID, RawSymbol, Symbol, Span
from aleph.model.enums import SymbolKind


def _make_symbol(name: str, kind: str = "f", scope: str = "", source_file: str = "test.py") -> Symbol:
    raw = RawSymbol(
        name=name,
        qualified_name=f"{scope}.{name}" if scope else name,
        kind=SymbolKind(kind),
        scope=scope,
        span=MagicMock(),
        language="python",
        source_file=source_file,
        body_text=f"def {name}(): pass",
        signature_text=f"def {name}()",
    )
    sid = SymbolID(prefix=kind, hex_hash=f"{hash(name) % 0xFFFFFF:06x}")
    return Symbol(id=sid, raw=raw)


def _make_pipeline_result(
    source_file: str,
    symbols: list[Symbol] | None = None,
    call_edges: list[tuple[str, str]] | None = None,
) -> dict:
    symbols = symbols or []
    call_edges = call_edges or []
    struct = StructComponent(
        source_file=source_file,
        call_edges=call_edges,
        symbols={str(s.id): s for s in symbols},
    )
    return {
        "source_file": source_file,
        "language": "python",
        "symbols_extracted": len(symbols),
        "call_edges": len(call_edges),
        "semantic_hash": f"hash_{os.path.basename(source_file)}",
        "original_tokens": 100,
        "compressed_tokens": 30,
        "token_reduction_percent": 70.0,
        "struct_component": struct,
        "symbols": symbols,
    }


class TestBuildProject:
    def test_empty_directory(self, tmp_path):
        result = build_project(str(tmp_path), lambda f: {})
        assert result.stats.total_files == 0
        assert len(result.map_component.files) == 0

    def test_single_file(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("def hello(): pass\n")

        sym = _make_symbol("hello", source_file=str(f))

        def runner(path):
            return _make_pipeline_result(path, symbols=[sym])

        result = build_project(str(tmp_path), runner)
        assert result.stats.total_files == 1
        assert result.stats.total_symbols == 1
        assert len(result.map_component.files) == 1
        assert result.map_component.files[0].path == "hello.py"
        assert result.map_component.files[0].language == "python"

    def test_dict_has_all_symbols(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("def foo(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def bar(): pass\n")

        sym_foo = _make_symbol("foo", source_file=str(f1))
        sym_bar = _make_symbol("bar", source_file=str(f2))

        def runner(path):
            if "a.py" in path:
                return _make_pipeline_result(path, symbols=[sym_foo])
            return _make_pipeline_result(path, symbols=[sym_bar])

        result = build_project(str(tmp_path), runner)
        assert result.stats.total_symbols == 2
        assert len(result.dict_component.symbols) == 2
        names = {s.name for s in result.dict_component.symbols}
        assert names == {"foo", "bar"}

    def test_fs_has_all_files(self, tmp_path):
        f1 = tmp_path / "x.py"
        f1.write_text("x = 1\n")
        f2 = tmp_path / "y.py"
        f2.write_text("y = 2\n")

        def runner(path):
            return _make_pipeline_result(path)

        result = build_project(str(tmp_path), runner)
        assert len(result.fs_component.files) == 2
        paths = {f.path for f in result.fs_component.files}
        assert paths == {"x.py", "y.py"}

    def test_cross_refs_detected(self, tmp_path):
        f1 = tmp_path / "caller.py"
        f1.write_text("def caller_fn(): callee_fn()\n")
        f2 = tmp_path / "callee.py"
        f2.write_text("def callee_fn(): pass\n")

        sym_caller = _make_symbol("caller_fn", source_file=str(f1))
        sym_callee = _make_symbol("callee_fn", source_file=str(f2))

        caller_id = str(sym_caller.id)
        callee_id = str(sym_callee.id)

        def runner(path):
            if "caller.py" in path:
                return _make_pipeline_result(
                    path,
                    symbols=[sym_caller],
                    call_edges=[(caller_id, callee_id)],
                )
            return _make_pipeline_result(path, symbols=[sym_callee])

        result = build_project(str(tmp_path), runner)
        assert result.stats.total_cross_refs == 1
        assert len(result.struct_component.cross_refs) == 1
        xref = result.struct_component.cross_refs[0]
        assert xref.caller_id == caller_id
        assert xref.callee_id == callee_id

    def test_file_deps_aggregated(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("def a1(): b1(); b2()\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def b1(): pass\ndef b2(): pass\n")

        sym_a1 = _make_symbol("a1", source_file=str(f1))
        sym_b1 = _make_symbol("b1", source_file=str(f2))
        sym_b2 = _make_symbol("b2", source_file=str(f2))

        a1_id = str(sym_a1.id)
        b1_id = str(sym_b1.id)
        b2_id = str(sym_b2.id)

        def runner(path):
            if "a.py" in path:
                return _make_pipeline_result(
                    path, symbols=[sym_a1],
                    call_edges=[(a1_id, b1_id), (a1_id, b2_id)],
                )
            return _make_pipeline_result(path, symbols=[sym_b1, sym_b2])

        result = build_project(str(tmp_path), runner)
        assert len(result.struct_component.file_deps) == 1
        dep = result.struct_component.file_deps[0]
        assert dep.symbol_refs == 2

    def test_error_handling(self, tmp_path):
        f1 = tmp_path / "good.py"
        f1.write_text("def good(): pass\n")
        f2 = tmp_path / "bad.py"
        f2.write_text("syntax error\n")

        call_count = 0

        def runner(path):
            nonlocal call_count
            call_count += 1
            if "bad.py" in path:
                raise ValueError("parse error")
            return _make_pipeline_result(path, symbols=[_make_symbol("good")])

        result = build_project(str(tmp_path), runner)
        assert len(result.stats.errors) == 1
        assert "bad.py" in result.stats.errors[0]
        # Good file still processed
        assert len(result.map_component.files) == 1

    def test_token_stats_accumulated(self, tmp_path):
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text(f"def {name[0]}(): pass\n")

        def runner(path):
            return _make_pipeline_result(path)

        result = build_project(str(tmp_path), runner)
        assert result.stats.total_original_tokens == 300  # 100 * 3
        assert result.stats.total_compressed_tokens == 90   # 30 * 3

    def test_result_has_all_components(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        result = build_project(str(tmp_path), lambda p: _make_pipeline_result(p))
        assert isinstance(result.map_component, ProjectMapComponent)
        assert isinstance(result.dict_component, ProjectDictComponent)
        assert isinstance(result.fs_component, ProjectFSComponent)
        assert isinstance(result.struct_component, ProjectStructComponent)
        assert result.map_component.root == str(tmp_path)


def _make_symbol_with_kind(name, kind="f", scope="", source_file="test.py", qualified_name=None):
    """Helper for cross-file resolution tests — uses real Span."""
    raw = RawSymbol(
        name=name,
        qualified_name=qualified_name or (f"{scope}.{name}" if scope else name),
        kind=SymbolKind(kind),
        scope=scope,
        span=Span(0, 0, 5, 0),
        language="python",
        source_file=source_file,
        body_text=f"def {name}(): pass",
        signature_text=f"def {name}()",
    )
    sid = SymbolID(prefix=kind, hex_hash=f"{abs(hash(name + source_file)) % 0xFFFFFF:06x}")
    return Symbol(id=sid, raw=raw)


def _make_dep_symbol(import_text, source_file="test.py"):
    """Create a DEPENDENCY symbol for import graph testing."""
    raw = RawSymbol(
        name=import_text,
        qualified_name=import_text,
        kind=SymbolKind.DEPENDENCY,
        scope="",
        span=Span(0, 0, 0, 0),
        language="python",
        source_file=source_file,
        body_text="",
        signature_text="",
    )
    sid = SymbolID(prefix="d", hex_hash=f"{abs(hash(import_text + source_file)) % 0xFFFFFF:06x}")
    return Symbol(id=sid, raw=raw)


class TestCrossFileResolution:
    """Tests for Phase 2.8 cross-file call resolution."""

    def test_unresolved_call_resolved_to_other_file(self, tmp_path):
        """File A has unresolved call to function defined in file B → cross-ref created."""
        f1 = tmp_path / "caller.py"
        f1.write_text("from callee import target\ndef caller_fn(): target()\n")
        f2 = tmp_path / "callee.py"
        f2.write_text("def target(): pass\n")

        sym_caller = _make_symbol_with_kind("caller_fn", source_file=str(f1))
        sym_target = _make_symbol_with_kind("target", source_file=str(f2))
        caller_id = str(sym_caller.id)
        target_id = str(sym_target.id)

        struct_a = StructComponent(
            source_file=str(f1),
            call_edges=[],  # no resolved edges within file
            call_edge_metadata=[{
                "caller_id": caller_id,
                "callee_name": "target",
                "status": "unresolved",
                "resolved_id": "",
                "candidate_count": "0",
            }],
            symbols={caller_id: sym_caller},
        )
        struct_b = StructComponent(
            source_file=str(f2),
            call_edges=[],
            symbols={target_id: sym_target},
        )

        def runner(path):
            if "caller.py" in path:
                return {
                    "source_file": path, "language": "python",
                    "symbols_extracted": 1, "call_edges": 0,
                    "semantic_hash": "h1", "original_tokens": 50,
                    "compressed_tokens": 20, "token_reduction_percent": 60.0,
                    "struct_component": struct_a, "symbols": [sym_caller],
                    "call_edge_metadata": struct_a.call_edge_metadata,
                }
            return {
                "source_file": path, "language": "python",
                "symbols_extracted": 1, "call_edges": 0,
                "semantic_hash": "h2", "original_tokens": 50,
                "compressed_tokens": 20, "token_reduction_percent": 60.0,
                "struct_component": struct_b, "symbols": [sym_target],
                "call_edge_metadata": [],
            }

        result = build_project(str(tmp_path), runner)
        assert result.stats.total_cross_refs >= 1
        xrefs = result.struct_component.cross_refs
        resolved = [x for x in xrefs if x.caller_id == caller_id and x.callee_id == target_id]
        assert len(resolved) == 1

    def test_import_disambiguation(self, tmp_path):
        """Two files define 'bar', caller imports from one → correct one resolved."""
        f1 = tmp_path / "caller.py"
        f1.write_text("from mod_b import bar\ndef foo(): bar()\n")
        f2 = tmp_path / "mod_a.py"
        f2.write_text("def bar(): pass\n")
        f3 = tmp_path / "mod_b.py"
        f3.write_text("def bar(): pass\n")

        sym_foo = _make_symbol_with_kind("foo", source_file=str(f1))
        sym_bar_a = _make_symbol_with_kind("bar", source_file=str(f2))
        sym_bar_b = _make_symbol_with_kind("bar", source_file=str(f3))
        dep = _make_dep_symbol("from mod_b import bar", source_file=str(f1))
        foo_id = str(sym_foo.id)

        struct_caller = StructComponent(
            source_file=str(f1),
            call_edges=[],
            call_edge_metadata=[{
                "caller_id": foo_id,
                "callee_name": "bar",
                "status": "unresolved",
                "resolved_id": "",
                "candidate_count": "0",
            }],
            symbols={foo_id: sym_foo},
        )

        def runner(path):
            if "caller.py" in path:
                return {
                    "source_file": path, "language": "python",
                    "symbols_extracted": 2, "call_edges": 0,
                    "semantic_hash": "h1", "original_tokens": 50,
                    "compressed_tokens": 20, "token_reduction_percent": 60.0,
                    "struct_component": struct_caller,
                    "symbols": [sym_foo, dep],
                    "call_edge_metadata": struct_caller.call_edge_metadata,
                }
            sym = sym_bar_a if "mod_a.py" in path else sym_bar_b
            sid = str(sym.id)
            return {
                "source_file": path, "language": "python",
                "symbols_extracted": 1, "call_edges": 0,
                "semantic_hash": f"h_{os.path.basename(path)}",
                "original_tokens": 50, "compressed_tokens": 20,
                "token_reduction_percent": 60.0,
                "struct_component": StructComponent(source_file=path, symbols={sid: sym}),
                "symbols": [sym],
                "call_edge_metadata": [],
            }

        result = build_project(str(tmp_path), runner)
        xrefs = result.struct_component.cross_refs
        # Should resolve to mod_b's bar, not mod_a's
        resolved = [x for x in xrefs if x.caller_id == foo_id]
        assert len(resolved) == 1
        assert resolved[0].target_file == "mod_b.py"
        assert resolved[0].callee_id == str(sym_bar_b.id)
