"""Tests for the Phase 2.4 query engine."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from aleph.emit.serializer import AlephSerializer
from aleph.emit.file_components import FileComponentWriter
from aleph.model.components import (
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectStructComponent, ProjectCrossRef, ProjectFileDep,
    ProjectSalienceComponent, ProjectSalienceEntry,
    BodiesComponent, BodyEntry,
)
from aleph.model.enums import BodyLevel
from aleph.model.symbol import SymbolID
from aleph.query.engine import QueryEngine


# ── Fixtures ──

def _write_project_dict(output_dir: str, symbols: list[ProjectSymbolEntry]) -> None:
    comp = ProjectDictComponent(root=output_dir, symbols=symbols)
    writer = FileComponentWriter(output_dir)
    writer.write_project_dict(comp)


def _write_project_struct(
    output_dir: str,
    cross_refs: list[ProjectCrossRef] | None = None,
    file_deps: list[ProjectFileDep] | None = None,
) -> None:
    comp = ProjectStructComponent(
        root=output_dir,
        cross_refs=cross_refs or [],
        file_deps=file_deps or [],
    )
    writer = FileComponentWriter(output_dir)
    writer.write_project_struct(comp)


def _write_project_salience(output_dir: str, entries: list[ProjectSalienceEntry]) -> None:
    comp = ProjectSalienceComponent(root=output_dir, entries=entries)
    writer = FileComponentWriter(output_dir)
    writer.write_project_salience(comp)


def _write_bodies(output_dir: str, source_file: str, entries: list[BodyEntry], symbol_dict: dict[str, str]) -> None:
    comp = BodiesComponent(source_file=source_file, entries=entries, symbol_dict=symbol_dict)
    writer = FileComponentWriter(output_dir)
    writer.write_bodies(comp, include_original_bodies=True)


def _write_index(output_dir: str, files: dict, symbols: dict | None = None) -> None:
    index = {
        "version": "1.0",
        "root": output_dir,
        "files": files,
        "symbols": symbols or {},
    }
    path = os.path.join(output_dir, ".aleph.index.json")
    with open(path, "w") as f:
        json.dump(index, f)


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal built project with dict, struct, salience, bodies, and index."""
    d = str(tmp_path)

    # Symbols
    sym_hello = ProjectSymbolEntry(
        symbol_id="f_aaa111",
        name="hello",
        qualified_name="mod.hello",
        kind="f",
        scope="mod",
        file="src/main.py",
        signature_hash="sig1",
    )
    sym_world = ProjectSymbolEntry(
        symbol_id="f_bbb222",
        name="world",
        qualified_name="mod.world",
        kind="f",
        scope="mod",
        file="src/main.py",
        signature_hash="sig2",
    )
    sym_helper = ProjectSymbolEntry(
        symbol_id="f_ccc333",
        name="helper",
        qualified_name="util.helper",
        kind="f",
        scope="util",
        file="src/util.py",
        signature_hash="sig3",
    )
    sym_class = ProjectSymbolEntry(
        symbol_id="t_ddd444",
        name="MyClass",
        qualified_name="mod.MyClass",
        kind="t",
        scope="mod",
        file="src/main.py",
    )

    _write_project_dict(d, [sym_hello, sym_world, sym_helper, sym_class])

    # Cross-file refs: hello calls helper (cross-file)
    xrefs = [
        ProjectCrossRef(
            caller_id="f_aaa111", callee_id="f_ccc333",
            source_file="src/main.py", target_file="src/util.py",
        ),
    ]
    _write_project_struct(d, cross_refs=xrefs)

    # Salience
    salience_entries = [
        ProjectSalienceEntry(
            symbol_id="f_aaa111", qualified_name="mod.hello",
            file="src/main.py", score=0.8, local_fan_in=2,
            cross_file_fan_in=1, total_fan_in=3,
        ),
    ]
    _write_project_salience(d, salience_entries)

    # Bodies for src/main.py
    bodies = [
        BodyEntry(
            symbol_id=SymbolID("f", "aaa111"),
            level=BodyLevel.FULL,
            content="def f_aaa111():\n    return f_bbb222()",
            original_body="def hello():\n    return world()",
        ),
        BodyEntry(
            symbol_id=SymbolID("f", "bbb222"),
            level=BodyLevel.OMIT,
            content="",
            original_body="def world():\n    print('hi')",
        ),
    ]
    _write_bodies(d, "src/main.py", bodies, {
        "f_aaa111": "mod.hello",
        "f_bbb222": "mod.world",
    })

    # Index with within-file call edges
    _write_index(d, {
        "src/main.py": {
            "symbols": [
                {"id": "f_aaa111", "name": "hello", "qualified_name": "mod.hello", "kind": "f", "scope": "mod"},
                {"id": "f_bbb222", "name": "world", "qualified_name": "mod.world", "kind": "f", "scope": "mod"},
            ],
            "calls": [["f_aaa111", "f_bbb222"]],
        },
        "src/util.py": {
            "symbols": [
                {"id": "f_ccc333", "name": "helper", "qualified_name": "util.helper", "kind": "f", "scope": "util"},
            ],
            "calls": [],
        },
    })

    return d


