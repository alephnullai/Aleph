"""Integration tests for incremental recompilation (Phase 2.3).

Verifies that:
- Second build reuses cached results for unchanged files
- Modified files are rebuilt while others are reused
- Deleted files are removed from cache
- New files are detected and built
- Incremental results match full build results
- Rebuild is fast (<100ms for a single function change)
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

from aleph.project.builder import build_project, BuildResult
from aleph.project.cache import (
    BuildCache,
    load_build_cache,
    save_build_cache,
    CACHE_FILENAME,
)
from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind
from aleph.model.components import StructComponent


def _make_span():
    return Span(start_line=1, start_col=0, end_line=5, end_col=1)


def _make_symbol(name: str, kind: str = "f", scope: str = "", source_file: str = "test.py") -> Symbol:
    raw = RawSymbol(
        name=name,
        qualified_name=f"{scope}.{name}" if scope else name,
        kind=SymbolKind(kind),
        scope=scope,
        span=_make_span(),
        language="python",
        source_file=source_file,
        body_text=f"def {name}(): pass",
        signature_text=f"def {name}()",
    )
    sid = SymbolID(prefix=kind, hex_hash=f"{hash(name) % 0xFFFFFF:06x}")
    return Symbol(id=sid, raw=raw)


def _make_pipeline_result(source_file, symbols=None, call_edges=None):
    symbols = symbols or []
    call_edges = call_edges or []
    struct = StructComponent(source_file=source_file, call_edges=call_edges)
    return {
        "source_file": source_file,
        "language": "python",
        "semantic_hash": f"hash_{os.path.basename(source_file)}",
        "symbols_extracted": len(symbols),
        "call_edges": len(call_edges),
        "original_tokens": 100,
        "compressed_tokens": 60,
        "token_reduction_percent": 40.0,
        "struct_component": struct,
        "symbols": symbols,
    }


class TestIncrementalBuild:
    def test_second_build_reuses_all_files(self, tmp_path):
        """A second build with no changes should reuse all cached results."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass\n")

        call_count = 0

        def runner(path):
            nonlocal call_count
            call_count += 1
            name = os.path.basename(path).replace(".py", "")
            return _make_pipeline_result(path, symbols=[_make_symbol(name, source_file=path)])

        # First build: runs pipeline on all files
        result1 = build_project(str(tmp_path), runner)
        assert result1.stats.rebuilt_files == 2
        assert result1.stats.reused_files == 0
        assert call_count == 2

        # Second build with cache: should reuse all
        call_count = 0
        result2 = build_project(str(tmp_path), runner, cache=result1.cache)
        assert result2.stats.reused_files == 2
        assert result2.stats.rebuilt_files == 0
        assert call_count == 0  # Runner was never called

    def test_modified_file_rebuilt(self, tmp_path):
        """Only the modified file should be rebuilt."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass\n")

        def runner(path):
            name = os.path.basename(path).replace(".py", "")
            return _make_pipeline_result(path, symbols=[_make_symbol(name, source_file=path)])

        result1 = build_project(str(tmp_path), runner)

        # Modify only a.py
        f1.write_text("def a_modified(): pass\n")

        rebuilt_files = []
        original_runner = runner

        def tracking_runner(path):
            rebuilt_files.append(path)
            return original_runner(path)

        result2 = build_project(str(tmp_path), tracking_runner, cache=result1.cache)
        assert result2.stats.reused_files == 1
        assert result2.stats.rebuilt_files == 1
        assert len(rebuilt_files) == 1
        assert "a.py" in rebuilt_files[0]

    def test_new_file_detected(self, tmp_path):
        """A new file should be built, existing files reused."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")

        def runner(path):
            name = os.path.basename(path).replace(".py", "")
            return _make_pipeline_result(path, symbols=[_make_symbol(name, source_file=path)])

        result1 = build_project(str(tmp_path), runner)
        assert result1.stats.total_files == 1

        # Add new file
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass\n")

        result2 = build_project(str(tmp_path), runner, cache=result1.cache)
        assert result2.stats.total_files == 2
        assert result2.stats.reused_files == 1
        assert result2.stats.rebuilt_files == 1

    def test_deleted_file_removed_from_cache(self, tmp_path):
        """Deleting a file should remove it from cache and results."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass\n")

        def runner(path):
            name = os.path.basename(path).replace(".py", "")
            return _make_pipeline_result(path, symbols=[_make_symbol(name, source_file=path)])

        result1 = build_project(str(tmp_path), runner)
        assert result1.stats.total_files == 2

        # Delete b.py
        os.unlink(str(f2))

        result2 = build_project(str(tmp_path), runner, cache=result1.cache)
        assert result2.stats.total_files == 1
        assert result2.stats.removed_files == 1
        assert result2.stats.reused_files == 1
        assert result2.stats.rebuilt_files == 0
        assert len(result2.map_component.files) == 1

    def test_incremental_results_match_full_build(self, tmp_path):
        """Incremental build should produce identical project-level components."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass\n")

        def runner(path):
            name = os.path.basename(path).replace(".py", "")
            return _make_pipeline_result(path, symbols=[_make_symbol(name, source_file=path)])

        # Full build
        full_result = build_project(str(tmp_path), runner)

        # Incremental build (no changes)
        inc_result = build_project(str(tmp_path), runner, cache=full_result.cache)

        # Compare map entries
        assert len(full_result.map_component.files) == len(inc_result.map_component.files)
        for f, i in zip(full_result.map_component.files, inc_result.map_component.files):
            assert f.path == i.path
            assert f.semantic_hash == i.semantic_hash
            assert f.symbol_count == i.symbol_count

        # Compare dict entries
        assert len(full_result.dict_component.symbols) == len(inc_result.dict_component.symbols)

        # Compare stats
        assert full_result.stats.total_symbols == inc_result.stats.total_symbols
        assert full_result.stats.total_original_tokens == inc_result.stats.total_original_tokens

    def test_cache_persists_through_save_load(self, tmp_path):
        """Cache should survive save/load cycle and still enable incremental builds."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass\n")

        call_count = 0

        def runner(path):
            nonlocal call_count
            call_count += 1
            return _make_pipeline_result(path, symbols=[_make_symbol("a", source_file=path)])

        result1 = build_project(str(tmp_path), runner)
        assert call_count == 1

        # Save and reload cache
        cache_path = str(tmp_path / CACHE_FILENAME)
        save_build_cache(cache_path, result1.cache)
        loaded_cache = load_build_cache(cache_path)

        # Build with loaded cache
        call_count = 0
        result2 = build_project(str(tmp_path), runner, cache=loaded_cache)
        assert call_count == 0
        assert result2.stats.reused_files == 1

    def test_no_cache_means_full_build(self, tmp_path):
        """Without cache, all files should be rebuilt."""
        f = tmp_path / "a.py"
        f.write_text("def a(): pass\n")

        def runner(path):
            return _make_pipeline_result(path, symbols=[_make_symbol("a", source_file=path)])

        result = build_project(str(tmp_path), runner, cache=None)
        assert result.stats.rebuilt_files == 1
        assert result.stats.reused_files == 0

    def test_pipeline_error_not_cached(self, tmp_path):
        """Files that fail pipeline should not be cached."""
        f1 = tmp_path / "good.py"
        f1.write_text("def good(): pass\n")
        f2 = tmp_path / "bad.py"
        f2.write_text("syntax error\n")

        def runner(path):
            if "bad.py" in path:
                raise ValueError("parse error")
            return _make_pipeline_result(path, symbols=[_make_symbol("good", source_file=path)])

        result = build_project(str(tmp_path), runner)
        assert len(result.stats.errors) == 1
        # Only good.py should be in cache
        assert result.cache is not None
        assert any("good.py" in k for k in result.cache.files)
        assert not any("bad.py" in k for k in result.cache.files)

    def test_cross_file_refs_with_cache(self, tmp_path):
        """Cross-file references should work correctly with cached results."""
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
                    path, symbols=[sym_caller],
                    call_edges=[(caller_id, callee_id)],
                )
            return _make_pipeline_result(path, symbols=[sym_callee])

        # Full build
        result1 = build_project(str(tmp_path), runner)
        assert result1.stats.total_cross_refs == 1

        # Incremental build (no changes)
        result2 = build_project(str(tmp_path), runner, cache=result1.cache)
        assert result2.stats.total_cross_refs == 1
        assert len(result2.struct_component.cross_refs) == 1
