"""Build cache for incremental recompilation (Phase 2.3).

Stores per-file pipeline results so unchanged files can be skipped on rebuild.
Change detection uses file mtime + size (fast) with content hash (accurate).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind, BodyLevel
from aleph.model.components import StructComponent
from aleph.util.hashing import byte_hash


CACHE_VERSION = "2.3"
CACHE_FILENAME = ".aleph.build_cache.json"


@dataclass
class FileStamp:
    """Fast change detection for a source file."""
    mtime_ns: int
    size: int
    content_hash: str

    def matches(self, other: FileStamp) -> bool:
        if self.mtime_ns == other.mtime_ns and self.size == other.size:
            return True
        return self.content_hash == other.content_hash

    def to_dict(self) -> dict:
        return {
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "content_hash": self.content_hash,
        }

    @staticmethod
    def from_dict(d: dict) -> FileStamp:
        return FileStamp(
            mtime_ns=d["mtime_ns"],
            size=d["size"],
            content_hash=d["content_hash"],
        )

    @staticmethod
    def from_file(path: str) -> FileStamp:
        stat = os.stat(path)
        with open(path, "rb") as f:
            content = f.read()
        return FileStamp(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            content_hash=byte_hash(content.decode("utf-8", errors="replace")),
        )


@dataclass
class CachedFileResult:
    """Serializable subset of a file's pipeline result needed by build_project."""
    stamp: FileStamp
    language: str
    semantic_hash: str
    symbols_extracted: int
    call_edges_count: int
    original_tokens: int
    compressed_tokens: int
    token_reduction_percent: float
    symbols_data: list[dict]  # serialized symbol data
    call_edges: list[tuple[str, str]]  # (caller_id, callee_id)
    call_edge_metadata: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stamp": self.stamp.to_dict(),
            "language": self.language,
            "semantic_hash": self.semantic_hash,
            "symbols_extracted": self.symbols_extracted,
            "call_edges_count": self.call_edges_count,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "token_reduction_percent": self.token_reduction_percent,
            "symbols_data": self.symbols_data,
            "call_edges": self.call_edges,
            "call_edge_metadata": self.call_edge_metadata,
        }

    @staticmethod
    def from_dict(d: dict) -> CachedFileResult:
        return CachedFileResult(
            stamp=FileStamp.from_dict(d["stamp"]),
            language=d["language"],
            semantic_hash=d["semantic_hash"],
            symbols_extracted=d["symbols_extracted"],
            call_edges_count=d["call_edges_count"],
            original_tokens=d["original_tokens"],
            compressed_tokens=d["compressed_tokens"],
            token_reduction_percent=d["token_reduction_percent"],
            symbols_data=d["symbols_data"],
            call_edges=[tuple(e) for e in d["call_edges"]],
            call_edge_metadata=d.get("call_edge_metadata", []),
        )


def serialize_symbol(sym: Symbol) -> dict:
    """Serialize a Symbol to a dict for cache storage."""
    return {
        "id": str(sym.id),
        "name": sym.raw.name,
        "qualified_name": sym.raw.qualified_name,
        "kind": sym.raw.kind.value,
        "scope": sym.raw.scope,
        "language": sym.raw.language,
        "source_file": sym.raw.source_file,
        "signature_text": sym.raw.signature_text,
        "body_text": sym.raw.body_text,
        "span": [sym.raw.span.start_line, sym.raw.span.start_col,
                 sym.raw.span.end_line, sym.raw.span.end_col],
        "salience": sym.salience,
        "body_level": sym.body_level.value,
        "calls": [str(c) for c in sym.calls],
        "called_by": [str(c) for c in sym.called_by],
        "children": [str(c) for c in sym.children],
        "parent": str(sym.parent) if sym.parent else None,
    }