# ── RESOLVE tests ──

class TestResolve:
    def test_resolve_existing_symbol(self, project_dir):
        engine = QueryEngine(project_dir)
        result = engine.resolve("f_aaa111")
        assert result is not None
        assert result.symbol_id == "f_aaa111"
        assert result.name == "hello"
        assert result.qualified_name == "mod.hello"
        assert result.kind == "f"
        assert result.scope == "mod"
        assert result.file == "src/main.py"
        assert result.signature_hash == "sig1"

    def test_resolve_nonexistent(self, project_dir):
        engine = QueryEngine(project_dir)
        assert engine.resolve("f_zzz999") is None

    def test_resolve_type_symbol(self, project_dir):
        engine = QueryEngine(project_dir)
        result = engine.resolve("t_ddd444")
        assert result is not None
        assert result.kind == "t"
        assert result.name == "MyClass"

    def test_resolve_to_dict(self, project_dir):
        engine = QueryEngine(project_dir)
        result = engine.resolve("f_aaa111")
        d = result.to_dict()
        assert d["symbol_id"] == "f_aaa111"
        assert d["qualified_name"] == "mod.hello"
        assert isinstance(d, dict)


# ── EXPAND tests ──

class TestExpand:
    def test_expand_full_body(self, project_dir):
        engine = QueryEngine(project_dir)
        body = engine.expand("f_aaa111")
        assert body is not None
        assert "hello" in body or "f_aaa111" in body

    def test_expand_omit_body(self, project_dir):
        engine = QueryEngine(project_dir)
        body = engine.expand("f_bbb222")
        # OMIT entries with original_body should still expand
        assert body is not None

    def test_expand_nonexistent(self, project_dir):
        engine = QueryEngine(project_dir)
        assert engine.expand("f_zzz999") is None

    def test_expand_no_bodies_file(self, project_dir):
        """Symbol exists in dict but no bodies file on disk."""
        engine = QueryEngine(project_dir)
        # f_ccc333 is in util.py, which has no bodies file written
        result = engine.expand("f_ccc333")
        assert result is None


# ── CALLERS tests ──

