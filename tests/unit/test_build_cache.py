"""Tests for build cache serialization and incremental recompilation (Phase 2.3)."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

import pytest

from aleph.project.cache import (
    FileStamp,
    CachedFileResult,
    BuildCache,
    serialize_symbol,
    deserialize_symbol,
    cache_from_pipeline_result,
    reconstruct_build_result,
    load_build_cache,
    save_build_cache,
    CACHE_VERSION,
    CACHE_FILENAME,
)
from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind, BodyLevel
from aleph.model.components import StructComponent


def _make_span():
    return Span(start_line=1, start_col=0, end_line=5, end_col=1)


def _make_symbol(name: str, kind: str = "f", scope: str = "") -> Symbol:
    raw = RawSymbol(
        name=name,
        qualified_name=f"{scope}.{name}" if scope else name,
        kind=SymbolKind(kind),
        scope=scope,
        span=_make_span(),
        language="python",
        source_file="test.py",
        body_text=f"def {name}(): pass",
        signature_text=f"def {name}()",
    )
    sid = SymbolID(prefix=kind, hex_hash=f"{hash(name) % 0xFFFFFF:06x}")
    return Symbol(id=sid, raw=raw, salience=0.5, body_level=BodyLevel.FULL)


def _make_pipeline_result(source_file: str, symbols=None, call_edges=None):
    symbols = symbols or [_make_symbol("foo")]
    call_edges = call_edges or []
    struct = StructComponent(source_file=source_file, call_edges=call_edges)
    return {
        "source_file": source_file,
        "language": "python",
        "semantic_hash": "abc123",
        "symbols_extracted": len(symbols),
        "call_edges": len(call_edges),
        "original_tokens": 100,
        "compressed_tokens": 60,
        "token_reduction_percent": 40.0,
        "struct_component": struct,
        "symbols": symbols,
    }


class TestFileStamp:
    def test_from_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        stamp = FileStamp.from_file(str(f))
        assert stamp.size > 0
        assert stamp.mtime_ns > 0
        assert len(stamp.content_hash) == 64  # sha256 hex

    def test_matches_same_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello")
        s1 = FileStamp.from_file(str(f))
        s2 = FileStamp.from_file(str(f))
        assert s1.matches(s2)

    def test_no_match_after_change(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello")
        s1 = FileStamp.from_file(str(f))
        f.write_text("world")
        s2 = FileStamp.from_file(str(f))
        assert not s1.matches(s2)

    def test_matches_via_content_hash(self):
        """If mtime differs but content hash matches, should still match."""
        s1 = FileStamp(mtime_ns=100, size=10, content_hash="abc")
        s2 = FileStamp(mtime_ns=200, size=10, content_hash="abc")
        assert s1.matches(s2)

    def test_roundtrip_dict(self):
        s = FileStamp(mtime_ns=12345, size=100, content_hash="abc123")
        d = s.to_dict()
        s2 = FileStamp.from_dict(d)
        assert s2.mtime_ns == s.mtime_ns
        assert s2.size == s.size
        assert s2.content_hash == s.content_hash


class TestSymbolSerialization:
    def test_roundtrip(self):
        sym = _make_symbol("my_func", scope="my_module")
        sym.calls = [SymbolID("f", "aaaaaa")]
        sym.called_by = [SymbolID("f", "bbbbbb")]
        sym.parent = SymbolID("m", "cccccc")
        sym.children = [SymbolID("v", "dddddd")]

        d = serialize_symbol(sym)
        restored = deserialize_symbol(d)

        assert str(restored.id) == str(sym.id)
        assert restored.raw.name == sym.raw.name
        assert restored.raw.qualified_name == sym.raw.qualified_name
        assert restored.raw.kind == sym.raw.kind
        assert restored.raw.scope == sym.raw.scope
        assert restored.raw.language == sym.raw.language
        assert restored.raw.body_text == sym.raw.body_text
        assert restored.raw.signature_text == sym.raw.signature_text
        assert restored.salience == sym.salience
        assert restored.body_level == sym.body_level
        assert len(restored.calls) == 1
        assert str(restored.calls[0]) == "f_aaaaaa"
        assert len(restored.called_by) == 1
        assert str(restored.parent) == "m_cccccc"
        assert len(restored.children) == 1

    def test_roundtrip_minimal(self):
        sym = _make_symbol("bare")
        d = serialize_symbol(sym)
        restored = deserialize_symbol(d)
        assert restored.raw.name == "bare"
        assert restored.parent is None
        assert restored.calls == []

    def test_span_preserved(self):
        sym = _make_symbol("spanned")
        d = serialize_symbol(sym)
        restored = deserialize_symbol(d)
        assert restored.raw.span.start_line == 1
        assert restored.raw.span.end_line == 5


class TestCachedFileResult:
    def test_roundtrip_dict(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))
        cached = cache_from_pipeline_result(str(f), result)

        d = cached.to_dict()
        restored = CachedFileResult.from_dict(d)

        assert restored.language == "python"
        assert restored.semantic_hash == "abc123"
        assert restored.symbols_extracted == 1
        assert restored.original_tokens == 100
        assert len(restored.symbols_data) == 1
        assert restored.symbols_data[0]["name"] == "foo"

    def test_reconstruct_build_result(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))
        cached = cache_from_pipeline_result(str(f), result)

        rebuilt = reconstruct_build_result(cached, str(f))
        assert rebuilt["language"] == "python"
        assert rebuilt["semantic_hash"] == "abc123"
        assert rebuilt["symbols_extracted"] == 1
        assert len(rebuilt["symbols"]) == 1
        assert rebuilt["symbols"][0].raw.name == "foo"
        assert isinstance(rebuilt["struct_component"], StructComponent)


class TestBuildCache:
    def test_empty_cache(self):
        cache = BuildCache()
        assert not cache.is_fresh("/nonexistent")
        assert cache.get_cached("/nonexistent") is None

    def test_update_and_check_fresh(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))

        cache = BuildCache()
        cache.update(str(f), result)

        assert cache.is_fresh(str(f))
        assert cache.get_cached(str(f)) is not None

    def test_stale_after_modification(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))

        cache = BuildCache()
        cache.update(str(f), result)

        # Modify the file
        f.write_text("def bar(): pass\n")

        assert not cache.is_fresh(str(f))

    def test_remove_stale(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2\n")

        cache = BuildCache()
        cache.update(str(f1), _make_pipeline_result(str(f1)))
        cache.update(str(f2), _make_pipeline_result(str(f2)))

        # Only f1 is current
        removed = cache.remove_stale({str(f1)})
        assert len(removed) == 1
        assert str(f2) in removed[0]
        assert str(f1) in cache.files

    def test_roundtrip_dict(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))

        cache = BuildCache(root=str(tmp_path))
        cache.update(str(f), result)

        d = cache.to_dict()
        restored = BuildCache.from_dict(d)

        assert restored.version == CACHE_VERSION
        assert restored.root == str(tmp_path)
        assert str(f) in restored.files

    def test_version_mismatch_returns_empty(self):
        d = {"version": "0.0", "root": "/tmp", "files": {}}
        cache = BuildCache.from_dict(d)
        assert len(cache.files) == 0

    def test_corrupt_file_entry_skipped(self, tmp_path):
        d = {
            "version": CACHE_VERSION,
            "root": str(tmp_path),
            "files": {
                "/tmp/good.py": {
                    "stamp": {"mtime_ns": 1, "size": 1, "content_hash": "abc"},
                    "language": "python",
                    "semantic_hash": "def",
                    "symbols_extracted": 0,
                    "call_edges_count": 0,
                    "original_tokens": 0,
                    "compressed_tokens": 0,
                    "token_reduction_percent": 0.0,
                    "symbols_data": [],
                    "call_edges": [],
                },
                "/tmp/bad.py": {"corrupt": True},
            },
        }
        cache = BuildCache.from_dict(d)
        assert "/tmp/good.py" in cache.files
        assert "/tmp/bad.py" not in cache.files


class TestBuildCachePersistence:
    def test_save_and_load(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def foo(): pass\n")
        result = _make_pipeline_result(str(f))

        cache = BuildCache(root=str(tmp_path))
        cache.update(str(f), result)

        cache_path = str(tmp_path / CACHE_FILENAME)
        save_build_cache(cache_path, cache)
        assert os.path.isfile(cache_path)

        loaded = load_build_cache(cache_path)
        assert loaded.root == str(tmp_path)
        assert str(f) in loaded.files
        assert loaded.files[str(f)].language == "python"

    def test_load_nonexistent_returns_empty(self, tmp_path):
        cache = load_build_cache(str(tmp_path / "nonexistent.json"))
        assert len(cache.files) == 0

    def test_load_corrupt_returns_empty(self, tmp_path):
        cache_path = tmp_path / "corrupt.json"
        cache_path.write_text("not json{{{")
        cache = load_build_cache(str(cache_path))
        assert len(cache.files) == 0

    def test_cache_file_is_valid_json(self, tmp_path):
        cache = BuildCache(root=str(tmp_path))
        cache_path = str(tmp_path / CACHE_FILENAME)
        save_build_cache(cache_path, cache)

        with open(cache_path) as fh:
            data = json.load(fh)
        assert data["version"] == CACHE_VERSION