def deserialize_symbol(d: dict) -> Symbol:
    """Reconstruct a Symbol from cached dict."""
    span_data = d["span"]
    raw = RawSymbol(
        name=d["name"],
        qualified_name=d["qualified_name"],
        kind=SymbolKind(d["kind"]),
        scope=d["scope"],
        span=Span(span_data[0], span_data[1], span_data[2], span_data[3]),
        language=d["language"],
        source_file=d.get("source_file", ""),
        body_text=d.get("body_text", ""),
        signature_text=d.get("signature_text", ""),
    )
    sym = Symbol(
        id=SymbolID.from_string(d["id"]),
        raw=raw,
        salience=d.get("salience", 0.0),
        body_level=BodyLevel(d.get("body_level", "OMIT")),
        calls=[SymbolID.from_string(c) for c in d.get("calls", [])],
        called_by=[SymbolID.from_string(c) for c in d.get("called_by", [])],
        children=[SymbolID.from_string(c) for c in d.get("children", [])],
        parent=SymbolID.from_string(d["parent"]) if d.get("parent") else None,
    )
    return sym


def cache_from_pipeline_result(source_file: str, result: dict) -> CachedFileResult:
    """Extract cacheable data from a pipeline result dict."""
    stamp = FileStamp.from_file(source_file)
    symbols_data = [serialize_symbol(sym) for sym in result["symbols"]]
    return CachedFileResult(
        stamp=stamp,
        language=result["language"],
        semantic_hash=result["semantic_hash"],
        symbols_extracted=result["symbols_extracted"],
        call_edges_count=result["call_edges"],
        original_tokens=result["original_tokens"],
        compressed_tokens=result["compressed_tokens"],
        token_reduction_percent=result["token_reduction_percent"],
        symbols_data=symbols_data,
        call_edges=result["struct_component"].call_edges,
        call_edge_metadata=result.get("call_edge_metadata", []),
    )


def reconstruct_build_result(cached: CachedFileResult, source_file: str) -> dict:
    """Reconstruct the minimal pipeline result dict needed by build_project."""
    symbols = [deserialize_symbol(d) for d in cached.symbols_data]
    struct_component = StructComponent(
        source_file=source_file,
        call_edges=cached.call_edges,
        call_edge_metadata=cached.call_edge_metadata,
    )
    return {
        "source_file": source_file,
        "language": cached.language,
        "semantic_hash": cached.semantic_hash,
        "symbols_extracted": cached.symbols_extracted,
        "call_edges": cached.call_edges_count,
        "original_tokens": cached.original_tokens,
        "compressed_tokens": cached.compressed_tokens,
        "token_reduction_percent": cached.token_reduction_percent,
        "symbols": symbols,
        "struct_component": struct_component,
        "call_edge_metadata": cached.call_edge_metadata,
    }


@dataclass
class BuildCache:
    """Persistent build cache for incremental recompilation."""
    version: str = CACHE_VERSION
    root: str = ""
    files: dict[str, CachedFileResult] = field(default_factory=dict)

    def get_cached(self, source_file: str) -> CachedFileResult | None:
        return self.files.get(source_file)

    def is_fresh(self, source_file: str) -> bool:
        """Check if cached result is still valid for the given file."""
        cached = self.files.get(source_file)
        if cached is None:
            return False
        if not os.path.isfile(source_file):
            return False
        current_stamp = FileStamp.from_file(source_file)
        return cached.stamp.matches(current_stamp)

    def update(self, source_file: str, result: dict) -> None:
        """Store a pipeline result in the cache."""
        self.files[source_file] = cache_from_pipeline_result(source_file, result)

    def remove_stale(self, current_files: set[str]) -> list[str]:
        """Remove entries for files that no longer exist. Returns removed paths."""
        stale = [f for f in self.files if f not in current_files]
        for f in stale:
            del self.files[f]
        return stale

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "root": self.root,
            "files": {
                path: cached.to_dict()
                for path, cached in self.files.items()
            },
        }

    @staticmethod
    def from_dict(d: dict) -> BuildCache:
        if d.get("version") != CACHE_VERSION:
            return BuildCache()
        cache = BuildCache(
            version=d["version"],
            root=d.get("root", ""),
        )
        for path, file_data in d.get("files", {}).items():
            try:
                cache.files[path] = CachedFileResult.from_dict(file_data)
            except (KeyError, TypeError, ValueError):
                continue
        return cache


def load_build_cache(path: str) -> BuildCache:
    """Load build cache from disk. Returns empty cache if not found or invalid."""
    if not os.path.isfile(path):
        return BuildCache()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return BuildCache.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return BuildCache()


def save_build_cache(path: str, cache: BuildCache) -> None:
    """Save build cache to disk (compact JSON for speed)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache.to_dict(), f, separators=(",", ":"))
        f.write("\n")