class TestCallers:
    def test_callers_cross_file(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_ccc333")
        assert len(callers) >= 1
        caller_ids = [c.caller_id for c in callers]
        assert "f_aaa111" in caller_ids

    def test_callers_within_file(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_bbb222")
        caller_ids = [c.caller_id for c in callers]
        assert "f_aaa111" in caller_ids

    def test_callers_no_callers(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_aaa111")
        # hello is not called by anything in our fixture
        assert isinstance(callers, list)

    def test_callers_nonexistent_symbol(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_zzz999")
        assert callers == []

    def test_caller_entry_has_name(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_ccc333")
        for c in callers:
            if c.caller_id == "f_aaa111":
                assert c.caller_name == "mod.hello"
                assert c.caller_file == "src/main.py"

    def test_caller_to_dict(self, project_dir):
        engine = QueryEngine(project_dir)
        callers = engine.callers("f_ccc333")
        for c in callers:
            d = c.to_dict()
            assert "caller_id" in d
            assert "caller_name" in d
            assert "target_id" in d


# ── CONTEXT tests ──

class TestContext:
    def test_context_returns_symbol_and_neighbors(self, project_dir):
        engine = QueryEngine(project_dir)
        ctx = engine.context("f_aaa111")
        assert ctx is not None
        assert ctx.symbol.symbol_id == "f_aaa111"
        assert ctx.symbol.qualified_name == "mod.hello"

    def test_context_has_callees(self, project_dir):
        engine = QueryEngine(project_dir)
        ctx = engine.context("f_aaa111")
        callee_ids = [c.symbol_id for c in ctx.callees]
        # hello calls world (within-file) and helper (cross-file)
        assert "f_bbb222" in callee_ids
        assert "f_ccc333" in callee_ids

    def test_context_has_callers(self, project_dir):
        engine = QueryEngine(project_dir)
        ctx = engine.context("f_ccc333")
        caller_ids = [c.caller_id for c in ctx.callers]
        assert "f_aaa111" in caller_ids

    def test_context_nonexistent(self, project_dir):
        engine = QueryEngine(project_dir)
        assert engine.context("f_zzz999") is None

    def test_context_to_dict(self, project_dir):
        engine = QueryEngine(project_dir)
        ctx = engine.context("f_aaa111")
        d = ctx.to_dict()
        assert "symbol" in d
        assert "callers" in d
        assert "callees" in d


# ── SEARCH tests ──

class TestSearch:
    def test_search_exact_name(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("hello")
        assert len(results) >= 1
        assert results[0].qualified_name == "mod.hello"
        assert results[0].score >= 0.9

    def test_search_qualified_name(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("mod.hello")
        assert len(results) >= 1
        assert results[0].qualified_name == "mod.hello"

    def test_search_substring(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("hel")
        matched_ids = [r.symbol_id for r in results]
        assert "f_aaa111" in matched_ids

    def test_search_case_insensitive(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("HELLO")
        assert len(results) >= 1
        assert results[0].symbol_id == "f_aaa111"

    def test_search_by_symbol_id(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("f_aaa111")
        assert len(results) >= 1
        assert results[0].score == 1.0

    def test_search_no_match(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("xyznonexistent")
        assert results == []

    def test_search_partial_class_name(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("MyClass")
        assert len(results) >= 1
        assert any(r.symbol_id == "t_ddd444" for r in results)

    def test_search_token_based(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("mod hello")
        assert len(results) >= 1
        # Should match mod.hello via token matching
        assert any(r.symbol_id == "f_aaa111" for r in results)

    def test_search_results_sorted_by_score(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("mod")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_to_dict(self, project_dir):
        engine = QueryEngine(project_dir)
        results = engine.search("hello")
        for r in results:
            d = r.to_dict()
            assert "symbol_id" in d
            assert "score" in d
            assert isinstance(d["score"], float)


# ── Engine initialization tests ──

class TestEngineInit:
    def test_missing_dict_file(self, tmp_path):
        # Write struct but no dict
        _write_project_struct(str(tmp_path))
        engine = QueryEngine(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            engine.resolve("f_aaa111")

    def test_missing_struct_file(self, tmp_path):
        _write_project_dict(str(tmp_path), [
            ProjectSymbolEntry(
                symbol_id="f_aaa111", name="hello",
                qualified_name="mod.hello", kind="f",
                scope="mod", file="src/main.py",
            ),
        ])
        engine = QueryEngine(str(tmp_path))
        # resolve works without struct
        result = engine.resolve("f_aaa111")
        assert result is not None
        # callers needs struct - should raise
        with pytest.raises(FileNotFoundError):
            engine.callers("f_aaa111")

    def test_lazy_loading(self, project_dir):
        """Components are loaded lazily, not at construction."""
        engine = QueryEngine(project_dir)
        assert engine._dict is None
        assert engine._struct is None
        engine.resolve("f_aaa111")
        assert engine._dict is not None
        assert engine._struct is None  # struct not needed for resolve

    def test_caching(self, project_dir):
        """Multiple queries reuse loaded components."""
        engine = QueryEngine(project_dir)
        engine.resolve("f_aaa111")
        dict1 = engine._dict
        engine.resolve("f_bbb222")
        assert engine._dict is dict1  # same object, not reloaded


# ── CLI integration tests ──

class TestQueryCLI:
    def test_cli_resolve_json(self, project_dir):
        """Test that the CLI can dispatch query RESOLVE."""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "RESOLVE", "f_aaa111",
             "-d", project_dir, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["symbol_id"] == "f_aaa111"
        assert data["qualified_name"] == "mod.hello"

    def test_cli_search_json(self, project_dir):
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "SEARCH", "hello",
             "-d", project_dir, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["query"] == "hello"
        assert len(data["results"]) >= 1

    def test_cli_callers_json(self, project_dir):
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "CALLERS", "f_ccc333",
             "-d", project_dir, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert any(c["caller_id"] == "f_aaa111" for c in data["callers"])

    def test_cli_context_json(self, project_dir):
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "CONTEXT", "f_aaa111",
             "-d", project_dir, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["symbol"]["symbol_id"] == "f_aaa111"
        assert "callers" in data
        assert "callees" in data

    def test_cli_unknown_command(self, project_dir):
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "UNKNOWN", "foo",
             "-d", project_dir],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_cli_resolve_not_found(self, project_dir):
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "RESOLVE", "f_zzz999",
             "-d", project_dir],
            capture_output=True, text=True,
        )
        assert result.returncode != 0

    def test_cli_case_insensitive_command(self, project_dir):
        """Commands should be case-insensitive."""
        result = subprocess.run(
            ["python", "-m", "aleph.cli", "query", "resolve", "f_aaa111",
             "-d", project_dir, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["symbol_id"] == "f_aaa111"
